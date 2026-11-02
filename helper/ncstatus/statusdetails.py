"""CloudProviders StatusDetails text. English only: anything else has no numbers."""
import re
from dataclasses import dataclass

_UNITS = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}
_ETA_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_ETA_SUFFIX = r"(?:\s*\((?P<eta>.+?) left\))?\s*$"
_SIZE = re.compile(r"^Syncing (?P<num>\d+(?:\.\d+)?) (?P<unit>[KMGT]?B)" + _ETA_SUFFIX)
_COUNT = re.compile(r"^Syncing (?P<done>\d+) of (?P<total>\d+)" + _ETA_SUFFIX)


@dataclass
class Details:
    connected: bool = False
    account_state: str = ""  # text before " - ", such as "Connected"
    kind: str = "other"  # idle, ignored, syncing, checking, paused, error, other
    total_bytes: int = None
    eta_sec: int = None
    files_done: int = None
    files_total: int = None


def parse_eta(text):
    """'6 minutes 22 seconds' to 382. None if any part isn't understood."""
    if not text:
        return None
    parts = re.findall(r"(\d+) (second|minute|hour|day)s?\b", text)
    leftover = re.sub(r"\d+ (second|minute|hour|day)s?\b", "", text).strip()
    if not parts or leftover:
        return None
    return sum(int(num) * _ETA_UNITS[unit] for num, unit in parts)


def parse_status_details(text):
    text = (text or "").strip()
    state, sep, message = text.partition(" - ")
    if not sep:
        state, message = text, ""
    details = Details(connected=text.startswith("Connected"), account_state=state)
    message = message.strip()

    if message == "Success":
        details.kind = "idle"
    elif message.startswith("Success,"):
        details.kind = "ignored"
    elif message == "Sync paused":
        details.kind = "paused"
    elif message == "Error":
        details.kind = "error"
    elif message.startswith("Checking for changes"):
        details.kind = "checking"
    elif message.startswith("Syncing"):
        details.kind = "syncing"
        size = _SIZE.match(message)
        count = _COUNT.match(message)
        if size:
            power = 1024 ** _UNITS[size["unit"]]  # labelled MB but 1024-based
            details.total_bytes = round(float(size["num"]) * power)
            details.eta_sec = parse_eta(size["eta"])
        elif count:
            if int(count["total"]) > 0:  # "0 of 0" is discovery or a no-op run
                details.files_done = int(count["done"])
                details.files_total = int(count["total"])
                details.eta_sec = parse_eta(count["eta"])
    return details
