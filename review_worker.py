#!/usr/bin/env python3
"""Review completed motion bursts continuously. Logging only; no actuator control."""

import argparse
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from cat_detector import CatDetector
from spray_decisions import DEFAULTS, decide


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


class Worker:
    def __init__(self, root, raw, state):
        self.root, self.raw = root.resolve(), raw.resolve()
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / 'queue.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, manifest TEXT NOT NULL, settings TEXT NOT NULL,
            policy TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER DEFAULT 0,
            next_attempt REAL DEFAULT 0)''')
        self.db.execute("UPDATE jobs SET status='pending' WHERE status='processing'")
        self.db.commit()

    def discover(self, settings, policy):
        for manifest in self.raw.glob('motion_*.complete.json'):
            key = hashlib.sha256(str(manifest.resolve()).encode()).hexdigest()
            self.db.execute('INSERT OR IGNORE INTO jobs (id,manifest,settings,policy,status) VALUES (?,?,?,?,?)',
                            (key, str(manifest.resolve()), json.dumps(settings), json.dumps(policy), 'pending'))
        self.db.commit()

    def process_one(self, cv2, detector_factory=CatDetector):
        job = self.db.execute("SELECT * FROM jobs WHERE status='pending' AND next_attempt<=? ORDER BY manifest LIMIT 1", (time.time(),)).fetchone()
        if job is None:
            return False
        self.db.execute("UPDATE jobs SET status='processing', attempts=attempts+1 WHERE id=?", (job['id'],))
        self.db.commit()
        try:
            destination = self.root / 'spray-review' / 'live' / (job['id'] + '.json')
            # Publication is atomic: a crash after publishing must not rerun inference.
            if not destination.exists():
                self.evaluate(job, cv2, detector_factory, destination)
            self.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job['id'],))
            self.db.commit()
            print(f"Reviewed burst {Path(job['manifest']).name}", flush=True)
        except Exception as error:
            delay = min(300, 10 * 2 ** min(job['attempts'], 5))
            self.db.execute("UPDATE jobs SET status='pending', next_attempt=? WHERE id=?", (time.time()+delay, job['id']))
            self.db.commit()
            print(f"Review failed ({type(error).__name__}); retry in {delay}s: {Path(job['manifest']).name}", flush=True)
        return True

    def evaluate(self, job, cv2, detector_factory, destination):
        manifest = json.loads(Path(job['manifest']).read_text())
        event = manifest['event_id']
        import re
        if not re.fullmatch(r'motion_\d{8}_\d{6}_\d{6}', event):
            raise ValueError('Invalid event ID')
        paths = manifest['frames']
        if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
            raise ValueError('Invalid burst size')
        settings, policy = json.loads(job['settings']), json.loads(job['policy'])
        detector = detector_factory(settings)
        output = self.root / 'cat-results' / 'live' / job['id']
        output.mkdir(parents=True, exist_ok=True)
        evidence = []
        for index, name in enumerate(paths, 1):
            if name != f'{event}_burst_{index:02d}.jpg':
                raise ValueError('Invalid frame name')
            source = (self.raw / name).resolve()
            if source.parent != self.raw:
                raise ValueError('Frame escaped raw directory')
            frame = cv2.imread(str(source))
            if frame is None:
                raise ValueError('Missing or unreadable frame')
            annotated, report = detector.detect(frame)
            image = output / name
            if not cv2.imwrite(str(image), annotated, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise OSError('Image save failed')
            report.update(input_image=str(source), output_image=image.name, settings=settings,
                          original_size={'width': frame.shape[1], 'height': frame.shape[0]})
            atomic_json(image.with_suffix('.json'), report)
            evidence.append({'burst_index': index, 'report': report, 'report_file': str(image.with_suffix('.json'))})
        result = {'event_id': event, 'source_directory': str(self.raw),
                  'evaluated_at': datetime.now().astimezone().isoformat(),
                  'mode': 'logging_only', 'policy': policy, 'evidence': evidence,
                  **decide(evidence, policy)}
        atomic_json(destination, result)


def load_settings(root):
    from cat_detector import DEFAULTS as MODEL_DEFAULTS
    settings = MODEL_DEFAULTS | json.loads((root / 'cat_config.json').read_text())
    policy = json.loads((root / 'spray_policy.json').read_text())
    if policy.keys() != DEFAULTS.keys():
        raise ValueError('Invalid spray policy keys')
    for key in ('animal_confidence', 'person_confidence'):
        value = policy[key]
        if type(value) not in (int, float) or not 0 < value <= 1:
            raise ValueError('Invalid policy confidence')
    if not all(type(policy[k]) is int and policy[k] > 0 for k in ('min_animal_frames', 'expected_frames')) or policy['min_animal_frames'] > policy['expected_frames']:
        raise ValueError('Invalid frame counts')
    settings['confidence'] = min(policy['animal_confidence'], policy['person_confidence'])
    if type(settings['image_size']) is not int or settings['image_size'] < 32 or settings['image_size'] % 32:
        raise ValueError('Invalid image size')
    if not isinstance(settings['model'], str) or not settings['model'].strip():
        raise ValueError('Invalid model')
    return settings, policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-config', type=Path, default=Path('config-tc41-night.json'))
    parser.add_argument('--once', action='store_true', help='discover and process currently ready jobs, then exit')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    config = json.loads(args.capture_config.read_text())
    raw = (root / config['output_dir'] / 'raw').resolve()
    state = root / '.review-worker'
    state.mkdir(exist_ok=True)
    with (state / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another review worker is already running')
        import cv2
        cv2.setNumThreads(1)
        worker = Worker(root, raw, state)
        models = {}
        def detector_factory(settings):
            key = json.dumps(settings, sort_keys=True)
            if key not in models:
                models.clear()
                models[key] = CatDetector(settings)
            return models[key]
        print(f'Watching completed bursts in {raw}; logging only', flush=True)
        try:
            while True:
                settings, policy = load_settings(root)
                worker.discover(settings, policy)
                processed = worker.process_one(cv2, detector_factory)
                if not processed:
                    if args.once:
                        break
                    time.sleep(2)
        except KeyboardInterrupt:
            print('Stopping review worker', flush=True)
        finally:
            worker.db.close()


if __name__ == '__main__':
    main()
