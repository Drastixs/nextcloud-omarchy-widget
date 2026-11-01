"""The client's local socket: folder and file status."""
import os
import socket
import threading
import time

from .config import strip_slash

PROBLEM_STATUSES = ("ERROR", "WARNING")
_MAX_TRACKED = 1000


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "Nextcloud", "socket")


def parse_socket_line(line):
    """Returns ('status', value, path), ('register', path), or None for other lines."""
    line = line.rstrip("\r\n")
    if line.startswith("STATUS:"):
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return ("status", parts[1].split("+", 1)[0], strip_slash(parts[2]))
    elif line.startswith("REGISTER_PATH:"):
        return ("register", strip_slash(line.split(":", 1)[1]))
    return None


class SocketState:
    """Folder status, plus files last reported with a problem. Failed uploads look idle
    everywhere else, so ERROR is kept until the client says otherwise."""

    def __init__(self):
        self.roots = set()
        self.folders = {}
        self.problems = {}  # path: (status, first seen)

    def clear(self):
        self.folders.clear()
        self.problems.clear()

    def feed(self, line, now=None):
        parsed = parse_socket_line(line)
        if parsed is None:
            return False
        if parsed[0] == "register":
            self.roots.add(parsed[1])
            return False
        _, status, path = parsed
        if path in self.roots:
            changed = self.folders.get(path) != status
            self.folders[path] = status
            return changed
        if status in PROBLEM_STATUSES:
            current = self.problems.get(path)
            if current and current[0] == status:
                return False
            if len(self.problems) < _MAX_TRACKED:
                self.problems[path] = (status, now if now is not None else time.time())
                return True
            return False
        return self.problems.pop(path, None) is not None


class SocketClient(threading.Thread):
    """Keeps a connection open, reconnecting forever. Calls notify() on any change."""

    RETRY_SEC = 3

    def __init__(self, path, notify):
        super().__init__(name="socket", daemon=True)
        self.path = path
        self.notify = notify
        self.state = SocketState()
        self.lock = threading.Lock()
        self.connection = None
        self.stopping = threading.Event()

    @property
    def connected(self):
        return self.connection is not None

    def stop(self):
        self.stopping.set()

    def snapshot(self):
        with self.lock:
            return dict(self.state.folders), dict(self.state.problems)

    def set_roots(self, paths):
        with self.lock:
            self.state.roots.update(paths)

    def request(self, folders, files):
        """Ask for current status; answers arrive as STATUS lines."""
        connection = self.connection
        if connection is None:
            return
        message = "".join(f"RETRIEVE_FOLDER_STATUS:{p}\n" for p in folders)
        message += "".join(f"RETRIEVE_FILE_STATUS:{p}\n" for p in files)
        try:
            connection.sendall(message.encode("utf-8", "surrogateescape"))
        except OSError:
            pass  # run() notices and reconnects

    def run(self):
        while not self.stopping.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(self.RETRY_SEC)
                    connection.connect(self.path)
                    self.connection = connection
                    connection.settimeout(1)
                    connection.sendall(b"VERSION:\n")
                    self.notify()
                    self._read(connection)
            except OSError:
                pass
            was_connected = self.connection is not None
            self.connection = None
            with self.lock:
                self.state.clear()
            if was_connected:
                self.notify()
            self.stopping.wait(self.RETRY_SEC)

    def _read(self, connection):
        buffer = b""
        while not self.stopping.is_set():
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer += chunk
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            changed = False
            with self.lock:
                for line in lines:
                    changed |= self.state.feed(line.decode("utf-8", "replace"))
            if changed:
                self.notify()
