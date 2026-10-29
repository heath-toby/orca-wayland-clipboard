#!/usr/bin/env bash
# Wayland Clipboard for Orca -- Uninstaller
#
# Removes the extension package and revokes its Orca approval. Settings are
# left alone, under /org/gnome/orca/<profile>/extensions/indent-beeper/, so a
# reinstall picks up where you left off. Pass --purge to remove them too.

set -euo pipefail

ADDON_NAME="wayland_clipboard"
# Orca sanitises the namespace for the settings path, and an underscore becomes
# a dash -- so the package is wayland_clipboard and its dconf path is not.
SETTINGS_NAME="indent-beeper"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/extensions/$ADDON_NAME"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

info() { echo "  [+] $*"; }

echo ""
echo "=== Wayland Clipboard for Orca -- Uninstaller ==="
echo ""

# Revoke first: once the directory is gone Orca can no longer identify it.
if orca --revoke-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Revoked Orca's approval of '$ADDON_NAME'."
else
    info "No Orca approval to revoke (already removed)."
fi

if [ -d "$ADDON_DIR" ]; then
    rm -rf "$ADDON_DIR"
    info "Removed $ADDON_DIR"
else
    info "No installed extension at $ADDON_DIR (already removed)."
fi

if [ "$PURGE" -eq 1 ]; then
    dconf reset -f "/org/gnome/orca/default/extensions/$SETTINGS_NAME/" 2>/dev/null \
        && info "Removed Wayland Clipboard's settings." \
        || info "No settings to remove."
else
    info "Settings kept. Re-run with --purge to remove them too."
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "  Restart Orca for the change to take effect:"
echo "    orca --replace &"
echo ""
