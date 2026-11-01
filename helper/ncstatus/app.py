"""Runtime: threads, the main loop and the commands. See docs/helper-protocol.md."""
import argparse
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from . import actions, server
from .clientlog import LogFollower, LogParser
from .config import config_dir, load_config
from .dbus import BUS_NAME, Bus
from .socketproto import SocketClient, socket_path
from .speed import SpeedMeter, parse_ss
from .state import (SCHEMA_VERSION, FolderView, Inputs, RunTracker, account_label,
                    build_transfer, compute_state, is_connected, is_transfer_active,
                    pick_details, quota_over_limit, summary_for)

log = logging.getLogger("nextcloud-status")

LOOP_SEC = 0.5
DBUS_MIN_SEC = 0.5     # reads triggered by signals, which can arrive many times a second
DBUS_POLL_SEC = 6.0    # safety net in case dbus-monitor is missing
SPEED_SEC = 2.0
LOG_SEC = 1.0
SOCKET_POLL_SEC = 6.0
CONFIG_SEC = 10.0
RECENT_SEC = 30.0
SERVER_RETRY_SEC = 15.0
PROBLEM_MAX_AGE_SEC = 24 * 3600


def run_section(name, function, seen={}):
    """Run one section, keeping a failure to itself. Logs by type only, and only on a
    change, so no exception text can carry a secret and an outage can't flood stderr."""
    try:
        result = function()
        seen.pop(name, None)
        return result
    except Exception as error:  # noqa: BLE001 - the process must outlive any one section
        kind = type(error).__name__
        if seen.get(name) != kind:
            seen[name] = kind
            log.warning("%s failed: %s", name, kind)
        return None


class StdinReader(threading.Thread):
    """Reads with os.read, as a thread blocked in sys.stdin stops the interpreter exiting
    cleanly."""

    def __init__(self, events):
        super().__init__(name="stdin", daemon=True)
        self.events = events

    def run(self):
        buffer = b""
        try:
            while True:
                chunk = os.read(0, 4096)
                if not chunk:
                    break
                buffer += chunk
                lines = buffer.split(b"\n")
                buffer = lines.pop()
                for line in lines:
                    self.events.put(("command", line.decode("utf-8", "surrogateescape")))
        except OSError:
            pass
        self.events.put(("eof", None))


