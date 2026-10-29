import unittest
from types import SimpleNamespace

from pynput.keyboard import Key

from workout_finish_control import is_finish_key


class WorkoutFinishControlTests(unittest.TestCase):
    def test_physical_q_finishes(self):
        self.assertTrue(is_finish_key(SimpleNamespace(char="q"), injected=False))
        self.assertTrue(is_finish_key(SimpleNamespace(char="Q"), injected=False))

    def test_physical_f12_finishes(self):
        self.assertTrue(is_finish_key(Key.f12, injected=False))

    def test_injected_controller_q_does_not_finish(self):
        self.assertFalse(is_finish_key(SimpleNamespace(char="q"), injected=True))

    def test_unrelated_key_does_not_finish(self):
        self.assertFalse(is_finish_key(SimpleNamespace(char="e"), injected=False))


if __name__ == "__main__":
    unittest.main()
