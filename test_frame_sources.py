"""Threaded capture tests without camera hardware."""

import queue
import threading
import unittest
from unittest.mock import Mock, patch

from frame_sources import StreamSource


class StreamSourceTests(unittest.TestCase):
    def setUp(self):
        self.incoming = queue.Queue()
        self.released = threading.Event()
        self.capture = Mock()
        self.capture.open.return_value = True
        self.capture.read.side_effect = lambda: self.incoming.get(timeout=2)
        self.capture.release.side_effect = self.released.set
        self.cv2 = Mock()
        self.cv2.resize.side_effect = lambda frame, size: frame
        self.cv2.VideoCapture.return_value = self.capture
        with patch.dict("os.environ", {"CAT_CAMERA_RTSP_URL": "rtsp://camera/stream2"}):
            self.source = StreamSource(self.cv2)

    def tearDown(self):
        self.source.stopping.set()
        self.incoming.put((False, None))
        self.source.close()
        self.assertTrue(self.released.is_set())
        self.assertFalse(self.source.worker.is_alive())
        self.capture.release.assert_called_once()

    def test_reads_continuously_and_keeps_latest_only(self):
        for number in range(100):
            self.incoming.put((True, number))
        with self.source.condition:
            self.assertTrue(self.source.condition.wait_for(
                lambda: self.source.latest == 99, timeout=2))
        self.assertEqual(self.source.read(), 99)
        self.assertIsNone(self.source.latest)
        self.incoming.put((True, 100))
        self.assertEqual(self.source.read(), 100)

    def test_failure_reaches_consumer_and_releases_capture(self):
        self.incoming.put((False, None))
        with self.assertRaisesRegex(RuntimeError, "RTSP capture failed"):
            self.source.read()
        self.assertTrue(self.released.wait(timeout=2))

    def test_closed_source_rejects_reads(self):
        self.source.stopping.set()
        self.incoming.put((False, None))
        self.source.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.source.read()


if __name__ == "__main__":
    unittest.main()
