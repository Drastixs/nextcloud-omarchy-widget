"""Client info log: file counts, current files, directions and errors."""
import gzip
import os
import re
import zlib
from collections import OrderedDict
from datetime import datetime

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):(\d{3}) \[.*?\]:\t(.*)$")
_RUN_START = re.compile(r"^>=+ Sync started for folder \[(.*)\] of account \[")
_RUN_END = re.compile(r"^<=+ Sync finished for folder \[(.*)\] of account \[")
_DISCOVERED = re.compile(
    r'^discovered "(.*)" CSyncEnums::CSYNC_INSTRUCTION_(\w+) '
    r"OCC::SyncFileItem::(\w+) CSyncEnums::(\w+)")
_STARTING = re.compile(
    r'^Starting CSyncEnums::CSYNC_INSTRUCTION_(\w+) propagation of "(.*)" by OCC::(\w+)\(')
_COMPLETED = re.compile(
    r'^Completed propagation of "(.*)" by OCC::\w+\(0x[0-9a-f]+\) '
    r"with status OCC::SyncFileItem::(\w+)")
_FAILED = re.compile(
    r'^Could not complete propagation of "(.*)" by OCC::\w+\(0x[0-9a-f]+\) '
    r"with status OCC::SyncFileItem::(\w+)")
_LOG_NAME = re.compile(r"^(\d{8}_\d{4})_nextcloud\.log\.(\d+)(\.gz)?$")

_HARMLESS = {"FileIgnored", "NoStatus", "Restoration"}
_HISTORY = 500
REPLAY_FILES = 5


def _parse_time(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp())


def _kind_from_instruction(instruction, direction):
    if instruction == "REMOVE":
        return "delete"
    if instruction == "CONFLICT":
        return "conflict"
    if direction == "Up":
        return "upload"
    if direction == "Down":
        return "download"
    return None


def _kind_from_job(job):
    if "Upload" in job:
        return "upload"
    if "Download" in job:
        return "download"
    if "Delete" in job or "Remove" in job:
        return "delete"
    return None


def _remember(mapping, key, value):
    mapping.pop(key, None)
    mapping[key] = value
    while len(mapping) > _HISTORY:
        mapping.popitem(last=False)


