import unittest
from types import SimpleNamespace

from bluetooth_connection import BluetoothDeviceUnavailable, resolve_ble_device, select_discovered_device


class BluetoothConnectionTests(unittest.IsolatedAsyncioTestCase):
    def test_saved_address_has_priority(self):
        expected = SimpleNamespace(address="OLD-ID", name="KICKR CORE")
        other = SimpleNamespace(address="NEW-ID", name="KICKR CORE")
        selected, matched_by = select_discovered_device([other, expected], "old-id", "KICKR CORE")
        self.assertIs(expected, selected)
        self.assertEqual("address", matched_by)

    def test_unique_saved_name_recovers_changed_address(self):
        expected = SimpleNamespace(address="NEW-ID", name="KICKR CORE 5836")
        selected, matched_by = select_discovered_device([expected], "OLD-ID", "KICKR CORE 5836")
        self.assertIs(expected, selected)
        self.assertEqual("name", matched_by)

    def test_ambiguous_name_is_not_guessed(self):
        devices = [
            SimpleNamespace(address="A", name="KICKR CORE"),
            SimpleNamespace(address="B", name="KICKR CORE"),
        ]
        selected, matched_by = select_discovered_device(devices, "OLD-ID", "KICKR CORE")
        self.assertIsNone(selected)
        self.assertIsNone(matched_by)

    async def test_resolver_retries_then_returns_device(self):
        expected = SimpleNamespace(address="NEW-ID", name="KICKR CORE 5836")
        scans = iter([[], [expected]])

        async def discover(**_kwargs):
            return next(scans)

        selected = await resolve_ble_device("OLD-ID", "KICKR CORE 5836", attempts=2, scan_timeout=0.1, discover=discover)
        self.assertIs(expected, selected)

    async def test_resolver_raises_actionable_error(self):
        async def discover(**_kwargs):
            return []

        with self.assertRaisesRegex(BluetoothDeviceUnavailable, "0_Setup_Devices.py"):
            await resolve_ble_device("OLD-ID", "KICKR CORE", attempts=1, scan_timeout=0.1, discover=discover)


if __name__ == "__main__":
    unittest.main()
