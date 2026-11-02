#!/bin/bash
set -euo pipefail

repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
plugins=~/.config/omarchy/plugins

mkdir -p "$plugins"
ln -sfn "$repo/plugin" "$plugins/nextcloud.widget"
python3 "$repo/scripts/shell-config.py" install
omarchy-shell -q shell rescanPlugins
echo "Installed nextcloud.widget"
