#!/usr/bin/env bash
# Wayland Clipboard for Orca -- Uninstaller
#
# Removes the extension package and revokes its Orca approval.
#
# There is deliberately no --purge: this extension declares no preferences, so
# it has no dconf subtree of its own to remove. A copy of this script carried a
# SETTINGS_NAME of "indent-beeper" from the project it was adapted from, so
# --purge wiped a *different* add-on's settings -- tone volume, duration,
# beep-on-change -- while reporting that it had removed this one's.

set -euo pipefail

ADDON_NAME="wayland_clipboard"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/extensions/$ADDON_NAME"

if [ "${1:-}" = "--purge" ]; then
    echo "  [!] --purge does nothing here: this extension has no settings." >&2
fi

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

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "  Restart Orca for the change to take effect:"
echo "    orca --replace &"
echo ""
