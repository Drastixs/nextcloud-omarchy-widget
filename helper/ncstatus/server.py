"""Nextcloud server: quota, notifications, activity and share links.

The password is held in memory only. It is sent in a request header, never put in a
command line or a log message, and redirects are refused so it can't reach another host.
"""
import base64
import binascii
import http.client
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

TIMEOUT_SEC = 10
_DAV = "{DAV:}"
_CHANGE_TYPES = ("file_created", "file_changed", "file_restored", "file_renamed", "file_moved")


class ServerError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# Keyring ------------------------------------------------------------------------------

def parse_keyring(text):
    """`secret-tool search --all` stdout to a list of (label, stored secret)."""
    items = []
    label = None
    for line in text.splitlines():
        if line.startswith("["):
            label = None
        elif line.startswith("label = "):
            label = line[len("label = "):]
        elif line.startswith("secret = ") and label is not None:
            items.append((label, line[len("secret = "):]))
    return items


def decode_secret(stored):
    """Base64-decode exactly once. The result also looks like base64, so never loop."""
    try:
        return base64.b64decode(stored, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None


def select_password(items, user, url):
    """Select by label, as this secret-tool build gives no usable attribute lines."""
    wanted = f"{user}_app-password:{url.rstrip('/')}/:0@Nextcloud"
    for label, stored in items:
        if label == wanted:
            return decode_secret(stored)
    return None


def load_password(user, url):
    try:
        result = subprocess.run(
            ["secret-tool", "search", "--all", "xdg:schema", "org.qt.keychain"],
            capture_output=True, text=True, timeout=TIMEOUT_SEC, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return select_password(parse_keyring(result.stdout), user, url)


# Parsing ------------------------------------------------------------------------------

def parse_quota(xml_bytes):
    """Returns (used, total). Negative available, such as -3, means no limit: total None."""
    root = ET.fromstring(xml_bytes)
    used = root.find(f".//{_DAV}quota-used-bytes")
    available = root.find(f".//{_DAV}quota-available-bytes")
    try:
        used_bytes = int(used.text)
    except (AttributeError, TypeError, ValueError):
        return None, None
    try:
        free = int(available.text)
    except (AttributeError, TypeError, ValueError):
        return used_bytes, None
    return used_bytes, used_bytes + free if free >= 0 else None


def _ocs_data(body):
    if not body:
        return []
    return json.loads(body).get("ocs", {}).get("data") or []


def parse_notifications(body):
    return len(_ocs_data(body))


def parse_share_url(body):
    data = _ocs_data(body)
    url = data.get("url") if isinstance(data, dict) else None
    if not url:
        raise ServerError("Server gave no link")
    return url


def _parse_time(text):
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError):
        return None


def to_local(server_path, folders):
    """Map a server path through targetPath. Returns (folder, local path) or None."""
    best = None
    for folder in folders:
        target = folder.target.rstrip("/")
        if server_path.startswith(target + "/"):
            if best is None or len(target) > len(best.target.rstrip("/")):
                best = folder
    if best is None:
        return None
    rel = server_path[len(best.target.rstrip("/")) + 1:]
    return best, f"{best.path}/{rel}"


def to_server(local_path, folders, realpath=os.path.realpath):
    """Map a local path into a synced folder. None if outside them all, after symlinks."""
    resolved = realpath(local_path)
    for folder in folders:
        root = realpath(folder.path)
        if resolved == root:
            return folder.target
        if resolved.startswith(root.rstrip("/") + "/"):
            return f"{folder.target.rstrip('/')}/{resolved[len(root.rstrip('/')) + 1:]}"
    return None


def is_conflict_name(name):
    return "(conflict" in name.lower()


def normalise_activity(entries, folders, kinds):
    """Server activity to protocol rows, newest first.

    One entry can cover several files, so objects expands to one row each. kinds maps
    (folder name, relative path) to the direction from the log, as the API has none.
    The kind is None when no log line says which way a file went.
    """
    rows = []
    seen = set()
    for entry in entries:
        kind_type = entry.get("type")
        deleted = kind_type == "file_deleted"
        if not deleted and kind_type not in _CHANGE_TYPES:
            continue
        objects = entry.get("objects")
        if not isinstance(objects, dict) or not objects:
            objects = {"": entry.get("object_name", "")}
        when = _parse_time(entry.get("datetime"))
        for server_path in objects.values():
            mapped = isinstance(server_path, str) and to_local(server_path, folders)
            if not mapped:
                continue
            folder, local = mapped
            if local in seen:  # newest first, so the first row for a path wins
                continue
            seen.add(local)
            name = local.rsplit("/", 1)[-1]
            if deleted:
                kind = "delete"
            elif is_conflict_name(name):
                kind = "conflict"
            else:
                kind = kinds.get((folder.name, local[len(folder.path) + 1:]))
            rows.append({"kind": kind, "name": name, "folder": folder.name,
                         "path": local, "time": when})
    return rows


def rows_from_paths(paths, folders):
    """Recent-menu fallback: the menu has neither time nor direction."""
    rows = []
    for path in paths:
        for folder in folders:
            if path.startswith(folder.path.rstrip("/") + "/"):
                rows.append({"kind": None, "name": path.rsplit("/", 1)[-1],
                             "folder": folder.name, "path": path, "time": None})
                break
    return rows


def merge_activity(rows, pinned, limit):
    """Errors and conflicts first, then newest first, cut to limit."""
    pinned_paths = {row["path"] for row in pinned}
    merged = pinned + [row for row in rows if row["path"] not in pinned_paths]
    merged.sort(key=lambda row: (row["kind"] not in ("error", "conflict"), -(row["time"] or 0)))
    return merged[:limit]


# HTTP ---------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # urllib would otherwise replay the Authorization header


class Client:
    def __init__(self, url, user, password):
        self.url = url.rstrip("/")
        self.user = user
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "OCS-APIRequest": "true",
                         "Accept": "application/json"}
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, method, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={**self._headers, **(headers or {})})
        try:
            with self._opener.open(request, timeout=TIMEOUT_SEC) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            error.close()
            if error.code in (204, 304):
                return b""
            raise ServerError(f"Server returned {error.code}", error.code) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            raise ServerError("Server unreachable") from None

    @staticmethod
    def _parsed(parse, body):
        """A proxy or portal can answer with anything."""
        try:
            return parse(body)
        except (ValueError, AttributeError, TypeError, ET.ParseError):
            raise ServerError("Bad server response") from None

    def quota(self):
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                "<d:quota-used-bytes/><d:quota-available-bytes/></d:prop></d:propfind>")
        user = urllib.parse.quote(self.user, safe="")
        reply = self._request("PROPFIND", f"/remote.php/dav/files/{user}/", body.encode(),
                              {"Depth": "0", "Content-Type": "application/xml"})
        return self._parsed(parse_quota, reply)

    def notifications(self):
        return self._parsed(parse_notifications, self._request(
            "GET", "/ocs/v2.php/apps/notifications/api/v2/notifications?format=json"))

    def activity(self, limit):
        reply = self._request(
            "GET", f"/ocs/v2.php/apps/activity/api/v2/activity?format=json&limit={limit}")
        return self._parsed(_ocs_data, reply)

    def share(self, server_path):
        body = urllib.parse.urlencode(
            {"path": server_path, "shareType": 3, "permissions": 1}).encode()
        reply = self._request(
            "POST", "/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        return self._parsed(parse_share_url, reply)
