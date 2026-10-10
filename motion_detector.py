#!/usr/bin/env python3
"""Headless garden motion detection; no animal classification or actuator control."""

import argparse
from datetime import datetime
from pathlib import Path
import time


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-area", type=float, default=600,
                        help="minimum contour area in pixels (default: 600)")
    parser.add_argument("--cooldown", type=float, default=10,
                        help="seconds between saved detections (default: 10)")
    parser.add_argument("--fps", type=float, default=10,
                        help="capture/processing rate, 1–30 (default: 10)")
    parser.add_argument("--threshold", type=int, default=25,
                        help="greyscale difference threshold, 1–255 (default: 25)")
    parser.add_argument("--warmup", type=float, default=3,
                        help="camera settling time in seconds (default: 3)")
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    args = parser.parse_args()
    import math
    if not all(math.isfinite(value) for value in
               (args.min_area, args.cooldown, args.fps, args.warmup)):
        parser.error("numeric settings must be finite")
    if args.min_area <= 0 or args.cooldown < 0 or args.warmup < 0:
        parser.error("min-area must be positive; cooldown and warmup cannot be negative")
    if not 1 <= args.fps <= 30 or not 1 <= args.threshold <= 255:
        parser.error("fps must be 1–30 and threshold must be 1–255")
    return args


def moving_regions(cv2, previous, current, threshold, min_area):
    """Return bounding boxes for significant changes between blurred grey frames."""
    difference = cv2.absdiff(previous, current)
    mask = cv2.threshold(difference, threshold, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.dilate(mask, None, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(contour) for contour in contours
            if cv2.contourArea(contour) >= min_area]


def main():
    args = parse_args()
    # Delayed imports allow --help to work even away from the Raspberry Pi.
    import cv2
    from picamera2 import Picamera2

    cv2.setNumThreads(1)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    camera = Picamera2()
    try:
        # Picamera2 RGB888 yields BGR bytes, matching OpenCV's colour order.
        config = camera.create_video_configuration(
            main={"size": (640, 360), "format": "RGB888"},
            controls={"FrameRate": args.fps}, buffer_count=4,
        )
        camera.configure(config)
        camera.start()
        print(f"Camera warming up for {args.warmup:g}s...", flush=True)
        time.sleep(args.warmup)
        previous = None
        last_saved = float("-inf")
        interval = 1 / args.fps
        print("Watching for motion at 640 × 360. Press Ctrl+C to stop.", flush=True)
        while True:
            loop_start = time.monotonic()
            frame = camera.capture_array("main")
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(grey, (21, 21), 0)
            boxes = [] if previous is None else moving_regions(
                cv2, previous, blurred, args.threshold, args.min_area)
            previous = blurred
            now = time.monotonic()
            if boxes and now - last_saved >= args.cooldown:
                timestamp = datetime.now().astimezone()
                print(f"{timestamp.isoformat(timespec='seconds')} Motion detected "
                      f"({len(boxes)} region(s))", flush=True)
                for x, y, width, height in boxes:
                    cv2.rectangle(frame, (x, y), (x + width, y + height),
                                  (0, 255, 0), 2)
                filename = args.output_dir / timestamp.strftime(
                    "motion_%Y%m%d_%H%M%S_%f.jpg")
                if not cv2.imwrite(str(filename), frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, 90]):
                    raise OSError(f"Could not save image: {filename}")
                last_saved = time.monotonic()
                print(f"Saved {filename}", flush=True)
            # Continue updating the previous frame throughout the cooldown.
            time.sleep(max(0, interval - (time.monotonic() - loop_start)))
    except KeyboardInterrupt:
        print("\nStopping motion detection.", flush=True)
    finally:
        camera.close()


if __name__ == "__main__":
    main()
