#!/usr/bin/env python3
"""Preview or delete motion captures by local filename timestamp (start inclusive, end exclusive)."""

import argparse
from datetime import datetime
from pathlib import Path
import re


PATTERN = re.compile(r"motion_(\d{8}_\d{6}_\d{6})(?:_burst_\d+)?\.(?:jpg|json)$")


def selected_files(root, start, end):
    for directory in (root, root / "raw"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            match = PATTERN.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or not match:
                continue
            try:
                timestamp = datetime.strptime(match[1], "%Y%m%d_%H%M%S_%f")
            except ValueError:
                continue
            if start <= timestamp < end:
                yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--start", required=True, help='local time: "2026-11-25 16:00"')
    parser.add_argument("--end", required=True, help='local time: "2026-11-26 08:00"')
    parser.add_argument("--delete", action="store_true", help="actually delete; default is preview")
    args = parser.parse_args()
    try:
        start = datetime.strptime(args.start, "%Y-%m-%d %H:%M")
        end = datetime.strptime(args.end, "%Y-%m-%d %H:%M")
    except ValueError:
        parser.error("times must use YYYY-MM-DD HH:MM")
    if start >= end or not args.directory.is_dir():
        parser.error("directory must exist and end must be after start")
    raw = args.directory / "raw"
    if args.directory.is_symlink() or raw.is_symlink():
        parser.error("capture directories must not be symlinks")
    paths = list(selected_files(args.directory, start, end))
    for path in paths:
        if args.delete:
            path.unlink()
        print(f"{'Deleted' if args.delete else 'Would delete'}: {path}")
    print(f"{len(paths)} files {'deleted' if args.delete else 'matched; preview only'}.")


if __name__ == "__main__":
    main()
