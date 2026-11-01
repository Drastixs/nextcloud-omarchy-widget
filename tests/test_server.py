import base64
import unittest
from types import SimpleNamespace

from support import fixture, fixture_json
from ncstatus import server


def folders():
    return [SimpleNamespace(name="Documents", path="/home/alice/Documents", target="/Sync/Documents"),
            SimpleNamespace(name="Pictures/Screenshots", path="/home/alice/Pictures/Screenshots",
                            target="/Sync/Screenshots")]


class Quota(unittest.TestCase):
    def test_minus_three_means_no_limit(self):
        self.assertEqual(server.parse_quota(fixture("webdav-quota.xml").encode()),
                         (10839714349, None))

    def test_limit(self):
        xml = fixture("webdav-quota.xml").replace(">-3<", ">1000<").encode()
        self.assertEqual(server.parse_quota(xml), (10839714349, 10839715349))

    def test_other_negative_values(self):
        for value in (">-1<", ">-2<"):
            xml = fixture("webdav-quota.xml").replace(">-3<", value).encode()
            self.assertIsNone(server.parse_quota(xml)[1])

    def test_missing_props(self):
        self.assertEqual(server.parse_quota(b'<d:multistatus xmlns:d="DAV:"/>'), (None, None))


class Ocs(unittest.TestCase):
    def test_notifications_count(self):
        self.assertEqual(server.parse_notifications(fixture("ocs-notifications.json")),
                         len(fixture_json("ocs-notifications.json")["ocs"]["data"]))

    def test_empty_body(self):
        self.assertEqual(server.parse_notifications(b""), 0)

    def test_share_url(self):
        self.assertTrue(server.parse_share_url(fixture("ocs-share-create.json")).startswith("https://"))

    def test_share_without_url(self):
        with self.assertRaises(server.ServerError):
            server.parse_share_url('{"ocs": {"data": []}}')


class Activity(unittest.TestCase):
    def setUp(self):
        self.entries = fixture_json("ocs-activity.json")["ocs"]["data"]

    def test_objects_expand_to_rows(self):
        rows = server.normalise_activity(self.entries[:1], folders(), {})
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["kind"] for r in rows}, {"delete"})
        self.assertEqual(rows[0]["folder"], "Documents")
        self.assertEqual(rows[0]["name"], "nc-widget-spike-sn2-delete-me.txt")
        self.assertEqual(rows[0]["path"], "/home/alice/Documents/nc-widget-spike-sn2-delete-me.txt")
        self.assertIsInstance(rows[0]["time"], int)

    def test_row_shape(self):
        for row in server.normalise_activity(self.entries, folders(), {}):
            self.assertEqual(set(row), {"kind", "name", "folder", "path", "time"})

    def test_shared_is_left_out(self):
        rows = server.normalise_activity([e for e in self.entries if e["type"] == "shared"],
                                         folders(), {})
        self.assertEqual(rows, [])

    def test_outside_synced_folders_is_left_out(self):
        entry = dict(self.entries[0], objects={"1": "/Other/x", "2": "/Sync/Documentsy/x", "3": "/x"})
        self.assertEqual(server.normalise_activity([entry], folders(), {}), [])

    def test_created_takes_direction_from_the_log(self):
        name = "nc-widget-spike-mid-delete-me.bin"
        entry = next(e for e in self.entries if e["type"] == "file_created")
        rows = server.normalise_activity([entry], folders(), {("Documents", name): "upload"})
        kinds = {row["name"]: row["kind"] for row in rows}
        self.assertEqual(kinds[name], "upload")

    def test_direction_unknown_is_null(self):
        entry = next(e for e in self.entries if e["type"] == "file_created")
        self.assertEqual({r["kind"] for r in server.normalise_activity([entry], folders(), {})}, {None})

    def test_conflict_by_name(self):
        entry = dict(self.entries[0], type="file_created",
                     objects={"1": "/Sync/Documents/budget (conflicted copy 2026-11-01).xlsx"})
        self.assertEqual(server.normalise_activity([entry], folders(), {})[0]["kind"], "conflict")

    def test_newest_row_wins_per_path(self):
        rows = server.normalise_activity(self.entries, folders(), {})
        paths = [r["path"] for r in rows]
        self.assertEqual(len(paths), len(set(paths)))
        row = next(r for r in rows if r["name"] == "nc-widget-spike-mid-delete-me.bin")
        self.assertEqual(row["kind"], "delete")

    def test_longest_target_wins(self):
        nested = folders() + [SimpleNamespace(name="Deep", path="/d", target="/Sync/Documents/deep")]
        self.assertEqual(server.to_local("/Sync/Documents/deep/x", nested)[1], "/d/x")

    def test_root_target(self):
        root = [SimpleNamespace(name="All", path="/home/alice/All", target="/")]
        self.assertEqual(server.to_local("/a/b", root)[1], "/home/alice/All/a/b")

    def test_recent_menu_rows(self):
        rows = server.rows_from_paths(["/home/alice/Documents/a.md", "/elsewhere/b"], folders())
        self.assertEqual(rows, [{"kind": None, "name": "a.md", "folder": "Documents",
                                 "path": "/home/alice/Documents/a.md", "time": None}])