class LogParser:
    """Feed lines in order. Holds what is known about the run and recent files."""

    def __init__(self):
        self.in_run = False
        self.run_folder = None
        self.run_started_at = None
        self.planned = {}  # rel: kind, for files this run will touch
        self.directories = set()
        self.done = set()
        self.active = OrderedDict()  # rel: started; last is the latest start
        self.last_finished = None
        self.last_sync_finished_at = None
        self.kinds = OrderedDict()  # (folder, rel): kind, or None for unknown
        self.problems = OrderedDict()  # (folder, rel): (kind, time)

    def feed(self, line):
        match = _LINE.match(line.rstrip("\r\n"))
        if not match:
            return
        message = match[3]
        # Cheapest tests first: most lines are none of these.
        head = message[:4]
        if head in (">===", "<==="):
            self._boundary(message, match[1])
        elif head == "disc":
            self._discovered(message)
        elif head == "Star":
            self._started(message, match[1])
        elif head == "Comp":
            self._completed(message, match[1])
        elif head == "Coul":
            self._failed(message, match[1])

    def _boundary(self, message, stamp):
        started = _RUN_START.match(message)
        if started:
            self.in_run = True
            self.run_folder = started[1]
            self.run_started_at = _parse_time(stamp)
            self.planned = {}
            self.directories = set()
            self.done = set()
            self.active.clear()
            return
        finished = _RUN_END.match(message)
        if finished:
            self.in_run = False
            self.last_sync_finished_at = _parse_time(stamp)
            self.run_folder = finished[1]
            self.run_started_at = None
            self.planned = {}
            self.done = set()
            self.active.clear()

    def _discovered(self, message):
        match = _DISCOVERED.match(message)
        if not match:
            return
        rel, instruction, direction, item_type = match.groups()
        if item_type != "ItemTypeFile":
            self.directories.add(rel)
        elif instruction != "UPDATE_METADATA" and self.in_run:
            self.planned[rel] = _kind_from_instruction(instruction, direction)

    def _started(self, message, stamp):
        match = _STARTING.match(message)
        if not match:
            return
        instruction, rel, job = match.groups()
        if job == "PropagateIgnoreJob" or rel in self.directories or "Mkdir" in job:
            return
        self.active.pop(rel, None)
        self.active[rel] = _parse_time(stamp)
        if rel not in self.planned:
            kind = _kind_from_instruction(instruction, None) or _kind_from_job(job)
            _remember(self.kinds, (self.run_folder, rel), kind)

    def _completed(self, message, stamp):
        match = _COMPLETED.match(message)
        if not match:
            return
        rel, status = match.groups()
        self._finish(rel, status, stamp, failed=False)

    def _failed(self, message, stamp):
        match = _FAILED.match(message)
        if not match:
            return
        rel, status = match.groups()
        self._finish(rel, status, stamp, failed=True)

    def _finish(self, rel, status, stamp, failed):
        self.active.pop(rel, None)
        key = (self.run_folder, rel)
        when = _parse_time(stamp)
        if status == "Conflict":
            _remember(self.problems, key, ("conflict", when))
        elif failed and status not in _HARMLESS:
            _remember(self.problems, key, ("error", when))
        if failed or status != "Success":
            return
        self.problems.pop(key, None)
        if rel in self.directories:
            return
        if rel in self.planned:
            self.done.add(rel)
            _remember(self.kinds, key, self.planned[rel])
        else:
            _remember(self.kinds, key, self.kinds.get(key))
        self.last_finished = rel.rsplit("/", 1)[-1]

    def file_counts(self):
        """(done, total) for this run; None unless the helper saw it start."""
        if not self.in_run or not self.planned:
            return None, None
        return len(self.done), len(self.planned)

    def current_file(self):
        """The latest file started, plus how many others are still running."""
        if not self.active:
            return None
        latest = next(reversed(self.active))
        name = latest.rsplit("/", 1)[-1]
        extra = len(self.active) - 1
        return f"{name} +{extra} more" if extra else name


def log_key(name):
    """Sort key shared by a log and its gzipped form. None if not a client log."""
    match = _LOG_NAME.match(name)
    return (match[1], int(match[2])) if match else None


class LogFollower:
    """Follow the newest client log and feed lines to a LogParser.

    The client rotates about once a minute and gzips the old file, so a file is
    held open by descriptor until a newer one appears. Files that appear and rotate
    between polls are read through their gz form.
    """

    def __init__(self, directory, parser, replay=REPLAY_FILES):
        self.directory = directory
        self.parser = parser
        self.replay = replay
        self.key = None
        self.handle = None
        self.partial = b""

    def close(self):
        if self.handle:
            self.handle.close()
        self.handle = None
        self.partial = b""

    def _listing(self):
        entries = {}
        for name in os.listdir(self.directory):
            key = log_key(name)
            if key is None:
                continue
            # The plain file wins, as that is the one still being written.
            if key not in entries or not name.endswith(".gz"):
                entries[key] = name
        return sorted(entries.items())

    def _drain(self):
        data = self.partial + self.handle.read()
        lines = data.split(b"\n")
        self.partial = lines.pop()
        for line in lines:
            self.parser.feed(line.decode("utf-8", "replace"))

    def _read_whole(self, name):
        path = os.path.join(self.directory, name)
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as handle:
            for line in handle:
                self.parser.feed(line.decode("utf-8", "replace"))

    def poll(self):
        """Read anything new. Returns False if the logs can't be read."""
        try:
            entries = self._listing()
            if self.key is None:
                todo = entries[-self.replay:]
            else:
                todo = [entry for entry in entries if entry[0] > self.key]
            if self.handle:
                self._drain()
            if not todo:
                return bool(entries)
            self.close()
            for key, name in todo[:-1]:
                try:
                    self._read_whole(name)
                except (OSError, EOFError, zlib.error):
                    pass
                self.key = key
            key, name = todo[-1]
            if name.endswith(".gz"):
                self._read_whole(name)
            else:
                self.handle = open(os.path.join(self.directory, name), "rb")
                self._drain()
            self.key = key
            return True
        except (OSError, EOFError, zlib.error):
            return False
