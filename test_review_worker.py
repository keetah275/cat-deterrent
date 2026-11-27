import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from garden_dashboard import Dashboard
from review_worker import Worker, atomic_json
from spray_decisions import DEFAULTS


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = self.root / 'captures' / 'test' / 'raw'
        self.raw.mkdir(parents=True)
        self.state = self.root / 'state'
        self.worker = Worker(self.root, self.raw, self.state)
        self.event = 'motion_20261127_120000_000000'
        self.settings = {'model': 'test.pt', 'confidence': .25, 'image_size': 640}
        self.factory = Mock()
        self.factory.return_value.detect.side_effect = lambda frame: (frame, {
            'detections': [{'label': 'cat', 'confidence': .8}], 'inference_time_ms': 1})

    def tearDown(self):
        self.worker.db.close()
        self.temp.cleanup()

    def complete(self):
        names = []
        for i in range(1, 6):
            name = f'{self.event}_burst_{i:02d}.jpg'
            names.append(name)
            cv2.imwrite(str(self.raw / name), np.zeros((60, 100, 3), np.uint8))
        atomic_json(self.raw / f'{self.event}.complete.json', {'event_id': self.event, 'frames': names})

    def test_completion_idempotence_dashboard_and_feedback(self):
        # A partial burst without a marker is never queued.
        (self.raw / f'{self.event}_burst_01.jpg').touch()
        self.worker.discover(self.settings, DEFAULTS)
        self.assertFalse(self.worker.process_one(cv2, self.factory))
        self.complete()
        self.worker.discover(self.settings, DEFAULTS)
        self.assertTrue(self.worker.process_one(cv2, self.factory))
        self.worker.discover(self.settings, DEFAULTS)
        self.assertFalse(self.worker.process_one(cv2, self.factory))
        self.assertEqual(self.factory.return_value.detect.call_count, 5)
        app = Dashboard(self.root)
        decision = app.decisions()['items'][0]
        self.assertTrue(decision['would_spray'])
        self.assertTrue(all(e['annotated'] for e in decision['evidence']))
        app.save_feedback({'decision_id': decision['decision_id'], 'verdict': 'agree', 'note': 'cat'})
        # A second event arriving must not erase the previous event or its feedback.
        self.event = 'motion_20261127_120010_000000'
        self.complete()
        self.worker.discover(self.settings, DEFAULTS)
        self.worker.process_one(cv2, self.factory)
        decisions = app.decisions()['items']
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[1]['feedback']['note'], 'cat')

    def test_failures_retry_and_inflight_jobs_recover(self):
        self.complete()
        self.worker.discover(self.settings, DEFAULTS)
        broken = Mock(side_effect=RuntimeError('test failure'))
        self.worker.process_one(cv2, broken)
        row = self.worker.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(row['status'], 'pending')
        self.assertFalse(list((self.root / 'spray-review').glob('live/*.json')))
        self.worker.db.execute("UPDATE jobs SET status='processing',next_attempt=0")
        self.worker.db.commit()
        self.worker.db.close()
        self.worker = Worker(self.root, self.raw, self.state)
        self.worker.process_one(cv2, self.factory)
        self.assertEqual(self.worker.db.execute('SELECT status FROM jobs').fetchone()[0], 'done')

    def test_person_veto_and_partial_policy_mismatch(self):
        self.complete()
        self.factory.return_value.detect.side_effect = lambda frame: (frame, {
            'detections': [{'label': 'cat', 'confidence': .8}, {'label': 'person', 'confidence': .3}]})
        self.worker.discover(self.settings, DEFAULTS)
        self.worker.process_one(cv2, self.factory)
        decision = Dashboard(self.root).decisions()['items'][0]
        self.assertFalse(decision['would_spray'])
        self.assertEqual(decision['reason'], 'person_detected')


if __name__ == '__main__':
    unittest.main()
