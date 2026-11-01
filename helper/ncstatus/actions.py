"""Pause, resume and Sync now on top of a bus object. Takes the bus as an argument so
the toggle logic can be tested with a fake."""
import time

from .dbus import STATUS_SYNCING

ACTIVATE_WAIT_SEC = 1.0
_POLL_SEC = 0.05


def _wait_for(bus, folder, paused, sleep):
    """Pause is applied asynchronously (under 0.2 s in testing)."""
    deadline = ACTIVATE_WAIT_SEC
    while deadline > 0:
        if bus.describe(folder.object_path) is paused:
            return True
        sleep(_POLL_SEC)
        deadline -= _POLL_SEC
    return bus.describe(folder.object_path) is paused


def _set_paused(bus, folder, paused, sleep):
    """Pause is a toggle: flip only after reading the state. True once it's confirmed."""
    current = bus.describe(folder.object_path)
    if current is None:
        return False
    if current is paused:
        return True
    return bus.activate(folder.object_path, "pause") and _wait_for(bus, folder, paused, sleep)


def set_all_paused(bus, paused, sleep=time.sleep):
    """Returns (ok, message). A mixed state ends up uniform, never inverted."""
    folders = bus.folders()
    if not folders:
        return False, "Client not running"
    failed = [f.name for f in folders if not _set_paused(bus, f, paused, sleep)]
    verb = "pause" if paused else "resume"
    if failed:
        return False, f"Couldn't {verb} {', '.join(failed)}"
    return True, "Paused" if paused else "Resumed"


def sync_now(bus, sleep=time.sleep):
    """Pause then resume each idle, unpaused folder, which starts a real run."""
    folders = bus.folders()
    if not folders:
        return False, "Client not running"
    idle = [f for f in folders if f.paused is False and f.status != STATUS_SYNCING]
    if not idle:
        if any(f.status == STATUS_SYNCING for f in folders):
            return True, "Already syncing"
        return False, "Sync is paused"
    failed = []
    for folder in idle:
        paused = _set_paused(bus, folder, True, sleep)
        # Retry the resume: leaving a folder paused would be worse than a missed sync.
        resumed = _set_paused(bus, folder, False, sleep) or _set_paused(bus, folder, False, sleep)
        if not (paused and resumed):
            failed.append(folder.name)
    if failed:
        return False, f"Couldn't sync {', '.join(failed)}"
    return True, "Sync started"
