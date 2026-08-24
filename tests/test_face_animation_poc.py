import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path("/app/scripts/face_animation_poc.py")
SPEC = importlib.util.spec_from_file_location("face_animation_poc", SCRIPT)
POC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POC)


class FaceAnimationPocTests(unittest.TestCase):
    def test_regions_are_normalized(self):
        regions = POC.load_regions(Path("/app/config/face_animation/personaggio_1_listening_resting_cheek_brightroom_v1.json"))
        self.assertEqual({"face", "left_eye", "right_eye", "mouth"}, set(regions))
        self.assertNotEqual(regions["left_eye"]["angle_deg"], regions["right_eye"]["angle_deg"])
        self.assertIn("closure_line", regions["left_eye"])
        for key in ("close_amount", "upper_lid_weight", "lower_lid_weight", "preserve_iris_ratio", "preserve_lashes", "asymmetry_bias"):
            self.assertIn(key, regions["left_eye"])
        self.assertNotEqual(regions["left_eye"]["close_amount"], regions["right_eye"]["close_amount"])
        self.assertNotEqual(regions["left_eye"]["upper_lid_weight"], regions["right_eye"]["upper_lid_weight"])

    def test_blinks_are_deterministic_and_not_continuous(self):
        first = POC.blink_schedule(8.0, 25, 9001)
        self.assertEqual(first, POC.blink_schedule(8.0, 25, 9001))
        self.assertGreater(max(first), 0.5)
        self.assertGreater(first.count(0.0), 150)

    def test_eyes_have_small_independent_delay(self):
        schedules = POC.blink_schedules(8.0, 25, 9001)
        self.assertNotEqual(schedules["left_eye"], schedules["right_eye"])
        self.assertLessEqual(abs(schedules["left_eye"].index(max(schedules["left_eye"])) - schedules["right_eye"].index(max(schedules["right_eye"]))), 1)

    def test_elliptical_mask_has_no_rectangular_corners(self):
        regions = POC.load_regions(Path("/app/config/face_animation/personaggio_1_listening_resting_cheek_brightroom_v1.json"))
        mask = POC.oriented_mask((80, 50), regions["left_eye"], (640, 360))
        self.assertEqual(0, mask.getpixel((0, 0)))
        self.assertGreater(mask.getpixel((40, 25)), 200)

    def test_four_audio_levels_and_smoothing(self):
        raw = array_to_pcm([0] * 400 + [1000] * 400 + [8000] * 400 + [28000] * 400)
        values = POC.audio_envelope(raw, 1, 2, 1000, 10, 16)
        self.assertEqual(16, len(values))
        self.assertEqual(0.0, values[0])
        self.assertGreater(values[-1], values[8])


def array_to_pcm(values):
    import array
    result = array.array("h", values)
    return result.tobytes()


if __name__ == "__main__":
    unittest.main()
