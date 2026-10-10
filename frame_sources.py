"""Camera-independent sources yielding OpenCV BGR frames at 640 × 360."""

import os
from pathlib import Path


class PiCameraSource:
    def __init__(self, cv2, fps):
        from picamera2 import Picamera2
        self.camera = Picamera2()
        try:
            self.camera.configure(self.camera.create_video_configuration(
                main={"size": (640, 360), "format": "RGB888"},
                controls={"FrameRate": fps}, buffer_count=4))
            self.camera.start()
        except BaseException:
            self.camera.close()
            raise

    def read(self):
        return self.camera.capture_array("main")

    def close(self):
        self.camera.close()


class StreamSource:
    def __init__(self, cv2):
        self.cv2 = cv2
        url = os.environ.get("CAT_CAMERA_RTSP_URL", "")
        if not url.startswith(("rtsp://", "rtsps://")):
            raise ValueError("Set CAT_CAMERA_RTSP_URL to your camera's RTSP URL")
        # Timeouts are open-time parameters supported by the FFmpeg backend.
        self.capture = cv2.VideoCapture()
        try:
            opened = self.capture.open(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
            if not opened:
                raise RuntimeError("Cannot open RTSP stream; check camera, network and credentials")
        except BaseException:
            self.capture.release()
            raise

    def read(self):
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError("RTSP frame unavailable; check connection and restart the detector")
        return self.cv2.resize(frame, (640, 360))

    def close(self):
        self.capture.release()


class ImageSource:
    """Read one image or a directory of images once, in filename order."""
    def __init__(self, cv2, path):
        self.cv2 = cv2
        path = Path(path)
        if not path.exists():
            raise ValueError("Image input does not exist")
        files = sorted(p for p in path.iterdir()
                       if p.suffix.lower() in {".jpg", ".jpeg", ".png"}) if path.is_dir() else [path]
        if not files:
            raise ValueError("Image directory contains no JPEG or PNG files")
        self.files = iter(files)

    def read(self):
        path = next(self.files, None)
        if path is None:
            return None
        frame = self.cv2.imread(str(path))
        if frame is None:
            raise ValueError(f"Cannot decode image: {path.name}")
        return self.cv2.resize(frame, (640, 360))

    def close(self):
        pass


def open_source(cv2, args):
    if args.source == "picamera2":
        return PiCameraSource(cv2, args.fps)
    if args.source == "rtsp":
        return StreamSource(cv2)
    return ImageSource(cv2, args.input_path)
