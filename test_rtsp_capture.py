#!/usr/bin/env python3
"""Manual RTSP diagnostic: continuously decode frames without motion processing."""

import argparse
import math
import os
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60,
                        help="test duration after opening the stream (default: 60)")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("seconds must be finite and greater than zero")
    url = os.environ.get("CAT_CAMERA_RTSP_URL", "")
    if not url.startswith(("rtsp://", "rtsps://")):
        parser.error("set CAT_CAMERA_RTSP_URL to the camera RTSP URL first")

    import cv2
    cv2.setNumThreads(1)
    capture = cv2.VideoCapture()
    frames = 0
    started = None
    status = 0
    try:
        print(f"OpenCV {cv2.__version__}; opening FFmpeg capture...", flush=True)
        for name in ("OPENCV_FFMPEG_CAPTURE_OPTIONS", "OPENCV_FFMPEG_THREADS"):
            # Do not print option values: arbitrary FFmpeg options may contain secrets.
            print(f"{name}: {'set' if name in os.environ else 'unset'}", flush=True)
        if not capture.open(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]):
            print("Could not open stream.", flush=True)
            return 1
        started = time.monotonic()
        next_report = started + 10
        print(f"Reading continuously for {args.seconds:g}s; Ctrl+C to stop.", flush=True)
        while time.monotonic() - started < args.seconds:
            ok, frame = capture.read()
            if not ok or frame is None:
                print("Frame read failed.", flush=True)
                status = 1
                break
            frames += 1
            if frames == 1:
                print(f"Native frame size: {frame.shape[1]} × {frame.shape[0]}", flush=True)
            now = time.monotonic()
            if now >= next_report:
                print(f"{now - started:.0f}s: {frames} frames received", flush=True)
                next_report = now + 10
    except KeyboardInterrupt:
        print("\nStopped early.", flush=True)
        status = 130
    finally:
        capture.release()
        if started is not None:
            elapsed = time.monotonic() - started
            print(f"Received {frames} frames in {elapsed:.1f}s "
                  f"({frames / max(elapsed, 0.001):.1f} FPS).", flush=True)
            print("Inspect console output for decoder warnings; successful reads "
                  "do not guarantee uncorrupted frames.", flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
