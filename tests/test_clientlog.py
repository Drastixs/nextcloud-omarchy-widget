import gzip
import os
import tempfile
import unittest

from support import fixture
from ncstatus.clientlog import LogFollower, LogParser, log_key

HEAD = "2026-11-01 21:56:0{}:000 [ info nextcloud.sync x.cpp:1 ]:\t"
ACCOUNT = " of account [alice@nextcloud.example.com] with remote [x]"


def line(text, second=0):
    return HEAD.format(second) + text


def started(folder="Documents"):
    return line(f">========== Sync started for folder [{folder}]{ACCOUNT}")


def finished(folder="Documents"):
    return line(f"<========== Sync finished for folder [{folder}]{ACCOUNT}")


def discovered(rel, instruction="NEW", direction="Up", item="ItemTypeFile"):
    return line(f'discovered "{rel}" CSyncEnums::CSYNC_INSTRUCTION_{instruction} '
                f"OCC::SyncFileItem::{direction} CSyncEnums::{item}")


def starting(rel, job="PropagateUploadFileV1", instruction="NEW"):
    return line(f'Starting CSyncEnums::CSYNC_INSTRUCTION_{instruction} propagation of "{rel}" '
                f"by OCC::{job}(0x5639)")


def completed(rel, status="Success"):
    return line(f'Completed propagation of "{rel}" by OCC::OwncloudPropagator(0x56) '
                f"with status OCC::SyncFileItem::{status}")


def failed(rel, status="BlacklistedError"):
    return line(f'Could not complete propagation of "{rel}" by OCC::OwncloudPropagator(0x56) '
                f'with status OCC::SyncFileItem::{status} and error: "boom"')


def feed(*lines):
    parser = LogParser()
    for text in lines:
        parser.feed(text)
    return parser


class Runs(unittest.TestCase):
    def test_counts_through_a_run(self):
        parser = feed(started(), discovered("a"), discovered("b"), discovered("c"),
                      starting("a"), completed("a"))
        self.assertEqual(parser.file_counts(), (1, 3))

    def test_directories_and_metadata_are_not_counted(self):
        parser = feed(started(), discovered("dir", item="ItemTypeDirectory"),
                      discovered("old", "UPDATE_METADATA", "Down"), discovered("dir/a"),
                      starting("dir", "PropagateRemoteMkdir"), completed("dir"),
                      starting("dir/a"), completed("dir/a"))
        self.assertEqual(parser.file_counts(), (1, 1))
        self.assertEqual(parser.last_finished, "a")

    def test_started_mid_run_hides_the_count(self):
        parser = feed(discovered("a"), starting("a"), starting("b"), completed("a"))
        self.assertEqual(parser.file_counts(), (None, None))
        self.assertEqual(parser.current_file(), "b")  # names still work

    def test_no_count_outside_a_run(self):
        parser = feed(started(), discovered("a"), completed("a"), finished())
        self.assertEqual(parser.file_counts(), (None, None))
        self.assertIsNone(parser.current_file())
        self.assertIsNotNone(parser.last_sync_finished_at)

    def test_no_op_run_has_no_count(self):
        self.assertEqual(feed(started()).file_counts(), (None, None))


class CurrentFile(unittest.TestCase):
    def test_three_parallel_shows_latest_plus_more(self):
        parser = feed(started(), starting("dir/m5-0"), starting("dir/m5-1"), starting("dir/m5-2"))
        self.assertEqual(parser.current_file(), "m5-2 +2 more")

    def test_completion_out_of_start_order(self):
        parser = feed(started(), starting("0"), starting("1"), starting("2"), completed("0"),
                      starting("3"), completed("3"), starting("4"), completed("4"))
        self.assertEqual(parser.current_file(), "2 +1 more")

    def test_failure_also_ends_a_file(self):
        parser = feed(started(), starting("a", "PropagateUploadFileNG"), failed("a", "NormalError"))
        self.assertIsNone(parser.current_file())

    def test_blacklist_job_is_not_a_transfer(self):
        parser = feed(started(), starting("a", "PropagateIgnoreJob", "IGNORE"))
        self.assertIsNone(parser.current_file())


