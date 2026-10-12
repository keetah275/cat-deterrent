# Garden motion-detection prototype

For the saved-image YOLO cat detector, see [cat detection setup and testing](CAT_DETECTION.md).

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

## Configuration and capture records

Edit `config.json` beside the script to change the defaults. Restart the detector
after editing; settings are loaded once at startup. No additional packages are needed.
JSON uses the names `min_area`, `cooldown`, `fps`, `threshold`, `warmup`,
`output_dir`, `source`, and `input_path`. Output and image input paths are relative
to your terminal's working directory.

To compare experiments, copy `config.json` to another file, adjust its values,
and select it explicitly:

```bash
cp config.json garden-test.json
python motion_detector.py --config garden-test.json
```

Each JPEG gets a matching JSON record, for example `motion_20261010_170000_123456.jpg`
and `motion_20261010_170000_123456.json`. The record contains the timestamp,
effective settings, fixed processing parameters, and bounding boxes. This records
the settings actually used even if you later edit the original config file.

Command-line options override values from the selected config file. Missing keys
use the built-in defaults; invalid values or unknown keys produce an error before
the camera starts. For example:

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

### Ignore motion around the frame edges

Set `"ignore_border": 30` in your motion config to exclude the outer 30 pixels
on all four sides. At 640 × 360, this leaves a central 580 × 300 detection zone.
The supplied config uses 30; older configs without this key retain full-frame
detection. Use 0 to disable exclusion, or increase it to ignore a wider border
(allowed range: 0–179 pixels).

```bash
python motion_detector.py --ignore-border 30
```

Restart after changing the config. Only motion inside the central zone counts
towards the minimum contour area. Motion entirely outside it cannot trigger a
capture; objects crossing into it can. The zone is cropped before Gaussian blur
to prevent edge movement leaking into detection. Saved JPEGs still show the full
frame, with motion boxes at the correct positions. Each capture's JSON settings
include `ignore_border`. This option applies to motion detection, not the separate
YOLO cat detector.

## Other cameras and saved images

Capture is separated into `frame_sources.py`. Every source supplies BGR images at
640 × 360 to the same motion detector. Picamera2 is imported only when using the Pi
camera; RTSP and saved images need OpenCV but do not need Pi camera hardware.

For your outdoor RTSP camera, set `"source": "rtsp"` in a copy of the config,
or use the command-line option below. Obtain the exact RTSP path from the camera
manufacturer; paths differ between models. Supply the URL through an environment
variable, so it is not written into config files or capture metadata:

```bash
read -r -s -p "Camera RTSP URL: " CAT_CAMERA_RTSP_URL
echo
export CAT_CAMERA_RTSP_URL
python motion_detector.py --source rtsp
```

Enter the full URL, typically `rtsp://USERNAME:PASSWORD@CAMERA_IP:554/STREAM_PATH`.
Special characters in credentials may need URL encoding. The prompt hides input
and avoids putting the URL in shell history. OpenCV/FFmpeg diagnostics can still
include connection details: redact credentials before sharing error output.

Use a low-resolution camera substream close to 640 × 360 and configure its frame
rate near the detector's FPS. Resizing reduces processing size but does not reduce
network traffic or decoding cost. A faster stream can also accumulate latency;
this prototype does not yet discard buffered frames or reconnect automatically.
Connection and read timeouts are five seconds, using OpenCV's FFmpeg backend
([OpenCV video I/O documentation](https://docs.opencv.org/4.10.0/d4/d15/group__videoio__flags__base.html)).
If the stream fails, the program exits and releases it; restart after fixing the
connection. Your OpenCV installation must include FFmpeg with RTSP support.

You can also replay a directory of JPEG/PNG images in filename order:

```bash
python motion_detector.py --source images --input-path garden-images --warmup 0 --cooldown 0
```

The directory is processed once, then the program exits. Images should form a
sequence from a fixed view for meaningful motion testing. A single image is
accepted as input, but frame differencing needs at least two frames, so a single
image produces no motion capture. Non-Pi sources are resized to 640 × 360;
different aspect ratios are stretched in this prototype.

## Planned cat recognition

### Tapo C325WB setup

In the Tapo app, open the camera settings, then **Advanced Settings → Camera
Account**. Create a dedicated camera username and password; these are separate
from your Tapo login. Find the camera IP under **Device Settings → Device Info**,
and ensure the Pi is on the same local network.

Use `rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream2` for the lower-quality stream,
or `/stream1` for high quality. Start with `/stream2` for motion testing. Enter it
at the hidden URL prompt described above, then run with `--source rtsp`.
See [TP-Link's setup guide](https://www.tp-link.com/uk/support/faq/2680/).

The C325WB V2 supports PoE; confirm the hardware version on the camera label when
checking power requirements. See [TP-Link's V2 product information](https://www.tp-link.com/us/home-networking/cloud-camera/tapo-c325wb/).

## Animal classification stage

The next processing stage will accept an image from any source and return animal
labels, confidence scores, and bounding boxes using a trained object-detection
model. This stage should also support individual images independently of motion,
so it can recognise a stationary cat. Motion can later serve as an optional way
to reduce how often the model runs. No cat-recognition model is implemented yet;
current captures record motion only and must not be treated as cat detections.

## Testing and limitations

Start with a fixed camera and a quiet garden. Check that a still scene produces few captures, walk across the view, and inspect the bounding boxes. Keep moving during the cooldown and confirm images are spaced at least that far apart. Check Ctrl+C, then restart to confirm the camera was released.

The low resolution, default 10 FPS rate, one OpenCV worker thread, and lack of a preview keep processing modest; actual CPU use and throughput need checking on the Pi. Image encoding or slow storage may reduce the achieved frame rate.

Frame differencing detects changes, including moving leaves, shadows, exposure or autofocus adjustments, rain, and camera shake. It cannot identify cats, and stationary objects stop registering as motion. Fast movement may show boxes at both the old and new positions. A NoIR camera still needs visible or infrared illumination in darkness. Tune settings in the actual garden before using results to guide later development.

Images accumulate until you remove them; the prototype has no retention policy. If imports fail, check that you activated the environment created with `--system-site-packages`. If the camera is unavailable, repeat the camera listing and capture checks above and ensure another process is not using it.
