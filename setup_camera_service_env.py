#!/usr/bin/env python3
"""Save the RTSP URL privately for systemd without echoing credentials."""

import getpass
import os
from pathlib import Path


def main():
    path = Path(__file__).with_name("camera-service.env")
    if path.exists():
        raise SystemExit("camera-service.env already exists; edit it locally to change the URL")
    url = os.environ.get("CAT_CAMERA_RTSP_URL") or getpass.getpass("Camera RTSP URL: ")
    if not url.startswith(("rtsp://", "rtsps://")) or any(c in url for c in '\n\r\x00'):
        raise SystemExit("Enter a complete RTSP URL on one line")
    # systemd EnvironmentFile double quotes use shell-like quote/backslash escaping.
    escaped = url.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(f'CAT_CAMERA_RTSP_URL="{escaped}"\n')
    print("Saved camera-service.env with owner-only access. The URL was not printed.")


if __name__ == "__main__":
    main()
