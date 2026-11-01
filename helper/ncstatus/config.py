"""nextcloud.cfg: account URL, user and synced folders."""
import os
import re
from dataclasses import dataclass, field

_FOLDER_KEY = re.compile(
    r"^(?:Folders|FoldersWithPlaceholders|Multifolders)\\([^\\]+)\\(\w+)$"
)


@dataclass
class CfgFolder:
    path: str  # never has a trailing slash; this is the key shared with D-Bus and the socket
    target: str  # server path, e.g. /Sync/Documents
    paused: bool = False


@dataclass
class Config:
    url: str = ""
    user: str = ""
    folders: list = field(default_factory=list)


def strip_slash(path):
    return path.rstrip("/") or "/"


def _unquote(value):
    """Undo QSettings' quoting of string values."""
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return re.sub(r"\\(.)", lambda m: m.group(1), value)


def parse_cfg(text):
    """Parse nextcloud.cfg text. Returns the first account's Config, or None."""
    accounts = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Accounts" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        match = re.match(r"^(\d+)\\(.+)$", key)
        if match:
            accounts.setdefault(int(match.group(1)), {})[match.group(2)] = _unquote(value)

    for _, keys in sorted(accounts.items()):
        url = keys.get("url")
        if not url:
            continue
        grouped = {}
        for key, value in keys.items():
            match = _FOLDER_KEY.match(key)
            if match:
                grouped.setdefault(match.group(1), {})[match.group(2)] = value
        folders = [
            CfgFolder(
                path=strip_slash(f["localPath"]),
                target=strip_slash(f.get("targetPath", "/")),
                paused=f.get("paused", "false").lower() == "true",
            )
            for _, f in sorted(grouped.items())
            if f.get("localPath")
        ]
        return Config(
            url=url.rstrip("/"),
            user=keys.get("dav_user") or keys.get("webflow_user") or "",
            folders=folders,
        )
    return None


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "Nextcloud")


def load_config(directory):
    """Read nextcloud.cfg from directory. Returns None if missing or unusable."""
    try:
        with open(os.path.join(directory, "nextcloud.cfg"), encoding="utf-8",
                  errors="replace") as handle:
            return parse_cfg(handle.read())
    except OSError:
        return None
