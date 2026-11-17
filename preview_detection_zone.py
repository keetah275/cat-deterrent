#!/usr/bin/env python3
"""Show a configured detection zone on a saved image, without starting a camera."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config-tc41.json"))
    parser.add_argument("--output", type=Path, default=Path("captures-tc41/detection-zone-preview.jpg"))
    args = parser.parse_args()
    import cv2
    import numpy as np
    from motion_detector import detection_mask
    settings = json.loads(args.config.read_text())
    frame = cv2.imread(str(args.image))
    if frame is None:
        parser.error("cannot read image")
    if args.image.resolve() == args.output.resolve():
        parser.error("output must differ from the original image")
    frame = cv2.resize(frame, (640, 360))
    border = settings.get("ignore_border", 0)
    mask = np.zeros((360, 640), dtype=np.uint8)
    mask[border:360-border, border:640-border] = detection_mask(
        cv2, frame.shape, border, settings.get("detection_polygon", []))
    frame[mask == 0] = (frame[mask == 0] * 0.3).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(frame, contours, -1, (0, 255, 255), 2)
    cv2.putText(frame, "Yellow: active zone / dark: ignored", (10, 345),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), frame):
        raise SystemExit("Could not write preview")
    print(args.output)


if __name__ == "__main__":
    main()
