# Test cat detection on saved images

This detector processes one image or a directory, without requiring motion. It
saves an annotated JPEG and a matching JSON report for **every readable image**,
including those with no cat detected. It performs no camera or pump control.

## Install on the Pi

Use 64-bit Raspberry Pi OS. Create a separate environment for YOLO so its pip
dependencies do not change the motion detector's environment:

```bash
sudo apt install -y python3-venv
/usr/bin/python3 -m venv .venv-cat
source .venv-cat/bin/activate
python -m pip install --upgrade pip
python -m pip install --no-cache-dir torch==2.8.0+cpu torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install --no-cache-dir -r requirements-cat.txt -c constraints-cat-cpu.txt
```

This environment uses pip-installed OpenCV and needs no Picamera2. Installation
downloads PyTorch and other sizeable dependencies and may take time. The first
use of `yolo26n.pt` also downloads pretrained weights; subsequent runs use the
local weights. Internet access is needed for installation and that first download.
Install the CPU-only PyTorch packages first as shown above. The constraints file
keeps subsequent dependency resolution on those versions, avoiding NVIDIA CUDA
downloads that the Pi cannot use. The constraints target Python 3.10–3.13 on
64-bit Linux; other Python versions may need a different compatible pair.
See [Ultralytics' Pi guidance](https://docs.ultralytics.com/guides/raspberry-pi/).

## First test

If installation fails with `No space left on device`, check both storage and the
temporary filesystem with `df -h . /tmp`. Clear pip's download cache with
`python -m pip cache purge` (this removes cached package downloads, not your code
or images), then install using the CPU-only commands above. If only `/tmp` is
full or too small, create a directory on a filesystem with free space and set
`TMPDIR` to it for installation. `--no-cache-dir` avoids a download-cache copy
but installation still requires temporary space and space for the packages.

Put original, unannotated garden JPEG/PNG files in `garden-images/`. Include cats
at different distances, people, other animals, and empty scenes, in daylight and
at night. Avoid using images with green motion boxes drawn on them where possible.

```bash
python cat_detector.py garden-images
```

Or process one image:

```bash
python cat_detector.py garden-images/cat.jpg
```

Each run gets its own directory under `cat-results/`. Inspect the annotated
images and JSON reports. Reports include all objects detected above the threshold,
their labels, confidence scores and boxes, whether any cat was detected, model
and effective settings, image dimensions, and prediction wall time. Timing
includes preprocessing and postprocessing; the first prediction may be slower
due to model initialization. A negative result means no cat was detected above
the threshold, not proof that no cat is present.

The detector keeps the original image resolution for input and saved annotations.
YOLO prepares it for the configured inference size while preserving aspect ratio.
Boxes in the JSON refer to the original image coordinates. See
[Ultralytics prediction documentation](https://docs.ultralytics.com/modes/predict/).

## Try different settings

Edit `cat_config.json`, or make a copy for an experiment:

```bash
cp cat_config.json cat-sensitive.json
python cat_detector.py garden-images --config cat-sensitive.json
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `model` | `yolo26n.pt` | Pretrained YOLO nano weights or exported NCNN directory. |
| `confidence` | `0.5` | Minimum retained detection score, greater than 0 and at most 1. |
| `image_size` | `640` | Inference target size; use a multiple of 32. Higher values can help small objects but cost more processing. |
| `output_dir` | `cat-results` | Results root, relative to the working directory. |

CLI options override config values, for example:

```bash
python cat_detector.py garden-images --confidence 0.35 --image-size 640
```

All paths are relative to your working directory. Run again after editing settings.
Compare missed cats and false detections across the same images. Confidence is a
model score, not a calibrated probability. The pretrained model recognises COCO
categories including cat, dog, and person; it does not identify individual cats
or provide every species you may encounter in the garden.

## Faster Pi inference with NCNN

First establish a working baseline with `.pt` weights. Then export and compare:

```bash
yolo export model=yolo26n.pt format=ncnn imgsz=640
python cat_detector.py garden-images --model yolo26n_ncnn_model --image-size 640
```

Export can install additional dependencies. Use the same inference size as the
exported model. Compare accuracy and timing before adopting the export; actual
performance needs measurement on your Pi. See
[Ultralytics NCNN instructions](https://docs.ultralytics.com/integrations/ncnn/).

Ultralytics offers AGPL-3.0 and Enterprise licensing; its terms are documented on
the [Ultralytics licensing page](https://www.ultralytics.com/license).

## Next integration

`CatDetector.detect(frame)` accepts an OpenCV BGR image and returns an annotated
image plus a result dictionary. This is the shared stage for later Pi-camera and
RTSP integration. Live cat detection is not wired up yet. The existing motion
detector and its config remain separate. Press Ctrl+C to stop between operations;
a model inference or file write may need to finish first.
