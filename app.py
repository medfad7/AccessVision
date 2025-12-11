import streamlit as st
import os
import json
from datetime import datetime
from auditor import AccessAuditor
from PIL import Image, ImageDraw, ImageFont
import numpy as np

def create_highlighted_image(base_image_path, findings, highlight_idx=None, viewport_height=800, device='desktop'):
    """Create an image with one finding highlighted by dimming everything else"""
    # Use the annotated image (with all boxes already drawn)
    img = Image.open(base_image_path).convert("RGBA")
    
    crop_y_start = 0
    crop_y_end = img.height
    
    # If highlighting a specific finding, crop and dim around it
    if highlight_idx is not None and highlight_idx < len(findings):
        f = findings[highlight_idx]
        bbox = f['bbox']
        
        # Calculate element center
        elem_center_y = (bbox['y1'] + bbox['y2']) // 2
        
        # Crop to viewport centered on element (with some context above/below)
        crop_y_start = max(0, elem_center_y - viewport_height // 2)
        crop_y_end = min(img.height, crop_y_start + viewport_height)
        
        # Adjust if we hit bottom of page
        if crop_y_end == img.height:
            crop_y_start = max(0, crop_y_end - viewport_height)
        
        # Crop FIRST to reduce processing
        img = img.crop((0, crop_y_start, img.width, crop_y_end))
        
        # Create overlay ONLY for visible region (much faster!)
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        overlay_array = np.array(overlay, dtype=np.float32)
        
        padding = 10
        x1, y1, x2, y2 = bbox['x1'], bbox['y1'], bbox['x2'], bbox['y2']
        
        # Adjust coordinates for cropped image
        y1 -= crop_y_start
        y2 -= crop_y_start
        
        # Element bounds
        elem_x1 = x1 - padding
        elem_y1 = y1 - padding
        elem_x2 = x2 + padding
        elem_y2 = y2 + padding
        
        # Create distance-based gradient (only for visible region)
        gradient_falloff = 10  # Pixels for gradient to fully fade (smaller = tighter spotlight)
        height, width = img.size[1], img.size[0]
        
        # Use vectorized numpy operations instead of loops (much faster!)
        y_coords, x_coords = np.ogrid[0:height, 0:width]
        
        # Calculate distance from element box for all pixels at once
        # Correct distance calculation: ensure we compute distance to box edges
        # Use nested np.maximum to emulate max(elem_x1 - x, 0, x - elem_x2)
        dx = np.maximum(np.maximum(elem_x1 - x_coords, 0), x_coords - elem_x2)
        dy = np.maximum(np.maximum(elem_y1 - y_coords, 0), y_coords - elem_y2)
        distance = np.sqrt(dx**2 + dy**2)
        
        # Map distance to opacity (0-180)
        alpha = np.minimum(180, (distance / gradient_falloff * 180)).astype(np.uint8)
        overlay_array[:, :, 3] = alpha
        
        overlay = Image.fromarray(overlay_array.astype(np.uint8), 'RGBA')
        
        # Composite the overlay onto the cropped image
        img = Image.alpha_composite(img, overlay)
        
        # Update crop bounds (already cropped)
        crop_y_start = 0
        crop_y_end = img.height
    
    # Convert to RGB
    result = img.convert("RGB")

    # Resize to emulate device viewport widths (downscale only)
    device = (device or 'desktop').lower()
    if device == 'mobile':
        target_w = 390
    elif device == 'ipad':
        target_w = 768
    else:
        target_w = result.width

    # Only downscale if the image is wider than the target
    if target_w and result.width > target_w:
        scale = target_w / result.width
        new_h = max(1, int(result.height * scale))
        result = result.resize((target_w, new_h), Image.LANCZOS)

    return result, crop_y_start, crop_y_end

def get_issue_explanation(issue_text):
    """Return a helpful explanation for different types of accessibility issues"""
    explanations = {
        "Ghost Controls": "This element looks interactive but isn't coded properly for screen readers. Use semantic HTML (<button>, <a>) instead of <div> or <span>.",
        "missing accessible name": "Interactive elements need descriptive text or aria-labels so screen readers can announce their purpose to users.",
        "Small target size": "Touch targets should be at least 24x24px (WCAG 2.5.8) to be easily clickable, especially for users with motor disabilities.",
        "color alone": "Links should be underlined or have another visual indicator beyond just color, helping colorblind users identify them.",
        "styled as button": "This link is styled to look like a button. Screen readers will announce 'link' but users expect button behavior, causing confusion.",
        "Visual element not found": "The computer vision model detected something that appears interactive, but it's not present in the DOM or is hidden from accessibility tools.",
        "Overlapping": "Interactive elements are visually overlapping, making it difficult for users to accurately click the intended target.",
        "Insufficient spacing": "Elements are too close together. Recommend at least 8px spacing between interactive elements for easier clicking.",
        "Unusually large": "This interactive area is very large, which might be unintentional or confusing to users about what exactly is clickable."
    }
    
    for key, explanation in explanations.items():
        if key.lower() in issue_text.lower():
            return explanation
    return "This element has an accessibility issue that needs attention."


st.set_page_config(page_title="AccessVision", layout="wide")

# Add CSS to prevent page scrolling
st.markdown("""
    <style>
    /* Prevent any page scrolling */
    html, body, [data-testid="stAppViewContainer"], .main {
        overflow: hidden !important;
        height: 100vh !important;
        max-height: 100vh !important;
    }
    
    section.main .block-container {
        height: 100vh !important;
        max-height: 100vh !important;
        overflow: hidden !important;
        padding-bottom: 0 !important;
        padding-top: 1rem !important;
    }
    
    /* Smooth scroll behavior for better UX */
    .element-block {
        scroll-margin-top: 20px;
    }
    
    /* Highlight selected element */
    .selected-element {
        animation: pulse 0.5s ease-in-out;
        border-left: 4px solid #FFD700 !important;
        padding-left: 8px !important;
    }
    
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.7; }
    }
    </style>
""", unsafe_allow_html=True)

# Initialize session state
if 'selected_finding' not in st.session_state:
    st.session_state.selected_finding = None
if 'expanded_finding' not in st.session_state:
    st.session_state.expanded_finding = None
if 'show_results' not in st.session_state:
    st.session_state.show_results = False
if 'audit_history' not in st.session_state:
    st.session_state.audit_history = []
if 'audit_failed' not in st.session_state:
    st.session_state.audit_failed = False

def get_cached_audit(url):
    """Check if URL exists in audit history and return it"""
    for audit in reversed(st.session_state.audit_history):
        if audit['url'] == url:
            return audit
    return None

# Sidebar
with st.sidebar:
    st.header("Configuration")
    model_path = st.text_input("Model Path", "best.pt")
    st.caption("Ensure 'best.pt' is in the project folder.")
    
    # Show audit history
    if st.session_state.audit_history:
        st.markdown("---")
        st.subheader("Recent Audits")
        for idx, audit in enumerate(reversed(st.session_state.audit_history[-5:])):
            # Calculate the actual index in the original list
            actual_idx = len(st.session_state.audit_history) - 1 - idx
            
            col_a, col_b = st.columns([6, 1], vertical_alignment="center")
            with col_a:
                if st.button(f"🔗 {audit['url'][:30]}...", key=f"history_{idx}", width="stretch"):
                    st.session_state.url = audit['url']
                    st.session_state.per_device_results = audit.get('per_device', {})
                    st.session_state.device_view = 'desktop'
                    st.session_state.show_results = True
                    st.rerun()
            with col_b:
                if st.button("❌", key=f"delete_{idx}", width="stretch"):
                    # Remove from history
                    st.session_state.audit_history.pop(actual_idx)
                    # No shared file write; history is per session/tab
                    st.rerun()

# Show input form or results based on state
if not st.session_state.show_results:
    st.title("👁️ AccessVision")
    st.markdown("### AI-Powered Web Accessibility Auditor")
    # Main Input
    url = st.text_input("Enter Website URL:", "https://www.google.com")
    run_btn = st.button("Run Audit", type="primary")
else:
    # Show back button in a compact way
    if st.button("← Back to Input"):
        st.session_state.show_results = False
        st.session_state.selected_finding = None
        st.session_state.expanded_finding = None
        st.rerun()

if not st.session_state.show_results and run_btn and url:
    st.session_state.selected_finding = None  # Reset selection on new audit
    # Normalize URL (allow input without scheme like example.com)
    def normalize_url(u: str) -> str:
        u = u.strip()
        if not u:
            return u
        if u.startswith('http://') or u.startswith('https://'):
            return u
        # Default to https
        return 'https://' + u

    normalized_url = normalize_url(url)
    if normalized_url != url:
        st.info(f"Normalized URL → `{normalized_url}`")

    # Check if URL is already in cache
    cached_audit = get_cached_audit(normalized_url)
    if cached_audit:
        # Load from cache
        st.session_state.findings = cached_audit['findings']
        st.session_state.img_path = cached_audit['img_path']
        st.session_state.url = cached_audit['url']
        st.session_state.show_results = True
        st.rerun()
    elif not os.path.exists(model_path):
        st.error("❌ Model not found! Please download 'best.pt' from Drive.")
    else:
        auditor = AccessAuditor(model_path)
        
        # Create a placeholder for progress updates
        progress_placeholder = st.empty()
        status_placeholder = st.empty()
        
        progress_placeholder.progress(0)
        status_placeholder.info("🧠 Loading model and navigating to page...")
        
        # Generate unique audit ID based on timestamp to prevent overwriting cached images
        audit_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Run per-device audit (desktop, iPad, mobile)
        try:
            results = auditor.audit_url(normalized_url, progress_callback=lambda msg, pct: (
                progress_placeholder.progress(pct),
                status_placeholder.info(msg)
            ), audit_id=audit_id)
            st.session_state.audit_failed = False
        except Exception as e:
            # Mark that the audit failed so UI can show an appropriate message
            st.session_state.audit_failed = True
            # Surface the error to the user in the status area and stop
            status_placeholder.error(f"❌ Failed to audit the page: {str(e)}")
            progress_placeholder.empty()
            st.rerun()
        
        # Store per-device results in session state
        st.session_state.per_device_results = results
        # Default device view
        st.session_state.device_view = 'desktop'
        st.session_state.url = url
        st.session_state.show_results = True

        # Save to audit history (per-session)
        audit_entry = {
            'url': url,
            'timestamp': datetime.now().isoformat(),
            'per_device': results
        }
        st.session_state.audit_history.append(audit_entry)

        st.rerun()

# Display results if they exist and show_results is True
if st.session_state.show_results and 'per_device_results' in st.session_state:
    # Defer selecting which device's results to display until after the Device view
    # selector widget is created (see below). Initialize empty placeholders so
    # later code can fill them based on the selectbox value.
    findings = []
    img_path = None

    col1, col2 = st.columns([2, 1])

    with col1:
        # Device view selector: Desktop / iPad / Mobile
        # Default to previously selected device if available
        default_device = {'desktop': 0, 'ipad': 1, 'mobile': 2}.get(st.session_state.get('device_view', 'desktop'), 0)
        device_label = st.selectbox("Device view", ["Desktop", "iPad", "Mobile"], index=default_device, key="device_view_select")
        device_map = {"Desktop": "desktop", "iPad": "ipad", "Mobile": "mobile"}
        device_key = device_map.get(device_label, "desktop")
        # Persist user's device choice to session state for consistent result selection
        st.session_state.device_view = device_key

        # Determine per-device data now that the device selectbox exists
        per_device = st.session_state.per_device_results.get(device_key, None) if 'per_device_results' in st.session_state else None
        if per_device:
            findings = per_device.get('findings', [])
            img_path = per_device.get('annotated_path')
        else:
            findings = []
            img_path = None

        # Display highlighted element or full-page image when available
        if img_path and os.path.exists(img_path) and st.session_state.selected_finding is not None and findings:
            # Use the annotated image (already has all boxes drawn)
            highlighted_img, crop_start, crop_end = create_highlighted_image(
                img_path,
                findings,
                st.session_state.selected_finding,
                viewport_height=1000,  # Show ~1000px of context around element
                device=device_key,
            )

            # Get the element's position
            selected = findings[st.session_state.selected_finding]
            elem_y = selected['bbox']['y1']

            st.caption(f"🎯 Showing element at Y={elem_y}px (viewport: {crop_start}-{crop_end}px)")
            st.image(highlighted_img, caption="Viewport: Element Highlighted", use_container_width=True)

        elif img_path and os.path.exists(img_path):
            # Show full page when nothing selected (resized to device)
            full_img, _, _ = create_highlighted_image(
                img_path,
                findings,
                highlight_idx=None,
                viewport_height=1000,
                device=device_key,
            )
            st.image(full_img, caption="Full Page Analysis", use_container_width=True)

            # Show page dimensions info
            try:
                img_obj = Image.open(img_path)
                st.caption(f"📐 Page size: {img_obj.width}×{img_obj.height}px")
            except Exception:
                pass
        else:
            st.info("No screenshot available for this device yet.")
            
        with col2:
            # Create a scrollable container for the audit report
            # Fixed height to prevent page scrolling
            with st.container(height=600):
                st.subheader("Audit Report")
                
                # Show URL being audited
                if 'url' in st.session_state:
                    st.caption(f"🌐 Auditing: `{st.session_state.url}`")
                
                fails = len([f for f in findings if f['status'] == 'FAIL'])
                warns = len([f for f in findings if f['status'] == 'WARNING'])
                passes = len([f for f in findings if f['status'] == 'PASS'])
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Critical", fails, delta=None, delta_color="inverse")
                c2.metric("Warnings", warns, delta=None, delta_color="off")
                c3.metric("Passed", passes, delta=None, delta_color="normal")
                
                # Show overall score
                total = fails + warns + passes
                if total > 0:
                    pass_rate = (passes / total) * 100
                    if fails == 0 and warns == 0:
                        st.success(f"✅ Perfect! All {passes} elements passed.")
                    elif fails == 0:
                        st.info(f"ℹ️ {pass_rate:.0f}% pass rate - {warns} warnings to review")
                    else:
                        st.warning(f"⚠️ {fails} critical issues need immediate attention")
                
                # Element type breakdown
                st.markdown("---")
                with st.expander("📊 Breakdown by Element Type", expanded=False):
                    element_stats = {}
                    for f in findings:
                        elem_type = f['type']
                        if elem_type not in element_stats:
                            element_stats[elem_type] = {'FAIL': 0, 'WARNING': 0, 'PASS': 0}
                        element_stats[elem_type][f['status']] += 1
                    
                    for elem_type, stats in sorted(element_stats.items()):
                        total_elem = stats['FAIL'] + stats['WARNING'] + stats['PASS']
                        st.markdown(f"**{elem_type}** ({total_elem} total)")
                        
                        col_f, col_w, col_p = st.columns(3)
                        with col_f:
                            st.metric("❌ Fail", stats['FAIL'])
                        with col_w:
                            st.metric("⚠️ Warn", stats['WARNING'])
                        with col_p:
                            st.metric("✅ Pass", stats['PASS'])
                        st.markdown("---")
                
                st.markdown("---")
                
                if findings:
                    # Always sort by confidence, no filtering or threshold
                    # Add JavaScript for auto-scrolling to selected element
                    if st.session_state.selected_finding is not None:
                        st.markdown(f"""
                            <script>
                            setTimeout(function() {{
                                const element = document.getElementById('element_{st.session_state.selected_finding}');
                                if (element) {{
                                    element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                                }}
                            }}, 100);
                            </script>
                        """, unsafe_allow_html=True)
                    
                    # Show failures and warnings first
                    issues_all = [f for f in findings if f['status'] != 'PASS']
                    passing_all = [f for f in findings if f['status'] == 'PASS']

                    # Always sort by confidence, no filtering or threshold
                    issues_all.sort(key=lambda x: x.get('confidence', 0), reverse=True)
                    passing_all.sort(key=lambda x: x.get('confidence', 0), reverse=True)
                    issues = issues_all

                    if issues:
                        st.markdown("### ⚠️ Issues Found")
                        for idx, f in enumerate(issues):
                            icon = "🔴" if f['status'] == "FAIL" else "⚠️"
                            bbox = f['bbox']
                            
                            # Find original index for highlighting
                            orig_idx = findings.index(f)
                            
                            # Create anchor for this element
                            is_selected = st.session_state.selected_finding == orig_idx
                            element_class = "selected-element" if is_selected else ""
                            
                            # Use container with unique ID for scroll targeting
                            st.markdown(f'<div id="element_{orig_idx}" class="element-block {element_class}"></div>', unsafe_allow_html=True)
                            
                            # Highlight button
                            button_label = f"{'🎯 ' if is_selected else '📍 '}{f['type']} #{orig_idx+1}"
                            
                            if st.button(button_label, key=f"finding_{orig_idx}", width="stretch"):
                                if st.session_state.selected_finding == orig_idx:
                                    st.session_state.selected_finding = None  # Deselect
                                    st.session_state.expanded_finding = None
                                else:
                                    st.session_state.selected_finding = orig_idx  # Select
                                    st.session_state.expanded_finding = orig_idx  # Auto-expand
                                st.rerun()
                            
                            # Display the issue
                            st.error(f"{icon} **{f['issue']}**")
                            st.caption(f"📍 Position: ({bbox['x1']}, {bbox['y1']}) • Size: {bbox['w']}×{bbox['h']}px")
                            
                            # Show explanation
                            explanation = get_issue_explanation(f['issue'])
                            st.info(f"💡 {explanation}")
                            
                            # Auto-expand details when selected
                            is_expanded = st.session_state.expanded_finding == orig_idx
                            with st.expander(f"Details & Source Code", expanded=is_expanded):
                                col_a, col_b = st.columns(2)
                                with col_a:
                                    st.caption("**Confidence:**")
                                    st.write(f"{f['confidence']*100:.1f}%")
                                with col_b:
                                    st.caption("**Status:**")
                                    st.write(f['status'])
                                
                                st.caption("**HTML Element:**")
                                if f['dom']:
                                    st.code(f['dom']['html'], language='html')
                                else:
                                    st.warning("⚠️ No DOM element matched (visual-only detection)")
                                    st.warning("⚠️ No DOM element matched (visual-only detection)")
                            
                            st.markdown("---")                    # Show passing elements in collapsible section
                    # Use the earlier computed `passing_all` so sorting applies
                    try:
                        passing = passing_all
                    except NameError:
                        passing = [f for f in findings if f['status'] == 'PASS']
                    if passing:
                        # Initialize passing section state if not exists
                        if 'passing_section_expanded' not in st.session_state:
                            st.session_state.passing_section_expanded = False
                        
                        with st.expander(f"✅ Passed Elements ({len(passing)})", expanded=st.session_state.passing_section_expanded):
                            for idx, f in enumerate(passing):
                                bbox = f['bbox']
                                orig_idx = findings.index(f)
                                
                                # Create anchor for this element
                                is_selected = st.session_state.selected_finding == orig_idx
                                element_class = "selected-element" if is_selected else ""
                                st.markdown(f'<div id="element_{orig_idx}" class="element-block {element_class}"></div>', unsafe_allow_html=True)
                                
                                # Highlight button
                                button_label = f"{'🎯 ' if is_selected else '📍 '}{f['type']} #{orig_idx+1}"
                                
                                if st.button(button_label, key=f"pass_{orig_idx}", width="stretch"):
                                    # Keep the passing section expanded when clicking elements
                                    st.session_state.passing_section_expanded = True
                                    
                                    if st.session_state.selected_finding == orig_idx:
                                        st.session_state.selected_finding = None
                                        st.session_state.expanded_finding = None
                                    else:
                                        st.session_state.selected_finding = orig_idx
                                        st.session_state.expanded_finding = orig_idx
                                    st.rerun()
                                
                                st.success(f"✅ **{f['type']}** - No issues")
                                st.caption(f"📍 Position: ({bbox['x1']}, {bbox['y1']}) • Size: {bbox['w']}×{bbox['h']}px")
                                
                                # Auto-expand details when selected
                                is_expanded = st.session_state.expanded_finding == orig_idx
                                with st.expander(f"View Source Code", expanded=is_expanded):
                                    col_a, col_b = st.columns(2)
                                    with col_a:
                                        st.caption("**Confidence:**")
                                        st.write(f"{f['confidence']*100:.1f}%")
                                    with col_b:
                                        st.caption("**Status:**")
                                        st.write(f['status'])
                                    
                                    st.caption("**HTML Element:**")
                                    if f['dom']:
                                        st.code(f['dom']['html'], language='html')
                                    else:
                                        st.warning("⚠️ No DOM element matched (visual-only detection)")
                                
                                st.markdown("---")
                else:
                    st.success("✅ No accessibility violations detected!")
else:
    # Only show a failure message if an audit attempt actually failed.
    if st.session_state.get('audit_failed', False):
        st.error("❌ Failed to audit the page. Check the URL and try again.")