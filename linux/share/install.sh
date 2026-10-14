#!/bin/sh
# Adds 1401 to your application menu (for your user only; nothing is installed system-wide, no password needed).
#   ./install.sh             menu entry + icon pointing at this folder
#   ./install.sh --uninstall removes them again (and leaves this folder and your 1401 data alone)
set -e
HERE=$(dirname "$(readlink -f "$0")")
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/256x256/apps"
if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$APPS/1401.desktop" "$ICONS/1401.png"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
    echo "1401 was removed from the application menu. This folder and ~/.local/share/1401 (your scans and builds) stay."
    exit 0
fi
mkdir -p "$APPS" "$ICONS"
cp "$HERE/share/icons/1401.png" "$ICONS/1401.png"
sed "s|@HOME@|$HERE|g" "$HERE/share/applications/1401.desktop" > "$APPS/1401.desktop"
chmod 0644 "$APPS/1401.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "1401 is in your application menu. It runs from $HERE - keep this folder where it is (run ./install.sh again after moving it)."
