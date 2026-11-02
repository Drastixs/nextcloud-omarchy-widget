import unittest
from types import SimpleNamespace

import support  # noqa: F401
from ncstatus import actions, dbus


class FakeBus:
    def __init__(self, *states):
        self.paused = dict(enumerate(states))
        self.status = {n: 1 for n in self.paused}
        self.toggles = []
        self.broken = set()

    def folders(self):
        return [SimpleNamespace(object_path=n, name=f"F{n}", paused=p, status=self.status[n])
                for n, p in self.paused.items()]

    def describe(self, obj):
        return self.paused[obj]

    def activate(self, obj, action):
        if obj in self.broken:
            return False
        self.toggles.append(obj)
        self.paused[obj] = not self.paused[obj]
        return True


class Pause(unittest.TestCase):
    def run_it(self, bus, paused):
        return actions.set_all_paused(bus, paused, sleep=lambda _: None)

    def test_mixed_state_is_not_inverted(self):
        bus = FakeBus(True, False)
        self.assertEqual(self.run_it(bus, True), (True, "Paused"))
        self.assertEqual(bus.toggles, [1])
        self.assertEqual(list(bus.paused.values()), [True, True])

    def test_resume_only_flips_paused(self):
        bus = FakeBus(True, False)
        self.assertEqual(self.run_it(bus, False), (True, "Resumed"))
        self.assertEqual(bus.toggles, [0])

    def test_already_there_flips_nothing(self):
        bus = FakeBus(True, True)
        self.assertTrue(self.run_it(bus, True)[0])
        self.assertEqual(bus.toggles, [])

    def test_unknown_state_is_never_toggled(self):
        bus = FakeBus(False)
        bus.describe = lambda obj: None
        self.assertFalse(self.run_it(bus, True)[0])
        self.assertEqual(bus.toggles, [])

    def test_failure_is_reported(self):
        bus = FakeBus(False, False)
        bus.broken = {1}
        ok, message = self.run_it(bus, True)
        self.assertFalse(ok)
        self.assertIn("F1", message)

    def test_no_client(self):
        self.assertEqual(self.run_it(FakeBus(), True), (False, "Client not running"))


class SyncNow(unittest.TestCase):
    def run_it(self, bus):
        return actions.sync_now(bus, sleep=lambda _: None)

    def test_toggles_twice(self):
        bus = FakeBus(False)
        self.assertEqual(self.run_it(bus), (True, "Sync started"))
        self.assertEqual(bus.toggles, [0, 0])
        self.assertFalse(bus.paused[0])

    def test_skips_user_paused(self):
        bus = FakeBus(True, False)
        self.run_it(bus)
        self.assertEqual(bus.toggles, [1, 1])
        self.assertTrue(bus.paused[0])

    def test_no_op_while_syncing(self):
        bus = FakeBus(False)
        bus.status[0] = dbus.STATUS_SYNCING
        self.assertEqual(self.run_it(bus), (True, "Already syncing"))
        self.assertEqual(bus.toggles, [])

    def test_only_idle_folders(self):
        bus = FakeBus(False, False)
        bus.status[0] = dbus.STATUS_SYNCING
        self.run_it(bus)
        self.assertEqual(bus.toggles, [1, 1])

    def test_all_paused(self):
        bus = FakeBus(True)
        self.assertEqual(self.run_it(bus), (False, "Sync is paused"))
        self.assertEqual(bus.toggles, [])

    def test_resume_is_retried(self):
        bus = FakeBus(False)
        real = bus.activate
        calls = []

        def flaky(obj, action):
            calls.append(obj)
            return False if len(calls) == 2 else real(obj, action)

        bus.activate = flaky
        self.assertTrue(self.run_it(bus)[0])
        self.assertFalse(bus.paused[0])


class DbusParsing(unittest.TestCase):
    def test_tree_sorts_numerically_and_filters(self):
        text = ("/\n/com/nextcloudgmbh/Nextcloud/Folder\n/com/nextcloudgmbh/Nextcloud/Folder/10\n"
                "/com/nextcloudgmbh/Nextcloud/Folder/1\n/com/nextcloudgmbh/Nextcloud/Provider\n")
        self.assertEqual(dbus.parse_tree(text), ["/com/nextcloudgmbh/Nextcloud/Folder/1",
                                                 "/com/nextcloudgmbh/Nextcloud/Folder/10"])

    def test_describe(self):
        reply = {"type": "(bgav)", "data": [[True, "", [{"type": "b", "data": True}]]]}
        self.assertIs(dbus.parse_describe(reply), True)
        reply["data"][0][2][0]["data"] = False
        self.assertIs(dbus.parse_describe(reply), False)
        self.assertIsNone(dbus.parse_describe({"data": []}))
        self.assertIsNone(dbus.parse_describe({"data": [[True, "", []]]}))

    def test_recent_menu(self):
        reply = {"data": [[[1, 0, [{"target": {"type": "s", "data": "/home/alice/a"},
                                    "label": {"type": "s", "data": "a"}},
                                   {"label": {"type": "s", "data": "No items"}}]]]]}
        self.assertEqual(dbus.parse_recent_menu(reply), ["/home/alice/a"])
        self.assertEqual(dbus.parse_recent_menu({}), [])


if __name__ == "__main__":
    unittest.main()
