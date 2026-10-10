"""Contract tests without downloading a model: python -m unittest test_cat_detector."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cat_detector import CatDetector, image_files, parse_args, process_images


def box(label_id, score, coordinates):
    return SimpleNamespace(cls=SimpleNamespace(item=lambda: label_id),
                           conf=SimpleNamespace(item=lambda: score),
                           xyxy=[SimpleNamespace(tolist=lambda: coordinates)])


class CatDetectorTests(unittest.TestCase):
    def test_labels_and_coordinates(self):
        detector = CatDetector.__new__(CatDetector)
        detector.settings = {"confidence": 0.5, "image_size": 640}
        result = SimpleNamespace(names={0: "person", 15: "cat"},
                                 boxes=[box(0, 0.9, [1, 2, 3, 4]),
                                        box(15, 0.8, [10, 20, 30, 40])],
                                 plot=lambda: "annotated")
        detector.model = Mock()
        detector.model.predict.return_value = [result]
        annotated, report = detector.detect("frame")
        self.assertTrue(report["cat_detected"])
        self.assertEqual(annotated, "annotated")
        self.assertEqual(report["detections"][1]["box_xyxy"], [10, 20, 30, 40])
        result.boxes = []
        self.assertFalse(detector.detect("frame")[1]["cat_detected"])

    def test_results_saved_for_negative_and_positive_images(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = [root / "cat.jpg", root / "empty.png", root / "bad.jpg"]
            for path in files:
                path.touch()
            self.assertEqual(len(image_files(root)), 3)
            frame = SimpleNamespace(shape=(720, 1280, 3))
            cv2 = Mock(IMWRITE_JPEG_QUALITY=1)
            cv2.imread.side_effect = [frame, frame, None]
            def save(path, *_):
                Path(path).touch()
                return True
            cv2.imwrite.side_effect = save
            detector = Mock()
            detector.detect.side_effect = [(frame, {"cat_detected": flag,
                "detections": [], "inference_time_ms": 1}) for flag in (True, False)]
            settings = {"output_dir": str(root / "results"), "model": "test.pt"}
            self.assertEqual(process_images(cv2, detector, files, settings, root / "config.json"), 1)
            records = [json.loads(p.read_text()) for p in (root / "results").glob("*/*.json")]
            self.assertEqual(len(records), 2)
            self.assertEqual({r["cat_detected"] for r in records}, {True, False})
            self.assertEqual(records[0]["original_size"], {"width": 1280, "height": 720})

    def test_config_override_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text(json.dumps({"confidence": 0.7}))
            argv = ["cat_detector.py", "image.jpg", "--config", str(config), "--confidence", "0.3"]
            with patch("sys.argv", argv):
                _, settings = parse_args()
            self.assertEqual(settings["confidence"], 0.3)
            for invalid in ({"confidence": True}, {"image_size": 0}, {"extra": 1}):
                config.write_text(json.dumps(invalid))
                with patch("sys.argv", argv[:4]), self.assertRaises(SystemExit) as raised:
                    parse_args()
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
