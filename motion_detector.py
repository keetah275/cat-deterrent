#!/usr/bin/env python3
"""Headless garden motion detection; no animal classification or actuator control."""

import argparse
import json
from datetime import datetime
from pathlib import Path
import time


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).with_name("config.json"),
                        help="JSON settings file (default: config.json beside script)")
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", type=Path,
                               default=Path(__file__).with_name("config.json"))
    config_args, _ = config_parser.parse_known_args()
    try:
        settings = json.loads(config_args.config.read_text())
    except (OSError, ValueError) as error:
        parser.error(f"cannot load config {config_args.config}: {error}")
    allowed = {"min_area", "cooldown", "fps", "threshold", "warmup", "output_dir", "source", "input_path", "ignore_border", "detection_polygon", "motion_frames", "burst_frames", "burst_interval"}
    if not isinstance(settings, dict) or settings.keys() - allowed:
        parser.error("config must be an object containing only: " + ", ".join(sorted(allowed)))
    for key, value in settings.items():
        if key == "detection_polygon":
            valid = (isinstance(value, list) and (not value or len(value) >= 3)
                     and all(isinstance(p, list) and len(p) == 2
                             and all(type(v) is int for v in p)
                             and 0 <= p[0] < 640 and 0 <= p[1] < 360 for p in value))
        elif key in {"output_dir", "source", "input_path"}:
            valid = isinstance(value, str) and bool(value.strip())
        elif key in {"threshold", "ignore_border", "motion_frames", "burst_frames"}:
            valid = type(value) is int
        else:
            valid = type(value) in (int, float)
        if not valid:
            parser.error(f"invalid config value for {key}")
    parser.add_argument("--min-area", type=float, default=600,
                        help="minimum contour area in pixels (default: 600)")
    parser.add_argument("--cooldown", type=float, default=10,
                        help="seconds between saved detections (default: 10)")
    parser.add_argument("--fps", type=float, default=10,
                        help="capture/processing rate, 1–30 (default: 10)")
    parser.add_argument("--threshold", type=int, default=25,
                        help="greyscale difference threshold, 1–255 (default: 25)")
    parser.add_argument("--ignore-border", type=int, default=0,
                        help="ignore this many pixels on every edge, 0–179 (default: 0)")
    parser.add_argument("--motion-frames", type=int, default=1,
                        help="consecutive frames with motion before saving (default: 1)")
    parser.add_argument("--burst-frames", type=int, default=5,
                        help="unannotated native frames per trigger, 1–20 (default: 5)")
    parser.add_argument("--burst-interval", type=float, default=0.2,
                        help="minimum seconds between burst frames (default: 0.2)")
    parser.add_argument("--warmup", type=float, default=3,
                        help="camera settling time in seconds (default: 3)")
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--source", choices=("picamera2", "rtsp", "images"),
                        default="picamera2", help="frame source (default: picamera2)")
    parser.add_argument("--input-path", type=Path, default=Path("images"),
                        help="image file or directory for the images source")
    parser.set_defaults(detection_polygon=[])
    parser.set_defaults(**settings)
    args = parser.parse_args()
    if args.source not in {"picamera2", "rtsp", "images"}:
        parser.error("source must be picamera2, rtsp, or images")
    import math
    if not all(math.isfinite(value) for value in
               (args.min_area, args.cooldown, args.fps, args.warmup, args.burst_interval)):
        parser.error("numeric settings must be finite")
    if args.min_area <= 0 or args.cooldown < 0 or args.warmup < 0:
        parser.error("min-area must be positive; cooldown and warmup cannot be negative")
    if not 1 <= args.fps <= 30 or not 1 <= args.threshold <= 255:
        parser.error("fps must be 1–30 and threshold must be 1–255")
    if not 0 <= args.ignore_border < 180:
        parser.error("ignore-border must be between 0 and 179 pixels")
    if not 1 <= args.motion_frames <= 100:
        parser.error("motion-frames must be between 1 and 100")
    if not 1 <= args.burst_frames <= 20 or args.burst_interval < 0:
        parser.error("burst-frames must be 1–20; burst-interval cannot be negative")
    return args


def save_capture_metadata(filename, args, timestamp, boxes, extra=None):
    """Save effective settings, including CLI overrides, beside the JPEG."""
    metadata = {
        "timestamp": timestamp.isoformat(),
        "image": filename.name,
        "config_file": str(args.config.resolve()),
        "settings": {key: str(value) if isinstance(value, Path) else value
                     for key, value in vars(args).items() if key != "config"},
        "processing": {"resolution": [640, 360], "method": "frame_differencing",
                       "gaussian_blur_kernel": [21, 21], "dilation_iterations": 2,
                       "jpeg_quality": 90},
        "bounding_boxes": [dict(zip(("x", "y", "width", "height"), box))
                           for box in boxes],
    }
    if extra:
        metadata.update(extra)
    filename.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")


def detection_mask(cv2, shape, border, polygon):
    """Mask in cropped-frame coordinates; vertices use full 640 x 360 pixels."""
    import numpy as np
    height, width = shape[:2]
    mask = np.full((height - 2 * border, width - 2 * border), 255, dtype=np.uint8)
    if polygon:
        mask[:] = 0
        points = np.array(polygon, dtype=np.int32) - border
        if cv2.contourArea(points) == 0:
            raise ValueError("detection_polygon must have nonzero area")
        cv2.fillPoly(mask, [points], 255)
        if cv2.countNonZero(mask) == 0:
            raise ValueError("detection_polygon does not overlap the area inside ignore_border")
    return mask