class Merge(unittest.TestCase):
    @staticmethod
    def row(kind, path, when):
        return {"kind": kind, "name": path, "folder": "D", "path": path, "time": when}

    def test_pinned_first_then_newest(self):
        rows = [self.row("upload", "/old", 1), self.row("delete", "/new", 5)]
        merged = server.merge_activity(rows, [self.row("error", "/bad", 0)], 8)
        self.assertEqual([r["path"] for r in merged], ["/bad", "/new", "/old"])

    def test_pinned_replaces_the_server_row(self):
        merged = server.merge_activity([self.row("upload", "/bad", 9)],
                                       [self.row("error", "/bad", 1)], 8)
        self.assertEqual([r["kind"] for r in merged], ["error"])

    def test_limit(self):
        rows = [self.row("upload", f"/{n}", n) for n in range(20)]
        self.assertEqual(len(server.merge_activity(rows, [], 8)), 8)

    def test_unknown_time_sorts_last(self):
        merged = server.merge_activity([self.row(None, "/a", None), self.row(None, "/b", 3)], [], 8)
        self.assertEqual(merged[0]["path"], "/b")


class ShareMapping(unittest.TestCase):
    def setUp(self):
        self.identity = lambda path: path

    def test_inside(self):
        self.assertEqual(server.to_server("/home/alice/Documents/a b.md", folders(), self.identity),
                         "/Sync/Documents/a b.md")

    def test_outside(self):
        for path in ("/etc/passwd", "/home/alice/Documentsy/a"):
            self.assertIsNone(server.to_server(path, folders(), self.identity))

    def test_traversal(self):
        self.assertIsNone(server.to_server("/home/alice/Documents/../../../etc/passwd", folders()))


class Keyring(unittest.TestCase):
    URL = "https://nextcloud.example.com"

    def listing(self, stored):
        return (f"[/org/freedesktop/secrets/collection/login/1]\n"
                f"label = alice:{self.URL}/:0@Nextcloud\nsecret = raw-password\n"
                f"created = 2026-01-01\nschema = org.qt.keychain\n"
                f"[/org/freedesktop/secrets/collection/login/2]\n"
                f"label = alice_app-password:{self.URL}/:0@Nextcloud\nsecret = {stored}\n"
                f"[/org/freedesktop/secrets/collection/login/3]\n"
                f"label = Nextcloud\nsecret = other\n")

    def test_selects_by_label_and_decodes(self):
        stored = base64.b64encode(b"abc123").decode()
        items = server.parse_keyring(self.listing(stored))
        self.assertEqual(server.select_password(items, "alice", self.URL), "abc123")

    def test_decodes_exactly_once(self):
        # The real decoded value is itself valid base64, so a loop would corrupt it.
        once = base64.b64encode(b"hello world!").decode()
        twice = base64.b64encode(once.encode()).decode()
        self.assertEqual(server.decode_secret(twice), once)

    def test_url_trailing_slash(self):
        stored = base64.b64encode(b"x").decode()
        items = server.parse_keyring(self.listing(stored))
        self.assertEqual(server.select_password(items, "alice", self.URL + "/"), "x")

    def test_missing_or_bad(self):
        self.assertIsNone(server.select_password([], "alice", self.URL))
        items = server.parse_keyring(self.listing("not*base64"))
        self.assertIsNone(server.select_password(items, "alice", self.URL))


class Redirects(unittest.TestCase):
    def test_refused(self):
        self.assertIsNone(server._NoRedirect().redirect_request(None, None, 302, "", {}, "https://x"))


if __name__ == "__main__":
    unittest.main()
