import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from garden_dashboard import Dashboard, handler_for


class Socket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data):
        self.output.write(data)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'captures' / 'night' / 'raw'
        self.directory.mkdir(parents=True)
        self.image = self.directory / 'motion_20261127_095439_001862_burst_01.jpg'
        self.image.write_bytes(b'jpeg evidence')
        (self.root / 'camera-service.env').write_text('SECRET')
        self.app = Dashboard(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, target, payload=None, token=None):
        body = json.dumps(payload).encode() if payload is not None else b''
        method = 'POST' if payload is not None else 'GET'
        headers = f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
        if token:
            headers += f'X-Feedback-Token: {token}\r\n'
        socket = Socket(f'{method} {target} HTTP/1.0\r\n{headers}\r\n'.encode()+body)
        handler_for(self.app)(socket, ('127.0.0.1', 123), object())
        return socket.output.getvalue()

    def test_image_routes_and_secret_exclusion(self):
        url = self.app.image_url(self.image)
        self.assertIn(b'200 OK', self.request(url))
        for target in ['/camera-service.env', '/image?path=camera-service.env',
                       '/image?path=captures/../../camera-service.env', '/.git/config']:
            response = self.request(target)
            self.assertIn(b'404', response)
            self.assertNotIn(b'SECRET', response)
        link = self.directory / 'escape.jpg'
        link.symlink_to(self.root / 'camera-service.env')
        with self.assertRaises(ValueError):
            self.app.image_path(str(link.relative_to(self.root)))

    def test_capture_list_and_ui_assets(self):
        response = self.request('/api/captures?kind=raw')
        data = json.loads(response.split(b'\r\n\r\n', 1)[1])
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['kind'], 'raw')
        for target in ['/', '/app.js', '/style.css']:
            self.assertIn(b'200 OK', self.request(target))

    def test_decisions_evidence_uses_web_urls(self):
        run = self.root / 'spray-review' / 'run_20261127'
        run.mkdir(parents=True)
        report_path = self.root / 'cat-results' / 'run_test' / 'image.json'
        report_path.parent.mkdir(parents=True)
        annotated = report_path.with_suffix('.jpg')
        annotated.write_bytes(b'annotated')
        entry = {'event_id': 'motion_test', 'would_spray': True, 'reason': 'confirmed',
                 'evidence': [{'burst_index': 1, 'report_file': str(report_path),
                              'report': {'input_image': str(self.image),
                                         'output_image': annotated.name, 'detections': []}}]}
        (run / 'decisions.jsonl').write_text(json.dumps(entry)+'\n')
        data = self.app.decisions()
        self.assertTrue(data['items'][0]['would_spray'])
        evidence = data['items'][0]['evidence'][0]
        self.assertTrue(evidence['annotated'].startswith('/image?path='))
        self.assertTrue(evidence['original'].startswith('/image?path='))

        decision_id = data['items'][0]['decision_id']
        payload = {'decision_id': decision_id, 'verdict': 'agree', 'note': 'A real cat'}
        self.assertIn(b'403', self.request('/api/feedback', payload))
        self.assertIn(b'200 OK', self.request('/api/feedback', payload, self.app.feedback_token))
        # A new application instance still sees the saved vote.
        restarted = Dashboard(self.root)
        self.assertEqual(restarted.decisions()['items'][0]['feedback']['verdict'], 'agree')
        payload.update(verdict='disagree', note='=example note')
        self.assertIn(b'200 OK', self.request('/api/feedback', payload, self.app.feedback_token))
        self.assertEqual(restarted.decisions()['items'][0]['feedback']['note'], '=example note')
        connection = self.app.database()
        try:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM feedback').fetchone()[0], 2)
        finally:
            connection.close()
        self.assertIn(b"'=example note", self.request('/api/feedback.csv'))
        self.assertIn(b'404', self.request('/image?path=dashboard-data/feedback.sqlite3'))
        payload['verdict'] = 'spray_now'
        self.assertIn(b'400', self.request('/api/feedback', payload, self.app.feedback_token))
        payload.update(verdict='agree', decision_id='not-a-real-decision')
        self.assertIn(b'400', self.request('/api/feedback', payload, self.app.feedback_token))

    def test_unknown_service_is_not_reported_as_running(self):
        with patch('garden_dashboard.subprocess.run', side_effect=OSError):
            self.assertEqual(self.app.service_status()['ActiveState'], 'unknown')

    def test_feedback_note_only_applies_to_selected_decision(self):
        run = self.root / 'spray-review' / 'run_test'
        run.mkdir(parents=True)
        entries = [{'event_id': f'motion_{i}', 'would_spray': False,
                    'reason': 'no_animal', 'evidence': []} for i in range(3)]
        (run / 'decisions.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in entries))
        decisions = self.app.decisions()['items']
        selected = decisions[1]['decision_id']
        self.app.save_feedback({'decision_id': selected, 'verdict': 'disagree', 'note': 'Cat beside table'})
        for item in Dashboard(self.root).decisions()['items']:
            if item['decision_id'] == selected:
                self.assertEqual(item['feedback']['note'], 'Cat beside table')
            else:
                self.assertIsNone(item['feedback'])

    def test_storage_preflight_rolls_back_and_detects_read_only_database(self):
        self.app.check_feedback_storage()
        self.assertEqual(self.app.feedback_latest(), {})
        with patch.object(self.app, 'database', side_effect=sqlite3.OperationalError('readonly')):
            with self.assertRaises(sqlite3.OperationalError):
                self.app.check_feedback_storage()

    def test_failed_save_returns_actionable_error_without_success(self):
        with patch.object(self.app, 'save_feedback', side_effect=sqlite3.OperationalError('readonly')):
            response = self.request('/api/feedback', {'decision_id':'test','verdict':'agree'}, self.app.feedback_token)
        self.assertIn(b'503', response)
        body = json.loads(response.split(b'\r\n\r\n', 1)[1])
        self.assertIn('read-only', body['error'])
        self.assertNotIn('saved_at', body)


if __name__ == '__main__':
    unittest.main()
