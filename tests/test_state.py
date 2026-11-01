import unittest

from support import fixture_jsonl
from ncstatus.socketproto import SocketState, parse_socket_line
from ncstatus.state import (FolderView, Inputs, RunTracker, build_transfer, compute_state,
                            is_transfer_active, pick_details, quota_over_limit, summary_for)
from ncstatus.statusdetails import Details, parse_status_details


def folder(details="Connected - Success", status=1, socket="OK", paused=False):
    return FolderView("Documents", "/home/alice/Documents", "/Sync/Documents", socket, status,
                      details, paused)


def state(*folders, **kwargs):
    kwargs.setdefault("running", True)
    kwargs.setdefault("api_available", True)
    return compute_state(Inputs(folders=list(folders), **kwargs))


ERROR_FILE = {"/home/alice/Documents/x": ("ERROR", 1)}


class Precedence(unittest.TestCase):
    def test_client_down_beats_all(self):
        self.assertEqual(state(folder(), running=False, api_available=False,
                               file_problems=ERROR_FILE), "client-down")

    def test_disconnected_beats_paused_and_error(self):
        self.assertEqual(state(folder("Disconnected - x", 3, "IGNORE", True)), "disconnected")

    def test_api_gone_is_not_shown_as_synced(self):
        cfg_only = FolderView("Documents", "/p", socket_status="OK")
        inputs = Inputs(running=True, api_available=False, folders=[cfg_only])
        self.assertEqual(compute_state(inputs), "disconnected")
        self.assertEqual(summary_for("disconnected", inputs), "Client API unavailable")

    def test_paused_needs_every_folder(self):
        self.assertEqual(state(folder("Connected - Sync paused", 3, paused=True),
                               folder("Connected - Sync paused", 3, paused=True)), "paused")

    def test_paused_beats_error_and_syncing(self):
        self.assertEqual(state(folder("Connected - Syncing 9.5 MB (5 seconds left)", 3, paused=True),
                               file_problems=ERROR_FILE), "paused")

    def test_status_3_on_a_paused_folder_is_not_an_error(self):
        paused = folder("Connected - Sync paused", 3, paused=True)
        self.assertEqual(state(paused, folder()), "synced")

    def test_status_3_unpaused_is_error(self):
        self.assertEqual(state(folder("Connected - Error", 3, "IGNORE")), "error")

    def test_failed_upload_looks_idle_but_is_error(self):
        # Status 1, as attempts 2 and 3 ended up.
        ignored = "Connected - Success, some files were ignored."
        self.assertEqual(state(folder(ignored, 1, "IGNORE")), "error")
        self.assertEqual(state(folder(ignored, 1, "OK"), file_problems=ERROR_FILE), "error")

    def test_ignored_text_without_socket_is_error(self):
        self.assertEqual(state(folder("Connected - Success, some files were ignored.", 1, None)),
                         "error")

    def test_error_beats_syncing(self):
        self.assertEqual(state(folder("Connected - Syncing", 2, "SYNC"),
                               file_problems=ERROR_FILE), "error")

    def test_syncing_from_status_or_socket(self):
        self.assertEqual(state(folder("Connected - Syncing 0 of 0", 2, "OK")), "syncing")
        self.assertEqual(state(folder("Connected - Success", 1, "SYNC")), "syncing")

    def test_syncing_beats_warning(self):
        self.assertEqual(state(folder("Connected - Syncing", 2), quota_warning=True), "syncing")

    def test_warning(self):
        self.assertEqual(state(folder(), quota_warning=True), "warning")
        self.assertEqual(state(folder(socket="WARNING")), "warning")
        self.assertEqual(state(folder(), file_problems={"/x": ("WARNING", 1)}), "warning")

    def test_synced(self):
        self.assertEqual(state(folder()), "synced")
        self.assertEqual(summary_for("synced", Inputs()), "Up to date")


