## WebUI-7K UI Element Detector
# Overview
This repository contains the code and minimal notebook used to fine-tune a YOLO11s model on the WebUI-7K dataset (from BigLab) to detect UI components in website screenshots.

# Target classes

  - Button
  - Link
  - Input
  - Image
  - Icon
  - Text
  - Heading

The final model is trained at 1280×1280 resolution and achieves detection of core UI elements in diverse web layouts.

# Dataset

We use the dataset: [biglab/webui-7k](https://huggingface.co/datasets/biglab/webui-7k)

The dataset contains rendered webpages, accessibility trees, and bounding boxes for UI elements.

# Conversion to YOLO format

The notebook automatically:

- Downloads the Hugging Face dataset via ```snapshot_download```.
- Extracts for each screenshot: ```*-screenshot.webp``` ```*-axtree.json.gz``` ```*-bb.json.gz```
- Maps raw WebUI roles to **seven** final classes using:
  ```python
  ROLE_MAP = {
    "button": 0, "menuitem": 0, "tab": 0, "pushbutton": 0,        # Button
    "link": 1,                                                     # Link
    "textbox": 2, "searchbox": 2, "combobox": 2, "checkbox": 2,
    "radio": 2,                                                    # Input
    "img": 3, "image": 3, "figure": 3,                             # Image
    "graphics-symbol": 4,                                          # Icon
    "statictext": 5, "paragraph": 5, "label": 5,                  # Text
    "heading": 6                                                   # Heading
  }
  ```
- Converts CSS coordinates to pixel coordinates then to a normalized YOLO format.
- Builds a YOLO-ready dataset:
  ```bash
  /content/webui_7k_yolo/
    ├── train/
    │   ├── images/
    │   └── labels/
    └── val/
        ├── images/
        └── labels/
  ```
- Generates the Ultralytics training config:
  ```bash
  /content/webui7k.yaml
  ```

# Model training 

We fine-tune **YOLO11s** on the processed WebUI-7K YOLO dataset.

**Training cell (from the notebook)**

```python
from ultralytics import YOLO

# Load pretrained model
model = YOLO("yolo11s.pt")

# Fine-tune on WebUI-7K
results = model.train(
    data="/content/webui7k.yaml",
    project='/content/drive/MyDrive/WebUI7K_Training',
    name="yolo11s_webui7k",
    epochs=50,
    imgsz=1280,
    batch=16,
    patience=10,
    plots=True,
    save=True,
    device=0
)

```

All the experiment results are saved under: 
```bash
/content/drive/MyDrive/WebUI7K_Training/yolo11s_webui7k/
```
Experiments outputs include:
- ```best.pt```(best model)
- ```last.pt```
- training curves
- confusion matrices
- precision-recall curves
- F1-confidence curves
- recall-confidence curves

# Running the notebook on Google Colab 
- The code and paths in the notebook are designed to be run on Google Colab.
- Run the cells top-down.
- Retrieve the trained weights from:
  ```bash
   /content/drive/MyDrive/WebUI7K_Training/yolo11s_webui7k/weights/best.pt
  ```
# Important note 

Even though a label called **Background** was not created during the preporocessing of the data, it appears in the confusion matrix. It is not a class we train on, but rather a class YOLO adds during evalution.

The confusion matrix includes row/column background to count for:

- Predictions where the model said “Button” but there was no object there

- Predictions where the model said “Link” but there was nothing there

- Cases where the model failed to detect an actual object, and that object is treated as “true = Some class, predicted = background”

So YOLO adds **Background** only for metrics.
