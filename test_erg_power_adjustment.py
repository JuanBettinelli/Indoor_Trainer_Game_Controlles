import unittest

from erg_power_adjustment import ErgPowerAdjustment


class ErgPowerAdjustmentTests(unittest.TestCase):
    def test_clicks_change_target_in_five_watt_steps(self):
        adjustment = ErgPowerAdjustment()
        adjustment.change(1, announce=False)
        self.assertEqual(205, adjustment.target(200))
        adjustment.change(-1, announce=False)
        adjustment.change(-1, announce=False)
        self.assertEqual(195, adjustment.target(200))

    def test_target_is_clamped_to_safety_limits(self):
        adjustment = ErgPowerAdjustment(minimum_watts=50, maximum_watts=300)
        for _ in range(100):
            adjustment.change(1, 250, announce=False)
        self.assertEqual(300, adjustment.target(250))
        adjustment.change(-1, 250, announce=False)
        self.assertEqual(295, adjustment.target(250))
        for _ in range(200):
            adjustment.change(-1, 250, announce=False)
        self.assertEqual(50, adjustment.target(250))


if __name__ == "__main__":
    unittest.main()