class Summaries(unittest.TestCase):
    def test_failed_file_count(self):
        inputs = Inputs(file_problems=ERROR_FILE)
        self.assertEqual(summary_for("error", inputs), "1 file failed")

    def test_disconnected_uses_the_client_words(self):
        inputs = Inputs(running=True, api_available=True,
                        folders=[folder("Network error - Syncing")])
        self.assertEqual(summary_for("disconnected", inputs), "Network error")


class ActiveTransfer(unittest.TestCase):
    def test_paused_folder_still_transferring_is_active(self):
        inputs = Inputs(folders=[folder("Connected - Syncing 9.5 MB (5 seconds left)", 3, paused=True)])
        self.assertTrue(is_transfer_active(inputs))

    def test_idle(self):
        self.assertFalse(is_transfer_active(Inputs(folders=[folder()])))


class Tracker(unittest.TestCase):
    def test_first_zero_eta_is_withheld(self):
        tracker = RunTracker()
        self.assertEqual(tracker.update(0, True, 0), (None, None))
        self.assertEqual(tracker.update(10, True, 30), (30, 0.25))

    def test_final_zero_is_believed(self):
        tracker = RunTracker()
        tracker.update(0, True, 30)
        self.assertEqual(tracker.update(30, True, 0), (0, 1.0))

    def test_delete_runs_stay_hidden(self):
        tracker = RunTracker()
        for now in range(5):
            self.assertEqual(tracker.update(now, True, 0), (None, None))

    def test_new_run_starts_again(self):
        tracker = RunTracker()
        tracker.update(0, True, 30)
        self.assertEqual(tracker.update(5, False, None), (None, None))
        self.assertEqual(tracker.last_finished_at, 5)
        self.assertEqual(tracker.update(6, True, 0), (None, None))

    def test_log_start_gives_true_elapsed(self):
        tracker = RunTracker()
        self.assertEqual(tracker.update(100, True, 10, log_started_at=90), (10, 0.5))

    def test_no_eta_no_progress(self):
        tracker = RunTracker()
        tracker.update(0, True, 30)
        self.assertEqual(tracker.update(5, True, None), (None, None))


class Transfer(unittest.TestCase):
    def build(self, **overrides):
        values = dict(active=True, picked=Details(total_bytes=100), eta_sec=10, progress=0.5,
                      rates=(1000, 500), counts=(1, 3), current="a", last_finished="z")
        values.update(overrides)
        return build_transfer(**values)

    def test_bytes_left_is_combined_speed_times_eta(self):
        self.assertEqual(self.build()["bytesLeft"], 15000)

    def test_bytes_left_null_without_eta_or_speed(self):
        self.assertIsNone(self.build(eta_sec=None)["bytesLeft"])
        self.assertIsNone(self.build(rates=(0, 0))["bytesLeft"])
        self.assertIsNone(self.build(rates=(None, None))["bytesLeft"])

    def test_counts_from_text_when_log_has_none(self):
        picked = parse_status_details("Connected - Syncing 2 of 12  (0 seconds left)")
        result = self.build(picked=picked, counts=(None, None))
        self.assertEqual((result["filesDone"], result["filesTotal"]), (2, 12))

    def test_log_counts_win(self):
        picked = parse_status_details("Connected - Syncing 2 of 12")
        self.assertEqual(self.build(picked=picked)["filesTotal"], 3)

    def test_inactive_clears_the_run_but_keeps_last_finished(self):
        result = self.build(active=False)
        self.assertFalse(result["active"])
        self.assertIsNone(result["etaSec"])
        self.assertIsNone(result["filesTotal"])
        self.assertIsNone(result["currentFile"])
        self.assertEqual(result["lastFinishedFile"], "z")

    def test_pick_prefers_the_folder_with_numbers(self):
        picked = pick_details([folder("Connected - Syncing", 2),
                               folder("Connected - Syncing 19 MB (5 seconds left)", 2)])
        self.assertEqual(picked.eta_sec, 5)


class Quota(unittest.TestCase):
    def test_threshold(self):
        self.assertTrue(quota_over_limit(90, 100, 90))
        self.assertFalse(quota_over_limit(89, 100, 90))

    def test_no_limit_never_warns(self):
        self.assertFalse(quota_over_limit(10 ** 12, None, 90))
        self.assertFalse(quota_over_limit(None, None, 90))


