import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from ultralytics import YOLO
from PIL import Image, ImageDraw, ImageFont

class AccessAuditor:
    def __init__(self, model_path="best.pt"):
        print(f"🧠 Loading model: {model_path}...")
        try:
            self.model = YOLO(model_path)
        except Exception as e:
            print(f"❌ Error loading model: {e}")
            raise e

    def audit_url(self, url, output_folder="audit_results", progress_callback=None, devices=None):
        """Audit a URL across multiple device viewports. Returns a dict mapping device -> {findings, img_path}.
        By default audits ['desktop','ipad','mobile'].
        """
        if devices is None:
            devices = ['desktop', 'ipad', 'mobile']

        results = {}
        total = len(devices)
        for idx, device in enumerate(devices):
            # Wrap the progress_callback so per-device progress [0..1] maps to overall progress
            def make_device_progress(idx_local):
                def device_progress(message, pct):
                    # pct expected in [0..1]; map to overall fraction
                    overall = (idx_local + pct) / total
                    if progress_callback:
                        progress_callback(f"[{device.upper()}] {message}", overall)
                return device_progress

            device_progress_cb = make_device_progress(idx)
            findings, annotated_path = self._audit_for_device(url, device, output_folder=output_folder, progress_callback=device_progress_cb)
            # After device completes, ensure progress is set to the device boundary
            if progress_callback:
                progress_callback(f"Completed {device} audit", float(idx + 1) / total)

            results[device] = {
                'findings': findings,
                'annotated_path': annotated_path,
                'screenshot': os.path.join(output_folder, f"screenshot_{device}.png")
            }
        return results

    def _audit_for_device(self, url, device, output_folder="audit_results", progress_callback=None):
        """Audit a single device viewport (internal helper). Returns (findings, annotated_path)."""
        if not os.path.exists(output_folder): 
            os.makedirs(output_folder)

        def update_progress(message, percent):
            msg = f"[{device.upper()}] {message}"
            print(msg)
            if progress_callback:
                progress_callback(msg, percent)

        update_progress(f"🌍 Navigating to: {url}", 0.05)
        findings = []
        screenshot_path = f"{output_folder}/screenshot_{device}.png"
        annotated_path = f"{output_folder}/annotated_{device}.png"

        # Map device to initial window size (width,height)
        device_sizes = {
            'desktop': (1280, 800),
            'ipad': (768, 1024),
            'mobile': (390, 844),
        }
        initial_size = device_sizes.get(device, (1280, 800))

        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"--window-size={initial_size[0]},{initial_size[1]}")

        # For mobile/iPad, enable Chrome DevTools mobile emulation for accurate device
        # metrics (viewport, devicePixelRatio) and touch support. This provides closer
        # fidelity than setting user-agent alone.
        if device in ('mobile', 'ipad'):
            # Choose common device metrics and user-agent for iPhone 13 Pro / iPad Pro
            if device == 'mobile':
                ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
                device_metrics = { 'width': initial_size[0], 'height': initial_size[1], 'pixelRatio': 3 }
            else:
                ua = "Mozilla/5.0 (iPad; CPU OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
                device_metrics = { 'width': initial_size[0], 'height': initial_size[1], 'pixelRatio': 2 }

            mobile_emulation = {
                'deviceMetrics': device_metrics,
                'userAgent': ua
            }
            chrome_options.add_experimental_option('mobileEmulation', mobile_emulation)
        
        # Initialize driver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        try:
            driver.get(url)
            
            # Wait for page to load and network to be idle (handles lazy loading)
            time.sleep(2)
            
            # Wait for network idle - no pending requests for 1 second
            print("⏳ Waiting for lazy-loaded content (network idle)...")
            driver.execute_script("""
                return new Promise((resolve) => {
                    let activeRequests = 0;
                    let idleTimer = null;
                    
                    // Monitor fetch/XHR requests
                    const origFetch = window.fetch;
                    window.fetch = function(...args) {
                        activeRequests++;
                        return origFetch.apply(this, args).finally(() => {
                            activeRequests--;
                            resetIdleTimer();
                        });
                    };
                    
                    const origOpen = XMLHttpRequest.prototype.open;
                    XMLHttpRequest.prototype.open = function(...args) {
                        activeRequests++;
                        this.addEventListener('loadend', () => {
                            activeRequests--;
                            resetIdleTimer();
                        });
                        return origOpen.apply(this, args);
                    };
                    
                    function resetIdleTimer() {
                        clearTimeout(idleTimer);
                        if (activeRequests === 0) {
                            idleTimer = setTimeout(() => resolve('idle'), 1000);
                        }
                    }
                    
                    // Start idle check
                    resetIdleTimer();
                    
                    // Max wait 10 seconds
                    setTimeout(() => resolve('timeout'), 10000);
                });
            """)
            print("✅ Network idle or timeout reached")
            
            # Trigger lazy loading by scrolling through the page
            print("📜 Scrolling through page to trigger lazy-loaded images...")
            total_height = driver.execute_script("return document.body.scrollHeight")
            viewport_height = driver.execute_script("return window.innerHeight")
            
            # Scroll in steps to trigger lazy loading
            scroll_position = 0
            while scroll_position < total_height:
                driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                time.sleep(0.3)  # Wait for images to start loading
                scroll_position += viewport_height
            
            # Scroll back to top
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)  # Wait for any final images to finish
            print("✅ Lazy loading triggered")
            
            # Get full page dimensions
            total_height = driver.execute_script("return document.body.scrollHeight")
            viewport_height = driver.execute_script("return window.innerHeight")
            viewport_width = driver.execute_script("return window.innerWidth")
            
            update_progress(f"📏 Page height: {total_height}px, Viewport: {viewport_width}x{viewport_height}px", 0.10)
            
            # Capture single full-page screenshot using Chrome's built-in functionality
            # This captures everything in one go - no stitching needed!
            driver.execute_script("window.scrollTo(0, 0)")
            time.sleep(0.5)
            
            # Get full page screenshot (Chrome can do this natively)
            # Set window size to full page height temporarily
            original_size = driver.get_window_size()
            driver.set_window_size(viewport_width, total_height)
            time.sleep(1)  # Let page re-render
            
            driver.save_screenshot(screenshot_path)
            update_progress(f"✅ Full-page screenshot captured: {screenshot_path}", 0.15)
            
            # DON'T restore window size yet - we need it at full-page size for DOM queries
            # (so DOM coordinates match YOLO coordinates from the full-page screenshot)
            
            # Now segment the full screenshot and run model on each segment
            full_img = Image.open(screenshot_path)
            actual_width, actual_height = full_img.size
            update_progress(f"📐 Actual screenshot size: {actual_width}x{actual_height}px", 0.20)
            
            # Segment into viewport-sized chunks with OVERLAP to avoid splitting elements
            segment_height = viewport_height
            overlap = 100  # 100px overlap to catch elements at boundaries
            all_detections = []
            segment_num = 0
            
            # Calculate total segments for progress
            total_segments = 0
            y_test = 0
            while y_test < actual_height:
                total_segments += 1
                y_test += segment_height - overlap
                if y_test + overlap >= actual_height:
                    break
            
            y_offset = 0
            while y_offset < actual_height:
                segment_end = min(y_offset + segment_height, actual_height)
                
                # Crop segment
                segment = full_img.crop((0, y_offset, actual_width, segment_end))
                
                # Save temporary segment
                temp_segment_path = f"{output_folder}/temp_segment_{segment_num}.png"
                segment.save(temp_segment_path)
                
                # Run YOLO on this segment
                progress_pct = 0.20 + (segment_num / total_segments) * 0.40  # 20-60% for scanning
                update_progress(f"🔍 Scanning segment {segment_num + 1}/{total_segments} (y={y_offset}-{segment_end}px)...", progress_pct)
                segment_results = self.model.predict(temp_segment_path, conf=0.25, verbose=False)
                
                # Store detections with adjusted Y coordinates
                for box in segment_results[0].boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    label = self.model.names[cls_id]
                    
                    # Adjust Y coordinates to full-page coordinates
                    adjusted_y1 = y1 + y_offset
                    adjusted_y2 = y2 + y_offset
                    
                    # Skip detections in overlap regions if not the first segment
                    # (avoid duplicates from overlapping areas)
                    if segment_num > 0 and y1 < overlap:
                        continue  # This element was already captured in previous segment
                    
                    all_detections.append({
                        'x1': x1,
                        'y1': adjusted_y1,
                        'x2': x2,
                        'y2': adjusted_y2,
                        'conf': conf,
                        'cls_id': cls_id,
                        'label': label,
                        'segment': segment_num
                    })
                
                # Clean up temp segment
                os.remove(temp_segment_path)
                segment_num += 1
                
                # Move to next segment with overlap
                y_offset += segment_height - overlap
                if y_offset + overlap >= actual_height and segment_end == actual_height:
                    break  # Don't create unnecessary tiny segment at end
            
            update_progress(f"🎯 Found {len(all_detections)} detections, removing duplicates...", 0.60)
            
            # Save image with ALL detections BEFORE deduplication for debugging
            debug_img = Image.open(screenshot_path).copy()
            draw = ImageDraw.Draw(debug_img)
            try:
                font = ImageFont.truetype("arial.ttf", 14)
            except:
                font = ImageFont.load_default()
            
            for i, det in enumerate(all_detections, 1):
                # Draw bbox
                draw.rectangle([det['x1'], det['y1'], det['x2'], det['y2']], 
                              outline='red', width=2)
                # Label with number
                draw.text((det['x1'], det['y1']-15), f"#{i} {det['label']}", 
                         fill='red', font=font)
            
            debug_img.save(os.path.join(output_folder, f"all_detections_before_dedup_{device}.png"))
            print(f"💾 Saved all {len(all_detections)} raw detections to all_detections_before_dedup_{device}.png")
            
            # Deduplicate detections using IoU (Intersection over Union)
            # Sort by area (smallest first) - tighter boxes are more accurate than loose ones
            all_detections.sort(key=lambda d: (d['x2'] - d['x1']) * (d['y2'] - d['y1']))
            
            print(f"\n🔍 Deduplication: Starting with {len(all_detections)} detections")
            deduplicated = []
            
            for detection in all_detections:
                is_duplicate = False
                
                # Check if this detection significantly overlaps with any already-kept detection
                for kept in deduplicated:
                    x1a, y1a, x2a, y2a = detection['x1'], detection['y1'], detection['x2'], detection['y2']
                    x1b, y1b, x2b, y2b = kept['x1'], kept['y1'], kept['x2'], kept['y2']
                    
                    # Calculate intersection
                    inter_x1 = max(x1a, x1b)
                    inter_y1 = max(y1a, y1b)
                    inter_x2 = min(x2a, x2b)
                    inter_y2 = min(y2a, y2b)
                    
                    if inter_x2 > inter_x1 and inter_y2 > inter_y1:
                        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
                        area_a = (x2a - x1a) * (y2a - y1a)
                        area_b = (x2b - x1b) * (y2b - y1b)
                        
                        # TWO-WAY overlap check: both boxes must overlap >80% with each other
                        # This prevents child elements (button in input) from being deduplicated
                        overlap_a = inter_area / area_a  # How much of box A overlaps with B
                        overlap_b = inter_area / area_b  # How much of box B overlaps with A
                        
                        # Standard two-way check (works for all cases)
                        if overlap_a > 0.8 and overlap_b > 0.8:
                            is_duplicate = True
                            print(f"  🔄 Skipping duplicate {detection['label']} at ({detection['x1']},{detection['y1']})-({detection['x2']},{detection['y2']}) [conf={detection['conf']:.2f}] - overlaps {overlap_a*100:.0f}%/{overlap_b*100:.0f}% with {kept['label']}")
                            break
                        
                        # SAME CLASS special case: If same element type with bbox variance,
                        # use one-way check (smaller box >80% inside larger box)
                        # Use EITHER center proximity OR size similarity to confirm it's the same element
                        if detection['label'] == kept['label']:
                            smaller_area = min(area_a, area_b)
                            larger_area = max(area_a, area_b)
                            area_ratio = larger_area / smaller_area
                            
                            # Calculate center distance
                            center_a_x = (x1a + x2a) / 2
                            center_a_y = (y1a + y2a) / 2
                            center_b_x = (x1b + x2b) / 2
                            center_b_y = (y1b + y2b) / 2
                            center_dist = ((center_a_x - center_b_x)**2 + (center_a_y - center_b_y)**2)**0.5
                            
                            # Deduplicate if smaller >80% inside larger AND either:
                            # - Centers very close (<10px) - same element, different bbox
                            # - Similar size (<2x ratio) - slight bbox variance
                            if inter_area / smaller_area > 0.8 and (center_dist < 10 or area_ratio < 2):
                                is_duplicate = True
                                print(f"  🔄 Skipping duplicate {detection['label']} at ({detection['x1']},{detection['y1']})-({detection['x2']},{detection['y2']}) [conf={detection['conf']:.2f}] - same class, {inter_area/smaller_area*100:.0f}% overlap, {center_dist:.0f}px apart, {area_ratio:.1f}x size")
                                break
                
                if not is_duplicate:
                    deduplicated.append(detection)
            
            print(f"✅ After deduplication: {len(deduplicated)} unique elements")
            
            all_detections = deduplicated
            update_progress(f"✅ After deduplication: {len(all_detections)} unique elements", 0.65)
            
            # === NEW APPROACH: Query ALL interactive elements upfront ===
            update_progress("🔍 Querying all interactive elements from DOM...", 0.68)
            
            # Keep window at full-page size and scroll to top for consistent coordinate system
            # (YOLO coordinates are in full-page screenshot space, so DOM must match)
            driver.execute_script("window.scrollTo(0, 0)")
            time.sleep(0.1)
            
            print(f"🔍 DEBUG: Current window size: {driver.get_window_size()}")
            print(f"🔍 DEBUG: Current scroll position: {driver.execute_script('return [window.pageYOffset, window.pageXOffset]')}")
            
            # First, test if basic JavaScript execution works
            test_result = driver.execute_script("return 'JavaScript works!';")
            print(f"🔍 DEBUG: JS test result: {test_result}")
            
            # Test basic DOM query
            test_buttons = driver.execute_script("return document.querySelectorAll('button').length;")
            print(f"🔍 DEBUG: Number of <button> tags: {test_buttons}")
            
            all_dom_elements_script = """
                try {
                    console.log('Starting DOM query...');
                    const selectors = [
                        'button',
                        'a',
                        'input',
                        'textarea',
                        'select',
                        'img',  // Include images for visual-only detections
                        '[role="button"]',
                        '[role="link"]',
                        '[onclick]',
                        '[tabindex]:not([tabindex="-1"])'
                    ];
                    console.log('Selectors:', selectors);
                    
                    const elements = document.querySelectorAll(selectors.join(','));
                    console.log('Found elements:', elements.length);
                    const elementList = [];
                    
                    const dpr = window.devicePixelRatio || 1;
                    for (let el of elements) {
                        // Skip invisible elements
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            continue;
                        }
                        
                        const rect = el.getBoundingClientRect();
                        
                        // Skip elements with no size
                        if (rect.width === 0 || rect.height === 0) continue;
                        
                        // Calculate absolute position (accounting for scroll) and scale to device pixels
                        const scrollY = window.pageYOffset || document.documentElement.scrollTop;
                        const scrollX = window.pageXOffset || document.documentElement.scrollLeft;

                        // Multiply CSS pixels by devicePixelRatio so coordinates align with screenshot pixels
                        elementList.push({
                            tagName: el.tagName,
                            role: el.getAttribute('role'),
                            ariaLabel: el.getAttribute('aria-label'),
                            title: el.getAttribute('title'),
                            alt: el.getAttribute('alt'),
                            innerText: (el.innerText || "").substring(0, 100),
                            html: el.outerHTML.substring(0, 500),
                            textDecoration: style.textDecorationLine,
                            x: Math.round((rect.left + scrollX) * dpr),
                            y: Math.round((rect.top + scrollY) * dpr),
                            width: Math.round(rect.width * dpr),
                            height: Math.round(rect.height * dpr)
                        });
                    }
                    
                    console.log('Returning elementList:', elementList.length);
                    return elementList;
                } catch (error) {
                    console.error('Error in DOM query:', error);
                    return [];
                }
            """
            
            try:
                all_dom_elements = driver.execute_script(all_dom_elements_script)
                if all_dom_elements is None:
                    all_dom_elements = []
                    print("⚠️ Warning: DOM query returned None, falling back to empty list")
                print(f"🔍 DEBUG: DOM query returned {len(all_dom_elements)} elements")
                if len(all_dom_elements) > 0:
                    print(f"🔍 DEBUG: First element sample: {all_dom_elements[0]}")
                update_progress(f"✅ Found {len(all_dom_elements)} interactive elements in DOM", 0.70)
                
                # Now we can restore original window size (DOM data already collected)
                driver.set_window_size(original_size['width'], original_size['height'])
                
            except Exception as e:
                print(f"⚠️ Error querying DOM elements: {e}")
                import traceback
                traceback.print_exc()
                all_dom_elements = []
                update_progress(f"⚠️ DOM query failed, proceeding with visual-only analysis", 0.70)
            
            # Helper function: Calculate IoU (Intersection over Union)
            def calculate_iou(box1, box2):
                """Calculate IoU between YOLO detection and DOM element"""
                x1_min, y1_min, x1_max, y1_max = box1
                x2_min, y2_min, x2_max, y2_max = box2
                
                # Calculate intersection
                inter_x_min = max(x1_min, x2_min)
                inter_y_min = max(y1_min, y2_min)
                inter_x_max = min(x1_max, x2_max)
                inter_y_max = min(y1_max, y2_max)
                
                if inter_x_max < inter_x_min or inter_y_max < inter_y_min:
                    return 0.0
                
                inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)
                box1_area = (x1_max - x1_min) * (y1_max - y1_min)
                box2_area = (x2_max - x2_min) * (y2_max - y2_min)
                union_area = box1_area + box2_area - inter_area
                
                return inter_area / union_area if union_area > 0 else 0.0
            
            # Process all detections
            update_progress(f"🔍 Matching {len(all_detections)} visual elements to DOM...", 0.72)
            img = Image.open(screenshot_path).convert("RGB")
            draw = ImageDraw.Draw(img)
            
            # Prepare font for labels (use default if custom not available)
            try:
                label_font = ImageFont.truetype("arial.ttf", 16)
                small_font = ImageFont.truetype("arial.ttf", 12)
            except:
                label_font = ImageFont.load_default()
                small_font = ImageFont.load_default()
            
            for elem_idx, detection in enumerate(all_detections):
                # Update progress periodically
                if elem_idx % 10 == 0:  # Update every 10 elements
                    progress_pct = 0.72 + (elem_idx / len(all_detections)) * 0.23  # 72-95%
                    update_progress(f"🔍 Analyzing element {elem_idx + 1}/{len(all_detections)}...", progress_pct)
                    
                x1, y1, x2, y2 = detection['x1'], detection['y1'], detection['x2'], detection['y2']
                conf = detection['conf']
                cls_id = detection['cls_id']
                label = detection['label']
                
                # Debug: Print each detection
                print(f"\n🔍 Detection #{elem_idx+1}: {label} at ({x1},{y1})-({x2},{y2}), conf={conf:.2f}")
                
                # Find best matching DOM element based on IoU
                best_match = None
                best_iou = 0.0
                second_best_iou = 0.0
                
                yolo_box = (x1, y1, x2, y2)
                
                for dom_el in all_dom_elements:
                    dom_box = (
                        dom_el['x'],
                        dom_el['y'],
                        dom_el['x'] + dom_el['width'],
                        dom_el['y'] + dom_el['height']
                    )
                    
                    iou = calculate_iou(yolo_box, dom_box)
                    
                    if iou > best_iou:
                        second_best_iou = best_iou
                        best_iou = iou
                        best_match = dom_el
                    elif iou > second_best_iou:
                        second_best_iou = iou
                
                # Lower threshold to 0.1 (10% overlap) to catch more matches
                # YOLO boxes are often imprecise, especially for text links
                if best_match and best_iou > 0.1:
                    dom_data = best_match
                    if best_iou < 0.3:
                        print(f"  🔍 Low IoU match ({best_iou:.2f}) for {label} at ({x1},{y1})-({x2},{y2}) → DOM {best_match['tagName']} at ({best_match['x']},{best_match['y']})")
                else:
                    # Fallback: try center-point distance matching
                    # Sometimes YOLO box is completely off but center is close
                    yolo_cx = (x1 + x2) / 2
                    yolo_cy = (y1 + y2) / 2
                    
                    best_distance = float('inf')
                    closest_element = None
                    
                    for dom_el in all_dom_elements:
                        dom_cx = dom_el['x'] + dom_el['width'] / 2
                        dom_cy = dom_el['y'] + dom_el['height'] / 2
                        
                        distance = ((yolo_cx - dom_cx) ** 2 + (yolo_cy - dom_cy) ** 2) ** 0.5
                        
                        if distance < best_distance:
                            best_distance = distance
                            closest_element = dom_el
                    
                    # If center is within 50px, consider it a match
                    if closest_element and best_distance < 50:
                        dom_data = closest_element
                        print(f"  📍 Center-distance match ({best_distance:.0f}px) for {label} at ({x1},{y1}) → DOM {closest_element['tagName']} at ({closest_element['x']},{closest_element['y']})")
                    else:
                        dom_data = None
                        print(f"  ❌ NO MATCH for {label} at ({x1},{y1})-({x2},{y2}) size={x2-x1}x{y2-y1}px, conf={conf:.2f}")
                        print(f"     Best IoU: {best_iou:.3f}, Closest center: {best_distance:.0f}px")
                        if closest_element:
                            print(f"     Closest DOM: {closest_element['tagName']} at ({closest_element['x']},{closest_element['y']}) size={closest_element['width']}x{closest_element['height']}px")
                            print(f"     Text: '{closest_element.get('innerText', '')[:50]}'")
                
                # Calculate center for output
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                
                status = "PASS"
                issue = ""
                model_label = label  # Keep original model prediction
                
                if not dom_data:
                    # No DOM match - could be decorative or ghost control
                    if label in ["Button", "Link"]:
                        # Looks interactive but isn't in DOM = potential Ghost Control
                        status = "WARNING"
                        issue = "Visual element not found in DOM - possible Ghost Control (WCAG 4.1.2)"
                    else:
                        # Image or other non-interactive element = likely decorative
                        status = "PASS"
                        issue = "Decorative element (visual-only, not interactive)"
                else:
                    tag = dom_data.get('tagName', '')
                    role = dom_data.get('role')
                    text = (dom_data.get('innerText') or '').strip()
                    aria = (dom_data.get('ariaLabel') or '').strip()
                    title = (dom_data.get('title') or '').strip()
                    alt = (dom_data.get('alt') or '').strip()
                    
                    # VISUAL-FIRST APPROACH: The CV model found an interactive element visually.
                    # Now we verify its DOM implementation. This catches "Ghost Controls" - 
                    # things that LOOK interactive but aren't coded properly.
                    # DOM correction is secondary; the visual detection is the innovation.
                    actual_label = label
                    corrected = False
                    if tag == "BUTTON" and label == "Link":
                        actual_label = "Button"
                        corrected = True
                    elif tag == "A" and label == "Button":
                        actual_label = "Link"
                        corrected = True
                    
                    # --- WCAG CHECKS (Visual + Semantic) ---
                    # 1. Ghost Controls - VISUAL DETECTION IS KEY HERE
                    # Code scanners miss this because they only see valid HTML.
                    # We detect the VISUAL button, then verify semantic correctness.
                    if actual_label == "Button":
                        if tag in ["DIV", "SPAN", "IMG"] and role != "button":
                            status = "FAIL"
                            issue = "Visual Button coded as <div>/<span> (WCAG 4.1.2)"

                    # 2. Empty Interactive Elements - Check accessible name sources
                    if actual_label in ["Button", "Link"] and status == "PASS":
                        # For buttons/links: check text content and aria-label
                        has_accessible_name = bool(text or aria)
                        
                        if not has_accessible_name:
                            status = "FAIL"
                            issue = "Interactive element missing accessible name (WCAG 1.1.1)"
                    
                    # 2b. Images without alt text
                    if tag == "IMG" and status == "PASS":
                        if not alt and not aria:
                            status = "FAIL"
                            issue = "Image missing alt text (WCAG 1.1.1)"

                    # 3. Target Size - PURELY VISUAL CHECK
                    # This is impossible for code scanners - they don't know rendered size.
                    # We measure actual pixel dimensions from the visual detection.
                    w_px, h_px = (x2 - x1), (y2 - y1)
                    
                    if actual_label in ["Button", "Link", "Input"] and status == "PASS":
                        # Check if DOM size differs significantly from visual size
                        if dom_data:
                            dom_w = dom_data.get('width', w_px)
                            dom_h = dom_data.get('height', h_px)
                            
                            # Calculate size discrepancy
                            visual_area = w_px * h_px
                            dom_area = dom_w * dom_h
                            
                            # If DOM is significantly larger than visual (>20% larger)
                            if dom_area > visual_area * 1.2 and visual_area < 1000:
                                status = "WARNING"
                                issue = f"Visual size ({w_px}x{h_px}px) much smaller than clickable area ({dom_w}x{dom_h}px) - unclear target (WCAG 2.5.8)"
                            elif w_px < 24 or h_px < 24:
                                # Visual target is small
                                status = "WARNING"
                                issue = f"Visual target appears small ({w_px}x{h_px}px) - may be hard to click (WCAG 2.5.8)"
                        elif w_px < 24 or h_px < 24:
                            # No DOM match, just check visual size
                            status = "WARNING"
                            issue = f"Small target size ({w_px}x{h_px}px) - min 24x24px (WCAG 2.5.8)"

                    # 4. Links Color Only - VISUAL VERIFICATION
                    if actual_label == "Link" and status == "PASS":
                        if "underline" not in dom_data.get('textDecoration', ''):
                            status = "WARNING"
                            issue = "Link relies on color alone (No Underline) (WCAG 1.4.1)"
                    
                    # 5. Semantic Mismatch - Link styled as Button
                    # This catches the anti-pattern where <a> tags are styled to look like buttons.
                    # Screen readers say "link" but visual users see a "button" - confusing!
                    if actual_label == "Link" and model_label == "Button" and status == "PASS":
                        status = "WARNING"
                        issue = "Link styled as button - screen readers announce 'link' but appears as button (WCAG 1.3.1)"
                    
                    # 6. Overlapping Elements - VISUAL PROXIMITY CHECK
                    # Code scanners can't detect visual collisions
                    # Check if this element overlaps with previous ones
                    for i, prev_detection in enumerate(all_detections):
                        # Skip comparing element with itself
                        if i == elem_idx:
                            continue
                        px1, py1, px2, py2 = prev_detection['x1'], prev_detection['y1'], prev_detection['x2'], prev_detection['y2']
                        
                        # Check for overlap
                        overlap_x = max(0, min(x2, px2) - max(x1, px1))
                        overlap_y = max(0, min(y2, py2) - max(y1, py1))
                        overlap_area = overlap_x * overlap_y
                        
                        # Calculate areas
                        this_area = w_px * h_px
                        prev_area = (px2 - px1) * (py2 - py1)
                        smaller_area = min(this_area, prev_area)
                        larger_area = max(this_area, prev_area)
                        
                        # Skip parent-child relationships (one element fully inside another)
                        # This is intentional design (e.g., buttons inside search bar)
                        # Check if EITHER element is 95%+ contained in the other
                        overlap_ratio_this = overlap_area / this_area if this_area > 0 else 0
                        overlap_ratio_prev = overlap_area / prev_area if prev_area > 0 else 0
                        if overlap_ratio_this >= 0.95 or overlap_ratio_prev >= 0.95:
                            continue
                        
                        # If significant overlap (>30% of smaller element) but NOT parent-child
                        if smaller_area > 0 and (overlap_area / smaller_area) > 0.3:
                            if status == "PASS":
                                status = "WARNING"
                                issue = f"Overlapping interactive elements - may be hard to click (WCAG 2.5.8)"
                            break
                    
                    # 7. Insufficient Spacing - VISUAL LAYOUT CHECK
                    # Interactive elements too close together make clicking difficult
                    # Check distance to nearest interactive element
                    min_distance = float('inf')
                    for i, prev_detection in enumerate(all_detections):
                        # Skip comparing element with itself
                        if i == elem_idx:
                            continue
                        px1, py1, px2, py2 = prev_detection['x1'], prev_detection['y1'], prev_detection['x2'], prev_detection['y2']
                        
                        # Calculate center-to-center distance
                        this_cx, this_cy = (x1 + x2) / 2, (y1 + y2) / 2
                        prev_cx, prev_cy = (px1 + px2) / 2, (py1 + py2) / 2
                        distance = ((this_cx - prev_cx)**2 + (this_cy - prev_cy)**2)**0.5
                        
                        # Calculate edge-to-edge distance (more relevant)
                        edge_dist_x = max(0, max(x1, px1) - min(x2, px2))
                        edge_dist_y = max(0, max(y1, py1) - min(y2, py2))
                        edge_distance = (edge_dist_x**2 + edge_dist_y**2)**0.5
                        
                        min_distance = min(min_distance, edge_distance)
                    
                    # Flag if elements are very close (< 8px apart)
                    if actual_label in ["Button", "Link"] and min_distance < 8 and status == "PASS":
                        status = "WARNING"
                        issue = f"Insufficient spacing ({min_distance:.0f}px) to nearby element - recommend 8px+ (WCAG 2.5.8)"
                    
                    # 8. Very Large Interactive Elements - VISUAL SIZE CHECK
                    # Unusually large clickable areas can confuse users
                    if actual_label in ["Button", "Link"] and status == "PASS":
                        if w_px > 400 or h_px > 200:
                            status = "WARNING"
                            issue = f"Unusually large interactive element ({w_px}x{h_px}px) - may be unintentional"
                    
                    # Update label to actual for display
                    label = actual_label

                # Draw Results
                color = "green"
                if status == "FAIL": color = "red"
                elif status == "WARNING": color = "orange"
                
                draw.rectangle([x1, y1, x2, y2], outline=color, width=3 if status != "PASS" else 2)
                
                # Add numbered label on the image (element index will be its position in findings list)
                element_number = len(findings) + 1
                label_text = f"#{element_number}"

                # Smart label positioning: hug the element border and randomize ALONG the border
                box_width = x2 - x1
                box_height = y2 - y1

                # Compute text size so we can keep label within image bounds
                try:
                    tb = label_font.getbbox(label_text)
                    label_w = tb[2] - tb[0]
                    label_h = tb[3] - tb[1]
                except Exception:
                    try:
                        label_w, label_h = label_font.getsize(label_text)
                    except Exception:
                        label_w, label_h = 30, 16

                import random

                # If small element, place label OUTSIDE above the top edge, randomized along top border
                if box_width < 40 or box_height < 40:
                    min_x = x1
                    max_x = x2 - label_w
                    if max_x < min_x:
                        # If label wider than box, allow it to start at x1 but clamp later
                        min_x = x1 - (label_w // 2)
                        max_x = x1 + (label_w // 2)
                    label_x = random.randint(int(min_x), int(max_x))
                    label_y = y1 - label_h - 4
                else:
                    # For larger elements, place label INSIDE along the top edge, randomized horizontally
                    min_x = x1 + 2
                    max_x = x2 - label_w - 2
                    if max_x < min_x:
                        min_x = x1 + 2
                        max_x = x1 + 2
                    label_x = random.randint(int(min_x), int(max_x))
                    label_y = y1 + 2

                # Clamp label position to image boundaries if possible
                try:
                    img_width = img.width
                    label_x = max(0, min(label_x, img_width - label_w))
                except Exception:
                    pass
                
                # Draw label with colored background
                try:
                    bbox_result = draw.textbbox((label_x, label_y), label_text, font=label_font)
                    label_bg = [bbox_result[0]-3, bbox_result[1]-2, bbox_result[2]+3, bbox_result[3]+2]
                except:
                    # Fallback if textbbox not available
                    label_bg = [label_x-3, label_y-2, label_x+35, label_y+20]
                
                # Use color-coded background matching the box color
                draw.rectangle(label_bg, fill=color, outline="white", width=1)
                draw.text((label_x, label_y), label_text, fill="white", font=label_font)
                
                # Add all findings (including PASS) to the report
                findings.append({
                    "type": label,
                    "status": status,
                    "issue": issue if issue else "No accessibility issues detected",
                    "confidence": round(conf, 2),
                    "dom": dom_data,
                    "bbox": {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "w": x2 - x1,
                        "h": y2 - y1,
                        "center_x": cx,
                        "center_y": cy
                    }
                })

            img.save(annotated_path)
            update_progress("✅ Audit complete! Generating report...", 0.95)

        except Exception as e:
            print(f"Error: {e}")
            if progress_callback:
                progress_callback(f"❌ Error: {e}", 1.0)
            return [], None
        finally:
            driver.quit()
            
        update_progress("✅ Audit complete!", 1.0)
        return findings, annotated_path