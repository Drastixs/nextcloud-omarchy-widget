"""Combine the sources into the protocol's state object."""
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .dbus import STATUS_ERROR_OR_PAUSED, STATUS_SYNCING
from .statusdetails import Details, parse_status_details

SCHEMA_VERSION = 1


@dataclass
class FolderView:
    """One folder, merged across nextcloud.cfg, D-Bus and the socket, matched by path."""
    name: str
    path: str
    target: str = "/"
    socket_status: str = None
    dbus_status: int = None
    details: str = None
    paused: bool = False
    object_path: str = None

    @property
    def parsed(self):
        return parse_status_details(self.details)


@dataclass
class Inputs:
    running: bool = False
    api_available: bool = False
    folders: list = field(default_factory=list)
    file_problems: dict = field(default_factory=dict)  # path: (status, first seen)
    quota_warning: bool = False


def is_connected(inputs):
    with_details = [f for f in inputs.folders if f.details is not None]
    return bool(with_details) and all(f.parsed.connected for f in with_details)


def _has_error(inputs):
    for folder in inputs.folders:
        if folder.dbus_status == STATUS_ERROR_OR_PAUSED and not folder.paused:
            return True
        if folder.socket_status in ("IGNORE", "ERROR"):
            return True
        # With the socket gone, this text is what's left of a failed upload.
        if folder.socket_status is None and folder.parsed.kind == "ignored":
            return True
    return any(status == "ERROR" for status, _ in inputs.file_problems.values())


def _is_syncing(inputs):
    return any(f.dbus_status == STATUS_SYNCING or f.socket_status == "SYNC"
               for f in inputs.folders)


def compute_state(inputs):
    """PRD "Bar icon" table, first match wins."""
    if not inputs.running:
        return "client-down"
    if not inputs.api_available or not is_connected(inputs):
        return "disconnected"
    if inputs.folders and all(f.paused for f in inputs.folders):
        return "paused"
    if _has_error(inputs):
        return "error"
    if _is_syncing(inputs):
        return "syncing"
    if inputs.quota_warning or any(f.socket_status == "WARNING" for f in inputs.folders) or any(
            status == "WARNING" for status, _ in inputs.file_problems.values()):
        return "warning"
    return "synced"


def summary_for(state, inputs):
    if state == "disconnected":
        if not inputs.api_available:
            return "Client API unavailable"
        for folder in inputs.folders:
            if folder.details is not None and not folder.parsed.connected:
                return folder.parsed.account_state or "Disconnected"
        return "Disconnected"
    if state == "error":
        failed = sum(1 for status, _ in inputs.file_problems.values() if status == "ERROR")
        if failed:
            return "1 file failed" if failed == 1 else f"{failed} files failed"
        return "Sync error"
    if state == "warning":
        return "Storage nearly full" if inputs.quota_warning else "Sync warning"
    return {"client-down": "Client not running", "paused": "Paused",
            "syncing": "Syncing…", "synced": "Up to date"}[state]


def is_transfer_active(inputs):
    """Pause doesn't stop a running transfer, so this doesn't look at pause at all."""
    return _is_syncing(inputs) or any(f.parsed.kind == "syncing" for f in inputs.folders)


def pick_details(folders):
    """The status text with the most numbers, among folders that are syncing."""
    best = Details()
    best_score = -1
    for folder in folders:
        details = folder.parsed
        if details.kind != "syncing":
            continue
        score = sum(value is not None for value in
                    (details.total_bytes, details.eta_sec, details.files_total))
        if score > best_score:
            best, best_score = details, score
    return best


class RunTracker:
    """Per-run values: when the run started, and whether the ETA can be believed yet.

    A run's first "(0 seconds left)" is false, so 0 is withheld until a non-zero ETA has
    been seen. Count-shaped text on deletes only ever says 0, so it stays hidden.
    """

    def __init__(self):
        self.started_at = None
        self.eta_trusted = False
        self.last_finished_at = None

    def reset(self):
        self.started_at = None
        self.eta_trusted = False

    def update(self, now, active, eta_sec, log_started_at=None):
        """Returns (trusted ETA or None, progress or None)."""
        if not active:
            if self.started_at is not None:
                self.last_finished_at = int(now)
            self.reset()
            return None, None
        if self.started_at is None:
            self.started_at = now
        if log_started_at is not None and log_started_at < self.started_at:
            self.started_at = log_started_at
        if eta_sec:
            self.eta_trusted = True
        if eta_sec is None or not self.eta_trusted:
            return None, None
        elapsed = max(0, now - self.started_at)
        if elapsed + eta_sec <= 0:
            return eta_sec, None
        return eta_sec, round(elapsed / (elapsed + eta_sec), 3)


def build_transfer(active, picked, eta_sec, progress, rates, counts, current, last_finished):
    up, down = rates
    if not active:
        return {"active": False, "upBps": up, "downBps": down, "totalBytes": None,
                "etaSec": None, "bytesLeft": None, "progress": None, "filesDone": None,
                "filesTotal": None, "currentFile": None, "lastFinishedFile": last_finished}
    speed = (up or 0) + (down or 0)
    done, total = counts
    if total is None:  # the log didn't see the run start; use the text if it has a count
        done, total = picked.files_done, picked.files_total
    return {
        "active": True, "upBps": up, "downBps": down, "totalBytes": picked.total_bytes,
        "etaSec": eta_sec,
        "bytesLeft": int(speed * eta_sec) if eta_sec is not None and speed > 0 else None,
        "progress": progress, "filesDone": done, "filesTotal": total,
        "currentFile": current, "lastFinishedFile": last_finished,
    }


def account_label(user, url):
    if not user or not url:
        return None
    return f"{user}@{urlsplit(url).netloc}"


def quota_over_limit(used, total, percent):
    return bool(total) and used is not None and used * 100 >= total * percent
