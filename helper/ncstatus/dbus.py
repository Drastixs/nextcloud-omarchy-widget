"""Client D-Bus API through busctl.

Every call passes --auto-start=no: the client ships a D-Bus service file, and a status
widget must never launch it.
"""
import json
import os
import subprocess
from dataclasses import dataclass

BUS_NAME = "com.nextcloudgmbh.Nextcloud"
FOLDER_PREFIX = "/com/nextcloudgmbh/Nextcloud/Folder/"
ACCOUNT_IFACE = "org.freedesktop.CloudProviders.Account"
ACTIONS_IFACE = "org.gtk.Actions"
MENUS_IFACE = "org.gtk.Menus"
STATUS_IDLE, STATUS_SYNCING, STATUS_ERROR_OR_PAUSED = 1, 2, 3


@dataclass
class DbusFolder:
    object_path: str
    name: str
    path: str
    status: int
    details: str
    paused: bool = None


def parse_tree(text):
    """Folder object paths from `busctl tree --list`, sorted by number."""
    objects = [l.strip() for l in text.splitlines() if l.strip().startswith(FOLDER_PREFIX)]
    return sorted(objects, key=lambda p: (len(p), p))


def parse_json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def parse_describe(reply):
    """Paused flag from an org.gtk.Actions Describe reply.

    busctl gives {"data": [[enabled, "", [{"type": "b", "data": paused}]]]}.
    """
    try:
        state = reply["data"][0][2]
        return bool(state[0]["data"]) if state else None
    except (KeyError, IndexError, TypeError):
        return None


def parse_recent_menu(reply):
    """Local paths from the "Recently changed" group, in menu order."""
    paths = []
    try:
        groups = reply["data"][0]
    except (KeyError, IndexError, TypeError):
        return paths
    for group in groups:
        for item in group[2]:
            target = item.get("target", {}).get("data")
            if isinstance(target, str) and target.startswith("/"):
                paths.append(target)
    return paths


class Bus:
    TIMEOUT_SEC = 3

    def _run(self, *args):
        """stdout of a busctl call, or None on any failure."""
        env = dict(os.environ, LC_ALL="C")
        try:
            result = subprocess.run(
                ["busctl", "--user", "--auto-start=no", "--json=short", *args],
                capture_output=True, text=True, timeout=self.TIMEOUT_SEC, env=env,
                stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    def _call(self, *args):
        output = self._run(*args)
        if output is None:
            return None
        try:
            replies = parse_json_lines(output)
        except ValueError:
            return None
        return replies[0] if replies else {}

    def client_present(self):
        reply = self._call("call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "NameHasOwner", "s", BUS_NAME)
        return bool(reply and reply.get("data", [False])[0])

    def client_pid(self):
        reply = self._call("call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", BUS_NAME)
        return reply.get("data", [None])[0] if reply else None

    def folder_objects(self):
        output = self._run("tree", "--list", BUS_NAME)
        return parse_tree(output) if output else []

    def read_folder(self, object_path):
        output = self._run("get-property", BUS_NAME, object_path, ACCOUNT_IFACE,
                           "Name", "Path", "Status", "StatusDetails")
        if output is None:
            return None
        try:
            replies = parse_json_lines(output)
            name, path, status, details = [reply["data"] for reply in replies]
        except (ValueError, KeyError, TypeError):
            return None
        return DbusFolder(object_path, name, path.rstrip("/") or "/", status, details,
                          self.describe(object_path))

    def folders(self):
        found = (self.read_folder(obj) for obj in self.folder_objects())
        return [folder for folder in found if folder is not None]

    def describe(self, object_path):
        """True or False from the client, None if it couldn't be read."""
        reply = self._call("call", BUS_NAME, object_path, ACTIONS_IFACE, "Describe", "s", "pause")
        return parse_describe(reply) if reply else None

    def activate(self, object_path, action):
        return self._run("call", BUS_NAME, object_path, ACTIONS_IFACE, "Activate",
                         "sava{sv}", action, "0", "0") is not None

    def recent_paths(self, object_path):
        reply = self._call("call", BUS_NAME, object_path, MENUS_IFACE, "Start", "au", "1", "1")
        self._run("call", BUS_NAME, object_path, MENUS_IFACE, "End", "au", "1", "1")
        return parse_recent_menu(reply) if reply else []
