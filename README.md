# Garden motion-detection prototype

Detection-only Python prototype for a Raspberry Pi 5 and Camera Module 3 NoIR Wide, using Picamera2 and OpenCV. It continuously captures 640 × 360 frames, converts them to greyscale, applies a Gaussian blur, and compares each frame with the previous one. Thresholding, dilation, and a minimum contour area filter identify moving regions.

Significant motion prints a local timestamp and saves a JPEG with green bounding boxes. A cooldown limits both messages and saved images; detection continues during that cooldown. There is no GPIO, relay, or pump control, and no animal classification yet. See [the project plan](PROJECT_PLAN.md).

## Raspberry Pi OS setup

Use Raspberry Pi OS Bookworm or newer with its system Python. Power off the Pi before connecting the camera, using a camera cable compatible with the Pi 5 connector. Then boot and run:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-venv rpicam-apps
rpicam-hello --list-cameras
```

The camera should appear in the list. To check capture without a desktop preview:

```bash
rpicam-still --nopreview --timeout 2000 --output camera-test.jpg
```

Close any other camera programs before running the prototype.

From the directory containing `motion_detector.py`, create a virtual environment that can access the packages installed by apt:

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -c "import cv2; from picamera2 import Picamera2; print('Camera packages available')"
```

Picamera2 depends on system camera libraries. Use the system packages above rather than installing replacement Picamera2, OpenCV, or NumPy packages with pip in this environment. Raspberry Pi documents the apt installation and `--system-site-packages` approach in the [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).

## Run

```bash
source .venv/bin/activate
python motion_detector.py
```

Allow three seconds for the camera to settle, then move through its view. JPEGs are saved in `captures/`, relative to your working directory. Press **Ctrl+C** to stop and release the camera. No display is required, so this also works over SSH.

Example with custom settings:

```bash
python motion_detector.py --min-area 1000 --cooldown 15 --fps 8 --threshold 30 --output-dir captures
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--min-area` | `600` | Minimum contour area in pixels, measured after dilation. Increase to ignore small changes. |
| `--cooldown` | `10` | Minimum seconds between saved detections. Zero saves every qualifying frame. |
| `--fps` | `10` | Requested capture rate and processing limit, from 1 to 30 FPS. Lower values reduce CPU use. |
| `--threshold` | `25` | Minimum pixel brightness difference, from 1 to 255. Increase to reduce sensitivity. |
| `--warmup` | `3` | Seconds to let exposure and focus settle before detection. |
| `--output-dir` | `captures` | Destination folder, created automatically. |

Run `python motion_detector.py --help` for the available settings.

## Testing and limitations

Start with a fixed camera and a quiet garden. Check that a still scene produces few captures, walk across the view, and inspect the bounding boxes. Keep moving during the cooldown and confirm images are spaced at least that far apart. Check Ctrl+C, then restart to confirm the camera was released.

The low resolution, default 10 FPS rate, one OpenCV worker thread, and lack of a preview keep processing modest; actual CPU use and throughput need checking on the Pi. Image encoding or slow storage may reduce the achieved frame rate.

Frame differencing detects changes, including moving leaves, shadows, exposure or autofocus adjustments, rain, and camera shake. It cannot identify cats, and stationary objects stop registering as motion. Fast movement may show boxes at both the old and new positions. A NoIR camera still needs visible or infrared illumination in darkness. Tune settings in the actual garden before using results to guide later development.

Images accumulate until you remove them; the prototype has no retention policy. If imports fail, check that you activated the environment created with `--system-site-packages`. If the camera is unavailable, repeat the camera listing and capture checks above and ensure another process is not using it.
