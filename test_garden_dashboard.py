import io
import json
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

    def request(self, target):
        socket = Socket(f'GET {target} HTTP/1.0\r\n\r\n'.encode())
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

    def test_unknown_service_is_not_reported_as_running(self):
        with patch('garden_dashboard.subprocess.run', side_effect=OSError):
            self.assertEqual(self.app.service_status()['ActiveState'], 'unknown')


if __name__ == '__main__':
    unittest.main()