class Outcomes(unittest.TestCase):
    def test_directions(self):
        parser = feed(started(), discovered("up"), discovered("down", direction="Down"),
                      discovered("gone", "REMOVE"), completed("up"), completed("down"),
                      completed("gone"))
        self.assertEqual(parser.kinds[("Documents", "up")], "upload")
        self.assertEqual(parser.kinds[("Documents", "down")], "download")
        self.assertEqual(parser.kinds[("Documents", "gone")], "delete")

    def test_direction_from_job_when_not_discovered(self):
        parser = feed(started(), starting("a", "PropagateDownloadFile"), completed("a"))
        self.assertEqual(parser.kinds[("Documents", "a")], "download")

    def test_error_then_success_clears(self):
        parser = feed(started(), discovered("a"), failed("a"))
        self.assertEqual(parser.problems[("Documents", "a")][0], "error")
        parser.feed(completed("a"))
        self.assertEqual(len(parser.problems), 0)

    def test_ignored_is_not_an_error(self):
        self.assertEqual(len(feed(started(), failed("link", "FileIgnored")).problems), 0)

    def test_conflict(self):
        parser = feed(started(), completed("a", "Conflict"))
        self.assertEqual(parser.problems[("Documents", "a")][0], "conflict")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.lines = fixture("client-log-excerpt.log").splitlines()

    def test_five_file_run(self):
        parser = LogParser()
        seen = []
        for text in self.lines:
            parser.feed(text)
            done, total = parser.file_counts()
            if total == 5:
                seen.append(done)
        self.assertEqual(max(seen), 5)

    def test_upload_run_peaks_at_three(self):
        parser = LogParser()
        peak = 0
        for text in self.lines:
            if "CSYNC_INSTRUCTION_REMOVE propagation" in text:
                break  # deletes run wider than uploads, and "+N more" copes with either
            parser.feed(text)
            peak = max(peak, len(parser.active))
        self.assertEqual(peak, 3)

    def test_ends_clean(self):
        parser = LogParser()
        for text in self.lines:
            parser.feed(text)
        self.assertFalse(parser.in_run)
        self.assertEqual(len(parser.active), 0)
        self.assertEqual(parser.run_folder, "Documents")
        self.assertTrue(parser.last_finished.startswith("nc-widget-spike"))
        # The failed upload has no later Success in this excerpt; it must stay pinned.
        self.assertEqual(parser.problems[("Documents", "nc-widget-spike-delete-me.bin")][0], "error")

    def test_folder_name_with_a_slash(self):
        text = next(l for l in self.lines if "[Pictures/Screenshots]" in l and "started" in l)
        self.assertEqual(feed(text).run_folder, "Pictures/Screenshots")

    def test_garbage_is_ignored(self):
        parser = LogParser()
        for text in ("", "not a log line", "2026-11-01 21:56:00:000 no brackets"):
            parser.feed(text)
        self.assertFalse(parser.in_run)


class Follower(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.parser = LogParser()
        self.follower = LogFollower(self.dir, self.parser)
        self.addCleanup(self.follower.close)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, *lines, mode="w"):
        with open(self.path(name), mode) as handle:
            handle.write("".join(text + "\n" for text in lines))

    def rotate(self, name):
        with open(self.path(name), "rb") as plain, gzip.open(self.path(name + ".gz"), "wb") as out:
            out.write(plain.read())
        os.unlink(self.path(name))

    def test_sort_key(self):
        self.assertEqual(log_key("20261101_2203_nextcloud.log.1.gz"), ("20261101_2203", 1))
        self.assertEqual(log_key("20261101_2203_nextcloud.log.1"), ("20261101_2203", 1))
        self.assertIsNone(log_key("20261101_2203_permanent_delete.log.0"))

    def test_startup_replays_gz_history(self):
        self.write("x.txt", "unrelated")
        with gzip.open(self.path("20261101_2200_nextcloud.log.0.gz"), "wt") as out:
            out.write(started() + "\n" + discovered("a") + "\n")
        self.write("20261101_2201_nextcloud.log.0", discovered("b"), completed("a"))
        self.assertTrue(self.follower.poll())
        self.assertEqual(self.parser.file_counts(), (1, 2))

    def test_keeps_reading_the_tail(self):
        self.write("20261101_2200_nextcloud.log.0", started())
        self.follower.poll()
        self.write("20261101_2200_nextcloud.log.0", discovered("a"), mode="a")
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (0, 1))

    def test_partial_line_waits_for_its_end(self):
        self.write("20261101_2200_nextcloud.log.0", started())
        self.follower.poll()
        text = discovered("a")
        with open(self.path("20261101_2200_nextcloud.log.0"), "a") as handle:
            handle.write(text[:30])
        self.follower.poll()
        with open(self.path("20261101_2200_nextcloud.log.0"), "a") as handle:
            handle.write(text[30:] + "\n")
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (0, 1))

    def test_rotation_loses_nothing(self):
        self.write("20261101_2200_nextcloud.log.0", started())
        self.follower.poll()
        # Written after the last poll, then rotated away before the next.
        self.write("20261101_2200_nextcloud.log.0", discovered("a"), mode="a")
        self.rotate("20261101_2200_nextcloud.log.0")
        self.write("20261101_2201_nextcloud.log.0", discovered("b"))
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (0, 2))
        self.write("20261101_2201_nextcloud.log.0", completed("b"), mode="a")
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (1, 2))

    def test_a_file_that_came_and_went_between_polls(self):
        self.write("20261101_2200_nextcloud.log.0", started())
        self.follower.poll()
        self.write("20261101_2201_nextcloud.log.0", discovered("a"))
        self.rotate("20261101_2201_nextcloud.log.0")
        self.write("20261101_2202_nextcloud.log.0", discovered("b"))
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (0, 2))

    def test_order_within_a_minute(self):
        self.write("20261101_2200_nextcloud.log.1", discovered("late"))
        self.write("20261101_2200_nextcloud.log.0", started())
        self.follower.poll()
        self.assertEqual(self.parser.file_counts(), (0, 1))

    def test_missing_directory_is_not_fatal_and_recovers(self):
        follower = LogFollower(self.path("logs"), self.parser)
        self.assertFalse(follower.poll())
        os.mkdir(self.path("logs"))
        with open(self.path("logs/20261101_2200_nextcloud.log.0"), "w") as handle:
            handle.write(started() + "\n")
        self.assertTrue(follower.poll())
        follower.close()
        self.assertTrue(self.parser.in_run)

    def test_corrupt_gz_is_skipped(self):
        with open(self.path("20261101_2200_nextcloud.log.0.gz"), "wb") as handle:
            handle.write(b"not gzip")
        self.write("20261101_2201_nextcloud.log.0", started())
        self.assertTrue(self.follower.poll())
        self.assertTrue(self.parser.in_run)


if __name__ == "__main__":
    unittest.main()
