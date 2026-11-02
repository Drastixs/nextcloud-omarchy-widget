"""Transfer speed from `ss -tinHp` byte counters."""
import re
from collections import deque

_SENT = re.compile(r"\bbytes_sent:(\d+)")
_RECEIVED = re.compile(r"\bbytes_received:(\d+)")
_USER = re.compile(r'\(\("([^"]+)",pid=(\d+)')
FLOOR_BPS = 10_000  # background polling; reads as 0


def parse_ss(text, pid=None, process="nextcloud"):
    """Return {connection: (bytes_sent, bytes_received)} for the client's sockets.

    Rows come in pairs: pid on the first line, counters on the indented second. ss
    leaves out a counter that is still zero. With pid set, process is not consulted.
    """
    sockets = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.strip() == "--":
            key = None
        elif line[0].isspace():
            if key is not None:
                sent = _SENT.search(line)
                received = _RECEIVED.search(line)
                sockets[key] = (int(sent[1]) if sent else 0,
                                int(received[1]) if received else 0)
            key = None
        else:
            key = None
            user = _USER.search(line)
            columns = line.split()
            if user and len(columns) >= 5:
                mine = int(user[2]) == pid if pid is not None else user[1] == process
                if mine:
                    key = f"{columns[3]}>{columns[4]}"
    return sockets


class SpeedMeter:
    """Summed per-socket deltas over about 2 s, smoothed over about 5 samples."""

    MAX_GAP_SEC = 15.0

    def __init__(self, window=5, floor=FLOOR_BPS):
        self.floor = floor
        self.samples = deque(maxlen=window)
        self.previous = None
        self.previous_time = None

    def reset(self):
        self.samples.clear()
        self.previous = None
        self.previous_time = None

    def add(self, when, sockets):
        previous = self.previous
        elapsed = None if previous is None else when - self.previous_time
        self.previous = sockets
        self.previous_time = when
        if previous is None or elapsed <= 0:
            return
        if elapsed > self.MAX_GAP_SEC:  # stale baseline, such as after a suspend
            self.samples.clear()
            return
        sent = received = 0
        for key, (now_sent, now_received) in sockets.items():
            # A new socket counts from zero. A lower counter means the same address
            # pair was reused, so it also counts from zero, and deltas can't go negative.
            old_sent, old_received = previous.get(key, (0, 0))
            sent += now_sent - old_sent if now_sent >= old_sent else now_sent
            received += now_received - old_received if now_received >= old_received else now_received
        self.samples.append((elapsed, sent, received))

    def rates(self):
        """Return (up, down) in bytes per second."""
        total = sum(s[0] for s in self.samples)
        if total <= 0:
            return 0, 0
        up = sum(s[1] for s in self.samples) / total
        down = sum(s[2] for s in self.samples) / total
        return (int(up) if up >= self.floor else 0, int(down) if down >= self.floor else 0)