class Socket(unittest.TestCase):
    def state(self):
        state = SocketState()
        state.feed("REGISTER_PATH:/home/alice/Documents")
        return state

    def test_parse(self):
        self.assertEqual(parse_socket_line("STATUS:OK:/a/b:c\n"), ("status", "OK", "/a/b:c"))
        self.assertEqual(parse_socket_line("STATUS:OK+SWM:/a"), ("status", "OK", "/a"))
        self.assertEqual(parse_socket_line("REGISTER_PATH:/a/"), ("register", "/a"))
        for line in ("UPDATE_VIEW:/a", "VERSION:35.0.0daily:1.1", "STATUS:", "junk", ""):
            self.assertIsNone(parse_socket_line(line))

    def test_folder_versus_file(self):
        state = self.state()
        state.feed("STATUS:IGNORE:/home/alice/Documents")
        state.feed("STATUS:ERROR:/home/alice/Documents/x.bin", now=5)
        self.assertEqual(state.folders, {"/home/alice/Documents": "IGNORE"})
        self.assertEqual(state.problems, {"/home/alice/Documents/x.bin": ("ERROR", 5)})

    def test_trailing_slash_is_the_same_folder(self):
        state = self.state()
        state.feed("STATUS:SYNC:/home/alice/Documents/")
        self.assertEqual(state.folders, {"/home/alice/Documents": "SYNC"})

    def test_error_stays_until_the_file_says_otherwise(self):
        state = self.state()
        state.feed("STATUS:ERROR:/home/alice/Documents/x", now=1)
        state.feed("STATUS:OK:/home/alice/Documents/other")
        state.feed("STATUS:OK:/home/alice/Documents")
        self.assertIn("/home/alice/Documents/x", state.problems)
        self.assertTrue(state.feed("STATUS:OK:/home/alice/Documents/x"))
        self.assertEqual(state.problems, {})

    def test_nop_clears_a_deleted_file(self):
        state = self.state()
        state.feed("STATUS:ERROR:/home/alice/Documents/x", now=1)
        state.feed("STATUS:NOP:/home/alice/Documents/x")
        self.assertEqual(state.problems, {})

    def test_repeat_keeps_first_seen(self):
        state = self.state()
        state.feed("STATUS:ERROR:/home/alice/Documents/x", now=1)
        self.assertFalse(state.feed("STATUS:ERROR:/home/alice/Documents/x", now=9))
        self.assertEqual(state.problems["/home/alice/Documents/x"], ("ERROR", 1))

    def test_clear_keeps_roots(self):
        state = self.state()
        state.feed("STATUS:OK:/home/alice/Documents")
        state.clear()
        self.assertEqual(state.folders, {})
        self.assertIn("/home/alice/Documents", state.roots)


class FailedUploadReplay(unittest.TestCase):
    """The captured 200 MB failure: the bar must never read "synced" after it."""

    def test_replay(self):
        roots = ("/home/alice/Documents", "/home/alice/Pictures/Screenshots")
        socket = SocketState()
        socket.roots.update(roots)
        seen = []
        for sample in fixture_jsonl("sampler-200mb-failed-first-attempt.jsonl"):
            for line in sample["sock"]:
                socket.feed(line, now=sample["t"])
            views = []
            for root, status, details in zip(roots, sample["st"], sample["det"]):
                views.append(FolderView("f", root, socket_status=socket.folders.get(root),
                                        dbus_status=int(status.split()[1]),
                                        details=details[3:-1]))
            seen.append(compute_state(Inputs(running=True, api_available=True, folders=views,
                                             file_problems=dict(socket.problems))))
        self.assertIn("syncing", seen)
        self.assertEqual(seen[-1], "error")
        first_error = seen.index("error")
        self.assertNotIn("synced", seen[first_error:])


if __name__ == "__main__":
    unittest.main()
