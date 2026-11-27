#!/usr/bin/env python3
"""Log hypothetical spray decisions from saved YOLO reports. No hardware control."""

import argparse
import csv
from datetime import datetime
import html
import json
import math
from pathlib import Path
import re
import tempfile


BURST = re.compile(r"(motion_\d{8}_\d{6}_\d{6})_burst_(\d+)\.(?:jpg|png|jpeg)$", re.I)
DEFAULTS = {"animal_confidence": 0.25, "person_confidence": 0.25,
            "min_animal_frames": 2, "expected_frames": 5}


def decide(frames, settings):
    animals = []
    people = []
    indices = []
    invalid = False
    for frame in frames:
        indices.append(frame["burst_index"])
        report = frame["report"]
        try:
            # A higher inference threshold could hide a person below that cutoff.
            cutoff = report["settings"]["confidence"]
            if not math.isfinite(cutoff) or cutoff > min(settings["person_confidence"], settings["animal_confidence"]):
                invalid = True
            detections = report["detections"]
            if not isinstance(detections, list):
                raise ValueError("invalid detections")
            animal = person = False
            for detection in detections:
                label = detection["label"].lower()
                confidence = detection["confidence"]
                if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("invalid confidence")
                animal |= label in {"cat", "dog"} and confidence >= settings["animal_confidence"]
                person |= label == "person" and confidence >= settings["person_confidence"]
            if animal:
                animals.append(frame["burst_index"])
            if person:
                people.append(frame["burst_index"])
        except (KeyError, TypeError, ValueError):
            invalid = True
    complete = sorted(indices) == list(range(1, settings["expected_frames"] + 1))
    if people:
        decision, reason = False, "person_detected"
    elif invalid:
        decision, reason = False, "invalid_or_insufficient_detection_reports"
    elif not complete:
        decision, reason = False, "incomplete_or_duplicate_burst"
    elif len(animals) < settings["min_animal_frames"]:
        decision, reason = False, "insufficient_cat_or_dog_frames"
    else:
        decision, reason = True, "cat_or_dog_confirmed_no_person_detected"
    return {"would_spray": decision, "reason": reason,
            "animal_frame_indices": animals, "person_frame_indices": people,
            "complete_burst": complete}


def review_run(source, output_root, settings):
    groups = {}
    for path in sorted(source.glob("*.json")):
        report = json.loads(path.read_text())
        match = BURST.fullmatch(Path(report.get("input_image", "")).name)
        if not match:
            raise ValueError(f"Not a raw burst report: {path.name}; use YOLO results from raw/ images")
        # Keep identically named events from different source directories separate.
        key = (str(Path(report["input_image"]).parent), match[1])
        groups.setdefault(key, []).append({"burst_index": int(match[2]), "report": report,
                                            "report_file": str(path.resolve())})
    if not groups:
        raise ValueError("No YOLO burst reports found")
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run_" + datetime.now().strftime("%Y%m%d_%H%M%S_"), dir=output_root))
    rows = []
    with (output / "decisions.jsonl").open("w") as log:
        for (directory, event), frames in sorted(groups.items()):
            result = {"event_id": event, "source_directory": directory,
                      "evaluated_at": datetime.now().astimezone().isoformat(),
                      "mode": "logging_only", "policy": settings, **decide(frames, settings),
                      "evidence": frames}
            log.write(json.dumps(result) + "\n")
            links = []
            for frame in frames:
                report = frame["report"]
                annotated = Path(frame["report_file"]).parent / report["output_image"]
                original = Path(report["input_image"])
                links.append(f'Frame {frame["burst_index"]}: '
                             f'<a href="{html.escape(annotated.resolve().as_uri(), quote=True)}">annotated</a> / '
                             f'<a href="{html.escape(original.resolve().as_uri(), quote=True)}">original</a>')
            rows.append((result, "<br>".join(links)))
    with (output / "decisions.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["event_id", "would_spray", "reason", "animal_frames", "person_frames", "review_verdict", "review_notes"])
        for result, _ in rows:
            writer.writerow([result["event_id"], result["would_spray"], result["reason"],
                             len(result["animal_frame_indices"]), len(result["person_frame_indices"]), "", ""])
    document = ['<!doctype html><meta charset="utf-8"><title>Spray decision review</title>',
                '<h1>Logging-only spray decisions</h1><p>No hardware was activated. ',
                'Model detections can miss people or misidentify objects. Review evidence manually.</p>',
                '<table border="1" cellpadding="8"><tr><th>Event</th><th>Decision</th><th>Reason</th><th>Evidence</th></tr>']
    for result, links in rows:
        document.append(f'<tr><td>{html.escape(result["event_id"])}</td><td>'
                        f'{"WOULD SPRAY" if result["would_spray"] else "NO SPRAY"}</td>'
                        f'<td>{html.escape(result["reason"])}</td><td>{links}</td></tr>')
    (output / "review.html").write_text("\n".join(document) + "</table>")
    print(f"Logged {len(rows)} bursts; {sum(r['would_spray'] for r, _ in rows)} WOULD SPRAY. Review: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, help="one cat-results/run_... directory")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("spray_policy.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("spray-review"))
    args = parser.parse_args()
    try:
        settings = json.loads(args.config.read_text())
        if not isinstance(settings, dict) or settings.keys() != DEFAULTS.keys():
            raise ValueError("Policy must contain exactly: " + ", ".join(DEFAULTS))
        for key in ("animal_confidence", "person_confidence"):
            value = settings[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"Invalid {key}")
        for key in ("min_animal_frames", "expected_frames"):
            if type(settings[key]) is not int or settings[key] < 1:
                raise ValueError(f"Invalid {key}")
        if settings["min_animal_frames"] > settings["expected_frames"]:
            raise ValueError("min_animal_frames exceeds expected_frames")
        if not args.reports.is_dir():
            raise ValueError("Reports directory does not exist")
        review_run(args.reports, args.output_dir, settings)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Decision review failed: {error}\n")


if __name__ == "__main__":
    main()
