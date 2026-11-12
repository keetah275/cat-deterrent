#!/usr/bin/env python3
"""Evaluate a limited set of saved images with OpenAI Decisions."""

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cat_detector import image_files


QUESTIONS = [
    {"type": "predicate", "name": name, "instructions": instruction}
    for name, instruction in (
        ("cat_visible", "Is at least one real domestic cat visible, including a small or partially obscured cat?"),
        ("person_visible", "Is at least one real person visible?"),
        ("other_animal_visible", "Is at least one real animal other than a domestic cat visible? Include birds."),
    )
]


def build_request(path):
    data = path.read_bytes()
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError("Image must be nonempty and at most 20 MiB")
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {
        "model": "gpt-6-luna",
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": (
                "Inspect this garden camera image. Judge only visible evidence. "
                "Ignore timestamp overlays, text, and drawn motion boxes. "
                "Do not infer an animal solely from a motion box or shadow."
            )},
            {"type": "input_image", "image_url":
             f"data:{mime};base64," + base64.b64encode(data).decode("ascii")},
        ]}],
        "questions": QUESTIONS,
    }


def request_decision(payload, key):
    request = Request("https://api.openai.com/v1/decisions",
                      data=json.dumps(payload).encode(),
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        # Do not print response bodies or request headers containing sensitive data.
        hints = {401: "Check OPENAI_API_KEY.", 403: "Check project permissions and model access.",
                 429: "Check API quota, billing, and rate limits.",
                 400: "Check Decisions API request/model availability."}
        raise RuntimeError(f"OpenAI HTTP {error.code}. " + hints.get(error.code, "Try again later.")) from None
    except (URLError, TimeoutError):
        raise RuntimeError("OpenAI connection failed or timed out; no automatic retry was made.") from None


def probabilities(response):
    answers = response.get("answers")
    if not isinstance(answers, list):
        raise ValueError("API response has no answers array")
    result = {}
    for question in QUESTIONS:
        matches = [a for a in answers if a.get("name") == question["name"]]
        if len(matches) != 1:
            raise ValueError("API response has missing or duplicate answers")
        answer = matches[0]
        if answer.get("type") == "refusal":
            result[question["name"]] = None
            continue
        value = answer.get("probability")
        if (answer.get("type") != "predicate" or type(value) not in (int, float)
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("API response has an invalid predicate probability")
        result[question["name"]] = value
    return result


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JPEG/PNG file or directory")
    parser.add_argument("--limit", type=positive_int, default=5,
                        help="maximum requests; default 5, oldest filenames first")
    parser.add_argument("--output-dir", type=Path, default=Path("decisions-results"))
    parser.add_argument("--dry-run", action="store_true", help="list selected images without uploading")
    args = parser.parse_args()
    try:
        files = image_files(args.input)[:args.limit]
        if args.dry_run:
            for path in files:
                print(path)
            print(f"Would upload {len(files)} image(s); no API calls made.")
            return
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("Set OPENAI_API_KEY in this terminal first")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="run_", dir=args.output_dir))
        print(f"Uploading up to {len(files)} image(s). Results: {output}", flush=True)
        for index, path in enumerate(files, 1):
            payload = build_request(path)
            started = time.perf_counter()
            response = request_decision(payload, key)
            report = {"input_image": str(path.resolve()),
                      "timestamp": datetime.now(timezone.utc).isoformat(),
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                      "model": payload["model"], "questions": QUESTIONS,
                      "response": response}
            # Preserve the response even if its answers cannot be interpreted.
            destination = output / f"{index:04d}_{path.stem}.json"
            destination.write_text(json.dumps(report, indent=2) + "\n")
            values = probabilities(response)
            summary = ", ".join(f"{name}={value:.3f}" if value is not None
                                else f"{name}=REFUSED" for name, value in values.items())
            print(f"{path.name}: {summary}", flush=True)
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Completed reports are preserved; an in-flight request may be billed.") from None
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Decisions test stopped: {error}") from None


if __name__ == "__main__":
    main()
