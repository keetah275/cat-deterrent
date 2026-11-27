from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from garden_dashboard import Dashboard
from retain_evidence import run_retention


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Dashboard(self.root)
        self.app.check_feedback_storage()
        self.db = self.root / 'dashboard-data' / 'feedback.sqlite3'
        self.now = datetime(2026, 11, 29, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def event(self, hour, minute, verdict=False, age=48):
        event = f'motion_20261127_{hour:02d}{minute:02d}00_000000'
        raw = self.root / 'captures' / 'night' / 'raw'
        raw.mkdir(parents=True, exist_ok=True)
        source = raw / f'{event}_burst_01.jpg'
        source.write_bytes(b'image')
        source.with_suffix('.json').write_text('{}')
        preview = raw.parent / f'{event}.jpg'
        preview.write_bytes(b'preview')
        report = self.root / 'cat-results' / 'live' / event / source.with_suffix('.json').name
        report.parent.mkdir(parents=True)
        report.write_text('{}')
        report.with_suffix('.jpg').write_bytes(b'annotation')
        result = self.root / 'spray-review' / 'live' / (event+'.json')
        result.parent.mkdir(parents=True, exist_ok=True)
        item = {'event_id': event, 'would_spray': verdict, 'complete_burst': True,
                'source_directory': str(raw), 'evaluated_at': (self.now-timedelta(hours=age)).isoformat(),
                'reason': 'test', 'policy': {}, 'evidence': [{'burst_index': 1, 'report_file': str(report),
                'report': {'input_image': str(source), 'output_image': report.with_suffix('.jpg').name, 'detections': []}}]}
        result.write_text(json.dumps(item))
        return result, source

    def run_policy(self, apply, samples=0):
        return run_retention(self.root, {'no_spray_hours':24,'samples_per_hour':samples}, self.db, apply, self.now)

    def test_preview_then_delete_only_old_unreviewed_negative(self):
        negative, image = self.event(10, 1)
        positive, pos_image = self.event(10, 2, True)
        recent, recent_image = self.event(10, 3, age=1)
        reviewed, reviewed_image = self.event(10, 4)
        decision = next(d for d in self.app.decisions()['items'] if d['event_id']==reviewed.stem)
        self.app.save_feedback({'decision_id':decision['decision_id'], 'verdict':'agree','note':'keep this'})
        self.assertEqual(self.run_policy(False), 1)
        self.assertTrue(image.exists())
        self.assertEqual(self.run_policy(True), 1)
        self.assertFalse(image.exists())
        self.assertFalse(json.loads(negative.read_text())['evidence_retained'])
        self.assertTrue(all(p.exists() for p in [pos_image,recent_image,reviewed_image]))
        self.assertEqual(self.run_policy(True), 0)

    def test_one_sample_per_hour_and_missing_feedback_fails_closed(self):
        events=[self.event(10,1),self.event(10,2),self.event(11,1),self.event(11,2)]
        self.assertEqual(self.run_policy(True, samples=1), 2)
        self.assertEqual(sum(image.exists() for _,image in events), 2)
        with self.assertRaises(Exception):
            run_retention(self.root, {'no_spray_hours':24,'samples_per_hour':0}, self.root/'missing.db', True, self.now)
        self.assertEqual(sum(image.exists() for _,image in events), 2)


if __name__ == '__main__':
    unittest.main()