def prepare_frame(cv2, frame, border, mask):
    height, width = frame.shape[:2]
    zone = frame[border:height - border, border:width - border]
    grey = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    # Mask before blur so excluded plant movement cannot bleed into the zone.
    grey = cv2.bitwise_and(grey, mask)
    return cv2.GaussianBlur(grey, (21, 21), 0)


def moving_regions(cv2, previous, current, threshold, min_area, zone_mask=None):
    """Return bounding boxes for significant changes between blurred grey frames."""
    difference = cv2.absdiff(previous, current)
    mask = cv2.threshold(difference, threshold, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.dilate(mask, None, iterations=2)
    if zone_mask is not None:
        mask = cv2.bitwise_and(mask, zone_mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(contour) for contour in contours
            if cv2.contourArea(contour) >= min_area]


def save_raw_frame(cv2, native, args, timestamp, event_id, index):
    directory = args.output_dir / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / f"{event_id}_burst_{index:02d}.jpg"
    if not cv2.imwrite(str(filename), native, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise OSError(f"Could not save image: {filename}")
    save_capture_metadata(filename, args, timestamp, [], {
        "event_id": event_id, "burst_index": index,
        "image_size": {"width": native.shape[1], "height": native.shape[0]},
        "annotated": False,
        "processing": {"resolution": [native.shape[1], native.shape[0]],
                       "method": "unannotated_source_frame", "jpeg_quality": 95},
        "note": "Native received frame; motion processing uses 640 x 360. No boxes drawn.",
    })
    print(f"Saved raw {filename}", flush=True)


class MotionConfirmation:
    """Require consecutive positive frames, resetting immediately on a quiet frame."""
    def __init__(self, required):
        self.required = required
        self.count = 0

    def update(self, boxes):
        self.count = min(self.count + 1, self.required) if boxes else 0
        return self.count >= self.required


def main():
    args = parse_args()
    # Delayed imports allow --help to work even away from the Raspberry Pi.
    import cv2
    from frame_sources import open_source

    cv2.setNumThreads(1)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    camera = None
    try:
        camera = open_source(cv2, args)
        print(f"Camera warming up for {args.warmup:g}s...", flush=True)
        time.sleep(args.warmup)
        previous = None
        burst_remaining = 0
        burst_index = 0
        next_burst = 0
        event_id = None
        confirmation = MotionConfirmation(args.motion_frames)
        zone_mask = None
        last_saved = float("-inf")
        interval = 1 / args.fps
        print("Watching for motion at 640 × 360. Press Ctrl+C to stop.", flush=True)
        while True:
            loop_start = time.monotonic()
            native = camera.read_native() if hasattr(camera, "read_native") else camera.read()
            if native is None:
                print("End of image input.", flush=True)
                break
            frame = cv2.resize(native, (640, 360))
            border = args.ignore_border
            if zone_mask is None:
                zone_mask = detection_mask(cv2, frame.shape, border, args.detection_polygon)
            blurred = prepare_frame(cv2, frame, border, zone_mask)
            boxes = [] if previous is None else moving_regions(
                cv2, previous, blurred, args.threshold, args.min_area, zone_mask)
            boxes = [(x + border, y + border, w, h) for x, y, w, h in boxes]
            previous = blurred
            now = time.monotonic()
            confirmed = confirmation.update(boxes)
            if confirmed and not burst_remaining and now - last_saved >= args.cooldown:
                timestamp = datetime.now().astimezone()
                print(f"{timestamp.isoformat(timespec='seconds')} Motion detected "
                      f"({len(boxes)} region(s))", flush=True)
                event_id = timestamp.strftime("motion_%Y%m%d_%H%M%S_%f")
                burst_remaining = args.burst_frames
                burst_index = 0
                next_burst = now
                for x, y, width, height in boxes:
                    cv2.rectangle(frame, (x, y), (x + width, y + height),
                                  (0, 255, 0), 2)
                filename = args.output_dir / timestamp.strftime(
                    "motion_%Y%m%d_%H%M%S_%f.jpg")
                if not cv2.imwrite(str(filename), frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, 90]):
                    raise OSError(f"Could not save image: {filename}")
                save_capture_metadata(filename, args, timestamp, boxes)
                last_saved = time.monotonic()
                print(f"Saved {filename}", flush=True)
            if burst_remaining and time.monotonic() >= next_burst:
                burst_index += 1
                save_raw_frame(cv2, native, args, datetime.now().astimezone(), event_id, burst_index)
                burst_remaining -= 1
                if not burst_remaining:
                    # Publish only after every JPEG and sidecar has finished writing.
                    manifest = args.output_dir / "raw" / f"{event_id}.complete.json"
                    temporary = manifest.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps({"event_id": event_id, "frames": [
                        f"{event_id}_burst_{i:02d}.jpg" for i in range(1, burst_index + 1)]}) + "\n")
                    temporary.replace(manifest)
                next_burst = time.monotonic() + args.burst_interval
            # Continue updating the previous frame throughout the cooldown.
            time.sleep(max(0, interval - (time.monotonic() - loop_start)))
    except KeyboardInterrupt:
        print("\nStopping motion detection.", flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Detection stopped: {error}") from None
    finally:
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
