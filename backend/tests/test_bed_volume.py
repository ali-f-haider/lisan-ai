"""What is put back under the speech (a rebuild, not the original sound) is laid at 30 % of its old level unless told otherwise."""
import importlib
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pause_bed as pb

OLD_FLOOR, OLD_CROWD = -4.0, -3.0
DB30 = 20.0 * math.log10(0.3)


def fresh(**env):
    keys = ("DUB_BED_VOLUME", "DUB_BG_FLOOR_DB", "DUB_CROWD_BED_DB")
    with patch.dict(os.environ, {k: v for k, v in env.items()}, clear=False):
        for k in keys:
            if k not in env:
                os.environ.pop(k, None)
        return importlib.reload(pb)


class BedVolume(unittest.TestCase):
    def tearDown(self):
        for k in ("DUB_BED_VOLUME", "DUB_BG_FLOOR_DB", "DUB_CROWD_BED_DB"):
            os.environ.pop(k, None)
        importlib.reload(pb)

    def test_default_is_thirty_percent(self):
        m = fresh()
        self.assertAlmostEqual(m.BED_VOLUME, 0.3)
        self.assertAlmostEqual(m.FLOOR_DB, OLD_FLOOR + DB30, places=6)
        self.assertAlmostEqual(m.CROWD_BED_DB, OLD_CROWD + DB30, places=6)
        self.assertAlmostEqual(DB30, -10.46, places=2)

    def test_full_volume_gives_the_old_levels_back(self):
        m = fresh(DUB_BED_VOLUME="1")
        self.assertAlmostEqual(m.FLOOR_DB, OLD_FLOOR, places=6)
        self.assertAlmostEqual(m.CROWD_BED_DB, OLD_CROWD, places=6)

    def test_other_volumes_and_limits(self):
        self.assertAlmostEqual(fresh(DUB_BED_VOLUME="0.5").BED_VOLUME, 0.5)
        self.assertEqual(fresh(DUB_BED_VOLUME="0").BED_VOLUME, 0.05)          # never fully silent: clamped
        self.assertEqual(fresh(DUB_BED_VOLUME="7").BED_VOLUME, 1.0)
        self.assertEqual(fresh(DUB_BED_VOLUME="nonsense").BED_VOLUME, 0.3)

    def test_the_old_level_settings_still_apply_on_top(self):
        m = fresh(DUB_BG_FLOOR_DB="-6", DUB_CROWD_BED_DB="-5")
        self.assertAlmostEqual(m.FLOOR_DB, -6.0 + DB30, places=6)
        self.assertAlmostEqual(m.CROWD_BED_DB, -5.0 + DB30, places=6)


if __name__ == "__main__":
    unittest.main()