class SignalWatcher(threading.Thread):
    """Wakes the main loop on client signals. Any failure just leaves the polling."""

    RETRY_SEC = 10

    def __init__(self, notify):
        super().__init__(name="dbus-monitor", daemon=True)
        self.notify = notify
        self.process = None
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()
        process = self.process
        if process:
            process.terminate()

    def run(self):
        rules = [f"type='signal',sender='{BUS_NAME}'",
                 f"type='signal',member='NameOwnerChanged',arg0='{BUS_NAME}'"]
        while not self.stopping.is_set():
            try:
                self.process = subprocess.Popen(
                    ["dbus-monitor", "--session", *rules], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
                for line in self.process.stdout:
                    if line.startswith("signal"):
                        self.notify()
                self.process.wait()
            except OSError:
                pass
            self.stopping.wait(self.RETRY_SEC)


class ServerPoller(threading.Thread):
    """Fetches on its own thread: a dead server can hold a request for its full timeout."""

    def __init__(self, get_config, interval, activity_count, notify):
        super().__init__(name="server", daemon=True)
        self.get_config = get_config
        self.interval = interval
        self.activity_count = activity_count
        self.notify = notify
        self.client = None
        self.poke = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.result = {"ok": False, "quotaUsed": None, "quotaTotal": None,
                       "notifications": None, "error": None, "activity": None}

    def stop(self):
        self.stopping.set()
        self.poke.set()

    def snapshot(self):
        with self.lock:
            return dict(self.result)

    def _update(self, **values):
        with self.lock:
            changed = any(self.result.get(k) != v for k, v in values.items())
            self.result.update(values)
        if changed:
            self.notify()

    def connect(self):
        """A Client, loading the credential if needed. Raises ServerError with panel text."""
        config = self.get_config()
        if config is None:
            raise server.ServerError("No account configured")
        if not config.url.startswith("https://"):
            raise server.ServerError("Server URL is not HTTPS")
        if self.client is None:
            password = server.load_password(config.user, config.url)
            if password is None:
                raise server.ServerError("Can't read credentials")
            self.client = server.Client(config.url, config.user, password)
        return self.client

    def refresh(self):
        try:
            client = self.connect()
            used, total = client.quota()
        except server.ServerError as error:
            if error.status == 401:
                self.client = None  # the app password may have been replaced
                self._update(ok=False, error="Credentials rejected")
            else:
                self._update(ok=False, error=str(error))
            return False
        values = {"ok": True, "error": None, "quotaUsed": used, "quotaTotal": total}
        try:
            values["notifications"] = client.notifications()
        except server.ServerError:
            values["notifications"] = None  # the app may not be installed
        # Entries get filtered down to synced files, and it is rows that count.
        try:
            values["activity"] = client.activity(max(self.activity_count * 3, 25))
        except server.ServerError as error:
            if error.status in (403, 404, 501):
                values["activity"] = None  # app missing: fall back to the D-Bus list
        self._update(**values)
        return True

    def run(self):
        while not self.stopping.is_set():
            ok = run_section("server", self.refresh)
            self.poke.wait(self.interval if ok else min(self.interval, SERVER_RETRY_SEC))
            self.poke.clear()


class Helper:
    def __init__(self, args):
        self.args = args
        self.config_directory = args.config_dir or config_dir()
        self.events = queue.Queue()
        self.bus = Bus()
        self.config = None
        self.config_mtime = None
        self.parser = LogParser()
        self.follower = LogFollower(os.path.join(self.config_directory, "logs"), self.parser)
        self.log_readable = False
        self.meter = SpeedMeter()
        self.sockets_ok = False
        self.tracker = RunTracker()
        self.dbus_folders = []
        self.dbus_present = False
        self.dbus_dirty = threading.Event()
        self.client_pid = None
        self.was_connected = False
        self.recent_paths = []
        self.last_action = None
        self.action_seq = 0
        self.last_output = None
        self.force_emit = True
        self.running = True
        self.times = {}
        self.socket = SocketClient(socket_path(), self._wake)
        self.signals = SignalWatcher(self._signal)
        self.server = ServerPoller(lambda: self.config, args.server_refresh,
                                   args.activity_count, self._wake)

    # Wakeups ---------------------------------------------------------------------------

    def _wake(self):
        if self.events.qsize() < 4:
            self.events.put(("wake", None))

    def _signal(self):
        self.dbus_dirty.set()
        self._wake()

    def _due(self, name, interval, now):
        if now - self.times.get(name, -1e9) >= interval:
            self.times[name] = now
            return True
        return False

    # Sections --------------------------------------------------------------------------

    def _load_config(self):
        path = os.path.join(self.config_directory, "nextcloud.cfg")
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            self.config, self.config_mtime = None, None
            return
        if mtime != self.config_mtime:
            self.config = load_config(self.config_directory)
            self.config_mtime = mtime

    def _read_dbus(self):
        present = self.bus.client_present()
        if present != self.dbus_present:
            self.client_pid = self.bus.client_pid() if present else None
        self.dbus_present = present
        self.dbus_folders = self.bus.folders() if present else []

    def _sample_speed(self):
        self.sockets_ok = False
        result = subprocess.run(["ss", "-tinHp"], capture_output=True, text=True, timeout=3,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            return
        self.meter.add(time.monotonic(), parse_ss(result.stdout, pid=self.client_pid))
        self.sockets_ok = True

    def _ask_socket(self):
        if self.config:
            self.socket.set_roots(f.path for f in self.config.folders)
        self.socket.set_roots(f.path for f in self.dbus_folders)
        folders = [f.path for f in self._folder_views()]
        _, problems = self.socket.snapshot()
        files = set(problems) | {row["path"] for row in self._log_problems()}
        self.socket.request(folders, sorted(files))

    def _follow_log(self):
        self.log_readable = self.follower.poll()

    def _read_recent(self):
        """Needed only when the activity API or the log can't supply these."""
        if self.server.snapshot()["activity"] is not None and self.log_readable:
            return
        self.recent_paths = (self.bus.recent_paths(self.dbus_folders[0].object_path)
                             if self.dbus_folders else [])

    # Building state --------------------------------------------------------------------

    def _folder_views(self):
        """Matched on path: D-Bus folder numbers are not the cfg's."""
        socket_folders, _ = self.socket.snapshot()
        configured = {f.path: f for f in (self.config.folders if self.config else [])}
        views = []
        for folder in self.dbus_folders:
            cfg = configured.pop(folder.path, None)
            paused = folder.paused if folder.paused is not None else bool(cfg and cfg.paused)
            views.append(FolderView(folder.name, folder.path, cfg.target if cfg else "/",
                                    socket_folders.get(folder.path), folder.status,
                                    folder.details, paused, folder.object_path))
        for cfg in configured.values():  # D-Bus is down or the folder is missing from it
            views.append(FolderView(os.path.basename(cfg.path), cfg.path, cfg.target,
                                    socket_folders.get(cfg.path), paused=cfg.paused))
        return views

    def _log_problems(self):
        """Pinned rows from the log. A row leaves when its file goes away; the socket
        clears it too, by answering for the file."""
        by_name = {f.name: f for f in self._folder_views()}
        cutoff = time.time() - PROBLEM_MAX_AGE_SEC
        rows = []
        for (folder_name, rel), (kind, when) in list(self.parser.problems.items()):
            folder = by_name.get(folder_name)
            if folder is None or when < cutoff:
                continue
            path = f"{folder.path}/{rel}"
            if os.path.lexists(path):
                rows.append({"kind": kind, "name": rel.rsplit("/", 1)[-1],
                             "folder": folder_name, "path": path, "time": when})
        return rows

    def _socket_problem_rows(self, views, problems):
        rows = []
        for path, (status, seen) in problems.items():
            if status != "ERROR":
                continue
            folder = next((f.name for f in views if path.startswith(f.path + "/")), None)
            rows.append({"kind": "error", "name": path.rsplit("/", 1)[-1], "folder": folder,
                         "path": path, "time": int(seen)})
        return rows

    def _activity(self, views, problems, snapshot):
        raw = snapshot["activity"]
        if raw is not None:
            rows = server.normalise_activity(raw, views, self.parser.kinds)
        else:
            rows = server.rows_from_paths(self.recent_paths, views)
        pinned = {row["path"]: row for row in self._socket_problem_rows(views, problems)}
        pinned.update({row["path"]: row for row in self._log_problems()})
        # The socket has the last word on errors. Each log error gets asked about, so
        # one it no longer reports has been fixed. Conflicts stay until they age out.
        if self.socket.connected:
            pinned = {p: r for p, r in pinned.items() if r["kind"] != "error" or p in problems}
        return server.merge_activity(rows, list(pinned.values()), self.args.activity_count)

    def build(self):
        now = time.time()
        socket_folders, problems = self.socket.snapshot()
        views = self._folder_views()
        snapshot = self.server.snapshot()
        inputs = Inputs(
            running=self.dbus_present or self.socket.connected,
            api_available=bool(self.dbus_folders),
            folders=views,
            file_problems=problems,
            quota_warning=quota_over_limit(snapshot["quotaUsed"], snapshot["quotaTotal"],
                                           self.args.quota_warn_percent))
        state = compute_state(inputs)
        connected = inputs.api_available and is_connected(inputs)
        if not inputs.running:
            self.meter.reset()
            self.sockets_ok = False

        active = inputs.running and is_transfer_active(inputs)
        picked = pick_details(views)
        log_started = self.parser.run_started_at if self.parser.in_run else None
        eta_sec, progress = self.tracker.update(now, active, picked.eta_sec, log_started)
        rates = self.meter.rates() if self.sockets_ok else (None, None)
        last_finished = self.parser.last_finished
        if not self.log_readable and self.recent_paths:
            last_finished = os.path.basename(self.recent_paths[0])
        transfer = build_transfer(active, picked, eta_sec, progress, rates,
                                  self.parser.file_counts(), self.parser.current_file(),
                                  last_finished)

        if connected and not self.was_connected:
            self.server.poke.set()  # it just came back; the server may have too
        self.was_connected = connected
        config = self.config
        return {
            "schemaVersion": SCHEMA_VERSION,
            "state": state,
            "summary": summary_for(state, inputs),
            "lastSyncedAt": self.parser.last_sync_finished_at or self.tracker.last_finished_at,
            "client": {"running": inputs.running, "connected": connected,
                       "apiAvailable": inputs.api_available},
            "folders": [{"name": f.name, "path": f.path, "status": f.socket_status,
                         "paused": f.paused} for f in views],
            "transfer": transfer,
            "activity": self._activity(views, problems, snapshot),
            "server": {"ok": snapshot["ok"],
                       "account": account_label(config.user, config.url) if config else None,
                       "url": config.url if config else None,
                       "quotaUsed": snapshot["quotaUsed"], "quotaTotal": snapshot["quotaTotal"],
                       "notifications": snapshot["notifications"], "error": snapshot["error"]},
            "lastAction": self.last_action,
        }

    def emit(self):
        state = run_section("state", self.build)
        if state is None or (state == self.last_output and not self.force_emit):
            return
        self.last_output = state
        self.force_emit = False
        try:
            sys.stdout.write(json.dumps(state, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, ValueError):
            self.running = False

    # Commands --------------------------------------------------------------------------

    def _finish_action(self, command, ok, message):
        self.action_seq += 1
        self.last_action = {"seq": self.action_seq, "command": command, "ok": ok, "message": message}
        self.force_emit = True
        self.dbus_dirty.set()
        self.times.pop("socket", None)

    def _share(self, path):
        """On a thread of its own: the server call can take seconds."""
        ok, message = False, "Share failed"
        try:
            ok, message = self._create_link(path)
        except Exception as error:  # noqa: BLE001
            log.warning("share failed: %s", type(error).__name__)
        self.events.put(("action", ("share", ok, message)))

    def _create_link(self, path):
        views = self._folder_views()
        server_path = server.to_server(path, views) if os.path.isabs(path) else None
        if server_path is None:
            return False, "Not in a synced folder"
        try:
            url = self.server.connect().share(server_path)
        except server.ServerError as error:
            if error.status == 404:
                return False, "File isn't on the server yet"
            return False, str(error)
        # wl-copy stays behind to serve the clipboard. It must not hold our pipes, or
        # waiting for it would hang, and the link goes on stdin to stay out of ps.
        try:
            subprocess.run(["wl-copy"], input=url.encode(), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            return False, f"Link created but not copied: {url}"
        try:
            subprocess.run(["notify-send", "Nextcloud", "Share link copied"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        return True, "Link copied"

    def handle_command(self, line):
        command, _, argument = line.strip("\r").partition(" ")
        if command == "":
            return
        if command == "quit":
            self.running = False
        elif command == "refresh":
            self.force_emit = True
            self.dbus_dirty.set()
            self.times.pop("socket", None)
            self.server.poke.set()
        elif command in ("pause", "resume"):
            ok, message = actions.set_all_paused(self.bus, command == "pause")
            if ok and command == "pause" and any(
                    f.parsed.kind == "syncing" or f.dbus_status == 2
                    for f in self._folder_views()):
                message = "Paused; the current transfer will finish"
            self._finish_action(command, ok, message)
        elif command == "sync":
            ok, message = actions.sync_now(self.bus)
            self._finish_action(command, ok, message)
        elif command == "settings":
            folders = self.bus.folder_objects()
            ok = bool(folders) and self.bus.activate(folders[0], "opensettings")
            self._finish_action(command, ok, "Opening settings" if ok else "Client not running")
        elif command == "share":
            threading.Thread(target=self._share, args=(argument,), daemon=True).start()
        else:
            self._finish_action(command, False, "Unknown command")

    # Main loop -------------------------------------------------------------------------

    def _handle_event(self, kind, value):
        if kind == "eof":
            self.running = False
        elif kind == "command":
            run_section("command", lambda: self.handle_command(value))
        elif kind == "action":
            self._finish_action(*value)

    def _tick(self):
        now = time.monotonic()
        if self._due("config", CONFIG_SEC, now):
            run_section("config", self._load_config)
        interval = DBUS_MIN_SEC if self.dbus_dirty.is_set() else DBUS_POLL_SEC
        if self._due("dbus", interval, now):
            self.dbus_dirty.clear()
            run_section("dbus", self._read_dbus)
        if self._due("speed", SPEED_SEC, now):
            run_section("speed", self._sample_speed)
        if self._due("log", LOG_SEC, now):
            run_section("log", self._follow_log)
        if self._due("socket", SOCKET_POLL_SEC, now):
            run_section("socket", self._ask_socket)
        if self._due("recent", RECENT_SEC, now):
            run_section("recent", self._read_recent)
        self.emit()

    def run(self):
        StdinReader(self.events).start()
        self.socket.start()
        self.signals.start()
        run_section("config", self._load_config)
        self.server.start()
        try:
            while self.running:
                try:
                    kind, value = self.events.get(timeout=LOOP_SEC)
                    self._handle_event(kind, value)
                    while self.running:
                        kind, value = self.events.get_nowait()
                        self._handle_event(kind, value)
                except queue.Empty:
                    pass
                if self.running:
                    self._tick()
        finally:
            self.signals.stop()
            self.socket.stop()
            self.server.stop()


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Nextcloud status helper")
    parser.add_argument("--server-refresh", type=float, default=60, metavar="SEC")
    parser.add_argument("--activity-count", type=int, default=8)
    parser.add_argument("--quota-warn-percent", type=float, default=90)
    parser.add_argument("--config-dir", help="directory holding nextcloud.cfg and logs/")
    return parser.parse_args(argv)


def main(argv=None):
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="nextcloud-status: %(message)s")
    helper = Helper(parse_args(argv))

    def on_signal(_number, _frame):
        helper.running = False  # the main loop checks at least every LOOP_SEC

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    helper.run()
    return 0
