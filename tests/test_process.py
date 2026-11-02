"""Runs the real process with no client, bus or server around it."""
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from support import FIXTURES, ROOT, fixture

SCRIPT = os.path.join(ROOT, "helper", "nextcloud-status")
TOP_KEYS = {"schemaVersion", "state", "summary", "lastSyncedAt", "client", "folders",
            "transfer", "activity", "server", "lastAction"}
TRANSFER_KEYS = {"active", "upBps", "downBps", "totalBytes", "etaSec", "bytesLeft", "progress",
                 "filesDone", "filesTotal", "currentFile", "lastFinishedFile"}
SERVER_KEYS = {"ok", "account", "url", "quotaUsed", "quotaTotal", "notifications", "error"}


class Helper:
    def __init__(self, directory):
        self.runtime = os.path.join(directory, "run")
        self.config = os.path.join(directory, "config", "Nextcloud")
        os.makedirs(os.path.join(self.runtime, "Nextcloud"))
        os.makedirs(self.config)
        with open(os.path.join(self.config, "nextcloud.cfg"), "w") as handle:
            handle.write(fixture("nextcloud.cfg"))
        env = dict(os.environ, XDG_RUNTIME_DIR=self.runtime,
                   XDG_CONFIG_HOME=os.path.dirname(self.config),
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path={directory}/no-bus",
                   PATH=os.path.join(directory, "bin"))  # no secret-tool, busctl, or ss
        python = sys.executable
        self.process = subprocess.Popen(
            [python, SCRIPT, "--server-refresh", "1"], env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], 0.2)
            if ready:
                line = self.process.stdout.readline()
                if not line:
                    raise AssertionError("helper exited")
                state = json.loads(line)
                if predicate(state):
                    return state
        raise AssertionError("no matching state")

    def send(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def finish(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                pipe.close()
            except OSError:
                pass


class FakeClientSocket:
    """Stands in for the client's local socket."""

    def __init__(self, path):
        self.path = path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.connection = None
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.connection, _ = self.server.accept()
        self.connection.sendall(b"REGISTER_PATH:/home/alice/Documents\n"
                                b"STATUS:OK:/home/alice/Documents\n")

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
        self.server.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


class Process(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.helper = Helper(self.tmp.name)
        self.addCleanup(self.helper.finish)

    def test_shape_with_nothing_running(self):
        state = self.helper.wait_for(lambda s: s["server"]["error"])
        self.assertEqual(set(state), TOP_KEYS)
        self.assertEqual(set(state["transfer"]), TRANSFER_KEYS)
        self.assertEqual(set(state["server"]), SERVER_KEYS)
        self.assertEqual(state["schemaVersion"], 1)
        self.assertEqual(state["state"], "client-down")
        self.assertEqual(state["client"], {"running": False, "connected": False,
                                           "apiAvailable": False})
        self.assertEqual(state["server"]["error"], "Can't read credentials")
        self.assertFalse(state["server"]["ok"])
        self.assertIsNone(state["transfer"]["upBps"])
        self.assertEqual([f["name"] for f in state["folders"]], ["Documents", "Screenshots"])
        self.assertEqual(state["server"]["account"], "alice@nextcloud.example.com")

    def test_exits_when_stdin_closes(self):
        self.helper.wait_for(lambda s: True)
        self.helper.process.stdin.close()
        self.assertEqual(self.helper.process.wait(timeout=5), 0)

    def test_quit_command(self):
        self.helper.wait_for(lambda s: True)
        self.helper.send("quit")
        self.assertEqual(self.helper.process.wait(timeout=5), 0)

    def test_commands_without_a_client_report_and_survive(self):
        self.helper.wait_for(lambda s: True)
        for command, message in (("pause", "Client not running"), ("resume", "Client not running"),
                                 ("sync", "Client not running"), ("settings", "Client not running"),
                                 ("bogus", "Unknown command"),
                                 ("share /etc/passwd", "Not in a synced folder")):
            self.helper.send(command)
            name = command.split()[0]
            state = self.helper.wait_for(
                lambda s: s["lastAction"] and s["lastAction"]["command"] == name)
            action = dict(state["lastAction"])
            self.assertGreater(action.pop("seq"), 0)
            self.assertEqual(action, {"command": name, "ok": False, "message": message})

    def test_recovers_when_the_client_comes_and_goes(self):
        self.helper.wait_for(lambda s: s["state"] == "client-down")
        fake = FakeClientSocket(os.path.join(self.helper.runtime, "Nextcloud", "socket"))
        self.addCleanup(fake.close)
        state = self.helper.wait_for(
            lambda s: s["client"]["running"] and s["folders"][0]["status"] == "OK")
        # Socket without D-Bus: a running client whose API is gone, not a false "synced".
        self.assertEqual(state["state"], "disconnected")
        self.assertEqual(state["summary"], "Client API unavailable")
        fake.close()
        self.helper.wait_for(lambda s: s["state"] == "client-down")

    def test_terminate_is_clean(self):
        self.helper.wait_for(lambda s: True)
        self.helper.process.terminate()
        self.assertEqual(self.helper.process.wait(timeout=5), 0)
        self.assertNotIn("Traceback", self.helper.process.stderr.read())


if __name__ == "__main__":
    unittest.main()
