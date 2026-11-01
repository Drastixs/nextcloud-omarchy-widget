import unittest

from support import fixture
from ncstatus.config import parse_cfg


class Cfg(unittest.TestCase):
    def setUp(self):
        self.config = parse_cfg(fixture("nextcloud.cfg"))

    def test_account(self):
        self.assertEqual(self.config.url, "https://nextcloud.example.com")
        self.assertEqual(self.config.user, "alice")

    def test_folders_have_no_trailing_slash(self):
        self.assertEqual([f.path for f in self.config.folders],
                         ["/home/alice/Documents", "/home/alice/Pictures/Screenshots"])
        self.assertEqual(self.config.folders[0].target, "/Sync/Documents")
        self.assertEqual(self.config.folders[1].target, "/Sync/Screenshots")

    def test_paused_flag(self):
        text = fixture("nextcloud.cfg").replace(r"0\Folders\3\paused=false", r"0\Folders\3\paused=true")
        self.assertEqual([f.paused for f in parse_cfg(text).folders], [False, True])

    def test_trailing_slash_on_url_removed(self):
        text = "[Accounts]\n0\\url=https://x.example/\n0\\dav_user=a\n"
        self.assertEqual(parse_cfg(text).url, "https://x.example")

    def test_quoted_value(self):
        text = '[Accounts]\n0\\url=https://x.example\n0\\Folders\\1\\localPath="/home/a/My Files/"\n'
        self.assertEqual(parse_cfg(text).folders[0].path, "/home/a/My Files")

    def test_placeholder_folders_are_included(self):
        text = ("[Accounts]\n0\\url=https://x.example\n"
                "0\\FoldersWithPlaceholders\\2\\localPath=/a/\n"
                "0\\FoldersWithPlaceholders\\2\\targetPath=/\n")
        self.assertEqual(parse_cfg(text).folders[0].path, "/a")

    def test_no_account(self):
        self.assertIsNone(parse_cfg("[General]\nclientVersion=1\n"))
        self.assertIsNone(parse_cfg(""))


if __name__ == "__main__":
    unittest.main()
