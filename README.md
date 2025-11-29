# AccessVision

AI-Powered Web Accessibility Auditor

## Overview
AccessVision is a tool that uses computer vision and DOM analysis to audit web pages for accessibility issues. It combines a YOLO-based visual detector with DOM queries to find interactive elements, highlight potential problems, and provide actionable feedback for designers and developers.

## Features
- Detects interactive elements visually and via DOM
- Highlights accessibility issues (ghost controls, missing labels, small targets, overlap, etc.)
- Gradient spotlight visualization for selected findings
- Streamlit UI for easy use
- Per-user audit history (private to each browser tab/session)
- Sorts findings by confidence (most reliable first)

## How to Run
1. **Install dependencies**
   - Python 3.8+
   - Install required packages:
     ```bash
     pip install -r requirements.txt
     ```
   - Download the YOLO model file (`best.pt`) and place it in the AccessVision folder.

2. **Start the app**
   - From the `AccessVision` directory, run:
     ```bash
     streamlit run app.py
     ```
   - The app will open in your browser.

3. **Audit a website**
   - Enter a website URL (e.g., `https://www.google.com`) in the input box.
   - Click "Run Audit".
   - Review the findings, which are sorted by confidence.
   - Click any finding to see a spotlight visualization and details.

## Intended Use Cases
- **Web accessibility education:** Demonstrate common accessibility issues visually for students and designers.
- **Design review:** Quickly audit live sites or prototypes for missing labels, small targets, and other WCAG issues.
- **ML/AI coursework:** Show how computer vision can augment traditional DOM-based accessibility tools.
- **Rapid prototyping:** Get instant feedback on UI accessibility during development.

## Current Limitations
- **Vision model accuracy:** YOLO may miss or misclassify elements, especially on highly custom UIs.
- **DOM matching:** Some visual-only elements may not be found in the DOM (ghost controls).
- **No automated fixes:** The tool highlights issues but does not modify code or suggest direct fixes.
- **Performance:** Large or complex pages may take longer to process due to screenshot and model inference.
- **Model file required:** You must provide a trained YOLO model (`best.pt`).

## Device emulation & mobile support
- **Per-device audits:** AccessVision captures device-specific screenshots and runs audits for Desktop, iPad Pro, and iPhone 13 Pro viewports. The UI includes a `Device view` selector so you can view annotated images and findings per device.
- **Debug artifacts:** For debugging and demonstration, the auditor saves per-device artifacts (screenshots, annotated images, and raw YOLO debug images such as `all_detections_before_dedup_<device>.png`) under the `audit_results/` folder.

---
*AccessVision was developed for educational purposes as part of an ML course project.*
