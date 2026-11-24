"""Synthetic checks for excluding plant movement without hiding the saved frame."""

import unittest

import cv2
import numpy as np

from motion_detector import detection_mask, moving_regions, prepare_frame, MotionConfirmation


class MotionZoneTests(unittest.TestCase):
    def test_brief_flashes_do_not_confirm(self):
        confirmation = MotionConfirmation(3)
        positives = [True, False, True, True, False, True, True, True, True]
        results = [confirmation.update([(1, 1, 20, 20)] if positive else [])
                   for positive in positives]
        self.assertEqual(results, [False, False, False, False, False,
                                   False, False, True, True])

    def test_default_confirmation_preserves_single_frame_detection(self):
        confirmation = MotionConfirmation(1)
        self.assertTrue(confirmation.update([(1, 1, 20, 20)]))
        self.assertFalse(confirmation.update([]))

    def setUp(self):
        self.frame = np.zeros((360, 640, 3), dtype=np.uint8)
        self.mask = detection_mask(cv2, self.frame.shape, 60,
                                   [[200, 100], [500, 100], [500, 290], [200, 290]])

    def boxes(self, changed):
        return moving_regions(cv2, prepare_frame(cv2, self.frame, 60, self.mask),
                              prepare_frame(cv2, changed, 60, self.mask), 25, 600, self.mask)

    def test_excluded_movement_touching_polygon_does_not_leak(self):
        changed = self.frame.copy()
        changed[110:280, 100:200] = 255
        self.assertEqual(self.boxes(changed), [])

    def test_inside_movement_and_full_frame_preserved(self):
        changed = self.frame.copy()
        changed[140:240, 250:350] = 255
        original = changed.copy()
        self.assertTrue(self.boxes(changed))
        np.testing.assert_array_equal(changed, original)

    def test_border_exclusion_without_polygon(self):
        self.mask = detection_mask(cv2, self.frame.shape, 60, [])
        changed = self.frame.copy()
        changed[100:200, :60] = 255
        self.assertEqual(self.boxes(changed), [])

    def test_non_overlapping_polygon_is_rejected(self):
        with self.assertRaises(ValueError):
            detection_mask(cv2, self.frame.shape, 60, [[0, 0], [20, 0], [20, 20]])


if __name__ == "__main__":
    unittest.main()
