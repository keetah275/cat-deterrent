#!/usr/bin/env python3
"""Evaluate saved garden images with YOLO; no camera or actuator control."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import time


DEFAULTS = {"model": "yolo26n.pt", "confidence": 0.5, "image_size": 640,
            "output_dir": "cat-results"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="image or directory of JPEG/PNG images")
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).with_name("cat_config.json"))
    # Read config only after normal argument parsing, so --help needs no files.
    parser.add_argument("--model", help="YOLO weights file or exported NCNN directory")
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--image-size", type=int)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        settings = json.loads(args.config.read_text())
    except (OSError, ValueError) as error:
        parser.error(f"cannot read config: {error}")
    if not isinstance(settings, dict) or settings.keys() - DEFAULTS.keys():
        parser.error("config keys must be: " + ", ".join(DEFAULTS))
    settings = DEFAULTS | settings
    for key in DEFAULTS:
        override = getattr(args, key)
        if override is not None:
            settings[key] = str(override) if isinstance(override, Path) else override
    for key in ("model", "output_dir"):
        if not isinstance(settings[key], str) or not settings[key].strip():
            parser.error(f"{key} must be a non-empty string")
    confidence = settings["confidence"]
    if (type(confidence) not in (int, float) or not math.isfinite(confidence)
            or not 0 < confidence <= 1):
        parser.error("confidence must be greater than 0 and at most 1")
    size = settings["image_size"]
    if type(size) is not int or size < 32 or size % 32:
        parser.error("image_size must be a positive multiple of 32 (at least 32)")
    return args, settings


def image_files(path):
    supported = {".jpg", ".jpeg", ".png"}
    if path.is_file() and path.suffix.lower() in supported:
        return [path]
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in supported)
        if files:
            return files
    raise ValueError("Input must be a JPEG/PNG image or a directory containing images")


class CatDetector:
    """Reusable detection stage: accepts original-resolution OpenCV BGR frames."""
    def __init__(self, settings):
        from ultralytics import YOLO
        self.settings = dict(settings)
        self.model = YOLO(settings["model"], task="detect")

    def detect(self, frame):
        started = time.perf_counter()
        result = self.model.predict(
            source=frame, conf=self.settings["confidence"],
            imgsz=self.settings["image_size"], device="cpu", verbose=False)[0]
        elapsed_ms = (time.perf_counter() - started) * 1000
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls.item())
                detections.append({
                    "class_id": class_id, "label": result.names[class_id],
                    "confidence": float(box.conf.item()),
                    "box_xyxy": [float(v) for v in box.xyxy[0].tolist()],
                })
        return result.plot(), {
            "cat_detected": any(d["label"].lower() == "cat" for d in detections),
            "detections": detections, "inference_time_ms": round(elapsed_ms, 2),
        }


def process_images(cv2, detector, files, settings, config_path):
    # A separate directory per run preserves comparisons and avoids overwriting inputs.
    output_root = Path(settings["output_dir"])
    output_root.mkdir(parents=True, exist_ok=True)
    import tempfile
    output = Path(tempfile.mkdtemp(prefix="run_" + datetime.now().strftime("%Y%m%d_%H%M%S_"),
                                   dir=output_root))
    failures = 0
    for index, source in enumerate(files, 1):
        frame = cv2.imread(str(source))
        if frame is None:
            print(f"Skipping unreadable image: {source}", flush=True)
            failures += 1
            continue
        annotated, report = detector.detect(frame)
        destination = output / f"{index:04d}_{source.stem}.jpg"
        if not cv2.imwrite(str(destination), annotated, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise OSError(f"Could not save {destination}")
        report.update({
            "timestamp": datetime.now().astimezone().isoformat(),
            "input_image": str(source.resolve()), "output_image": destination.name,
            "original_size": {"width": frame.shape[1], "height": frame.shape[0]},
            "settings": dict(settings), "config_file": str(config_path.resolve()),
            "coordinate_system": "xyxy pixels in original image",
        })
        destination.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        verdict = "CAT DETECTED" if report["cat_detected"] else "No cat detected"
        print(f"{source.name}: {verdict} ({report['inference_time_ms']:.0f} ms) → {destination}",
              flush=True)
    return failures


def main():
    args, settings = parse_args()
    try:
        files = image_files(args.input)
        import cv2
        cv2.setNumThreads(1)
        detector = CatDetector(settings)
        failures = process_images(cv2, detector, files, settings, args.config)
        if failures:
            raise SystemExit(f"Finished with {failures} unreadable image(s)")
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Cat detection failed: {error}") from None


if __name__ == "__main__":
    main()
