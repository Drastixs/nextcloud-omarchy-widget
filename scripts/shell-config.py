#!/usr/bin/env python3
"""Add or remove the widget and tray rule in the Omarchy shell.json."""
import json
import sys
import time
from pathlib import Path

WIDGET = "nextcloud.widget"
TRAY = "omarchy.tray"
TRAY_ITEM = "Nextcloud"


def main(action):
    path = Path.home() / ".config/omarchy/shell.json"
    config = json.loads(path.read_text())
    sections = config["bar"]["layout"]
    right = sections["right"]
    tray = next((entry for entry in right if entry.get("id") == TRAY), None)
    original = json.dumps(config)

    if action == "install":
        if not any(entry.get("id") == WIDGET for entry in right):
            index = right.index(tray) + 1 if tray else 0
            right.insert(index, {"id": WIDGET})
        if tray is not None:
            hidden = tray.setdefault("hidden", [])
            if TRAY_ITEM not in hidden:
                hidden.append(TRAY_ITEM)
    else:
        right[:] = [entry for entry in right if entry.get("id") != WIDGET]
        if tray is not None and TRAY_ITEM in tray.get("hidden", []):
            tray["hidden"].remove(TRAY_ITEM)

    if json.dumps(config) == original:
        return
    backup = path.with_name(f"shell.json.bak-{time.strftime('%Y%m%d%H%M%S')}")
    backup.write_text(path.read_text())
    path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Updated {path}, backup at {backup}")


if __name__ == "__main__":
    main(sys.argv[1])
