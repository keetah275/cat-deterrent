#!/usr/bin/env python3
"""Prune old unreviewed live NO SPRAY evidence; retain decision summaries."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from review_worker import atomic_json


def plan_retention(root, policy, reviewed, now, clear_no_spray=False):
    root = root.resolve()
    candidates = []
    protected = set(reviewed)
    # A manual batch can contain a later positive verdict on the same capture.
    for log in (root / 'spray-review').glob('run_*/decisions.jsonl'):
        for line in log.read_text().splitlines():
            item = json.loads(line)
            if item.get('would_spray') is True:
                protected.add(item['event_id'])
    records = []
    for path in (root / 'spray-review' / 'live').glob('*.json'):
        if path.is_symlink():
            continue
        item = json.loads(path.read_text())
        if item.get('would_spray') is True:
            protected.add(item['event_id'])
        records.append((path, item))
    buckets = defaultdict(list)
    for path, item in records:
        event = item['event_id']
        if not re.fullmatch(r'motion_\d{8}_\d{6}_\d{6}', event):
            raise ValueError('Invalid event ID')
        if item.get('would_spray') is not False or event in protected or item.get('complete_burst') is not True:
            continue
        # Use analysis time for the grace period so backfilled images get review time.
        evaluated = datetime.fromisoformat(item['evaluated_at'])
        if evaluated.tzinfo is None:
            raise ValueError('Decision timestamp must include a timezone')
        bucket = (item['source_directory'], event.rsplit('_', 2)[0] + '_' + event.split('_')[2][:2])  # one bucket per capture hour
        buckets[bucket].append((path, item, evaluated))
    cutoff = now - timedelta(hours=policy['no_spray_hours'])
    for entries in buckets.values():
        # Stable sample selection, independent of file listing order.
        entries.sort(key=lambda entry: hashlib.sha256(entry[1]['event_id'].encode()).hexdigest())
        sampled = set() if clear_no_spray else {path for path, _, _ in entries[:policy['samples_per_hour']]}
        for path, item, evaluated in entries:
            if path in sampled or (not clear_no_spray and evaluated >= cutoff) or item.get('evidence_retained') is False:
                continue
            event = item['event_id']
            source = Path(item['source_directory'])
            allowed_capture_roots = [root / 'captures', root / 'captures-tc41']
            if source.name != 'raw' or not any(source.resolve().is_relative_to(r) and r.resolve() == r for r in allowed_capture_roots):
                raise ValueError('Unexpected capture directory')
            files = []
            # Keep completion markers and decision JSON for restart idempotence.
            for ext in ('jpg', 'json'):
                files.append(source.parent / f'{event}.{ext}')
            for frame in item['evidence']:
                index = frame['burst_index']
                if type(index) is not int or not 1 <= index <= 20:
                    raise ValueError('Invalid frame index')
                original = source / f'{event}_burst_{index:02d}.jpg'
                for candidate in (original, original.with_suffix('.json')):
                    files.append(candidate)
                # Annotated live reports must belong to this decision's job directory.
                report = Path(frame['report_file'])
                expected = root / 'cat-results' / 'live' / path.stem
                if report.parent != expected or report.name != original.with_suffix('.json').name:
                    raise ValueError('Unexpected report location')
                files.extend([report, report.with_suffix('.jpg')])
            for candidate in files:
                if candidate.is_symlink() or candidate.resolve() != candidate:
                    raise ValueError('Symlink evidence is not eligible for deletion')
            candidates.append((path, item, files))
    return candidates


def run_retention(root, policy, feedback_db, apply=False, now=None, clear_no_spray=False):
    if type(policy.get('no_spray_hours')) not in (int, float) or not 1 <= policy['no_spray_hours'] <= 87600:
        raise ValueError('no_spray_hours must be between 1 and 87600')
    if type(policy.get('samples_per_hour')) is not int or not 0 <= policy['samples_per_hour'] <= 100:
        raise ValueError('samples_per_hour must be 0–100')
    # Never interpret inaccessible feedback as no feedback. Lock out concurrent
    # feedback writes during planning/deletion, so committed reviews are protected.
    connection = sqlite3.connect(feedback_db.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        connection.execute('BEGIN IMMEDIATE')
        reviewed = {row[0] for row in connection.execute('SELECT DISTINCT event_id FROM feedback')}
        now = now or datetime.now(timezone.utc)
        plan = plan_retention(root, policy, reviewed, now, clear_no_spray)
        count = 0
        for path, item, files in plan:
            existing = [p for p in files if p.is_file()]
            if apply:
                for target in existing:
                    target.unlink()
                item.update(evidence_retained=False, evidence_removed_at=now.isoformat(), retention_policy=policy)
                before = path.stat()
                atomic_json(path, item)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            count += len(existing)
            print(f"{'Pruned' if apply else 'Would prune'} {item['event_id']}: {len(existing)} files", flush=True)
        print(f"{'Removed' if apply else 'Preview only:'} {count} files across {len(plan)} events. Summaries retained.", flush=True)
        return len(plan)
    finally:
        connection.rollback()
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('retention_config.json'))
    parser.add_argument('--feedback-db', type=Path, default=Path('/var/lib/garden-dashboard/feedback.sqlite3'))
    parser.add_argument('--apply', action='store_true', help='delete eligible evidence; default previews only')
    parser.add_argument('--clear-no-spray', action='store_true',
                        help='one-off clear: ignore age and sampling; still protect positive and reviewed events')
    args = parser.parse_args()
    try:
        run_retention(args.root.resolve(), json.loads(args.config.read_text()), args.feedback_db, args.apply,
                      clear_no_spray=args.clear_no_spray)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, f'Retention stopped: {error}\n')


if __name__ == '__main__':
    main()
