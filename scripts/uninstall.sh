#!/bin/bash
set -euo pipefail

repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)

python3 "$repo/scripts/shell-config.py" remove
rm -f ~/.config/omarchy/plugins/nextcloud.widget
omarchy-shell -q shell rescanPlugins
echo "Removed nextcloud.widget"
