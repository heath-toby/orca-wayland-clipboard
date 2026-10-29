#!/usr/bin/env bash
# Wayland Clipboard for Orca -- Installer
#
# Installs Wayland Clipboard as an Orca 51 user extension, into
# ~/.local/share/orca/extensions/, and approves it so Orca will load it.
#
# Re-running is safe. Because approval is by content hash, the script
# re-approves on every run -- which is what you want after editing.

set -euo pipefail

ADDON_NAME="wayland_clipboard"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
EXTENSIONS_DIR="$ORCA_DIR/extensions"
ADDON_DIR="$EXTENSIONS_DIR/$ADDON_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/$ADDON_NAME"

info()  { echo "  [+] $*"; }
warn()  { echo "  [!] $*"; }
error() { echo "  [ERROR] $*" >&2; exit 1; }

echo ""
echo "=== Wayland Clipboard for Orca -- Installer ==="
echo ""

# Probe via extension_loader, not orca.extension: importing orca.extension
# first hits a circular import inside Orca itself (live_region_presenter
# imports it mid-initialisation). extension_loader pulls in command_manager
# ahead of it, so this import order is the one that works.
if ! python3 -c "import orca.extension_loader" 2>/dev/null; then
    error "Orca 51 or later with extension support not found."
fi
info "Orca with extension support found."

# What this extension actually needs at runtime: the two wl-clipboard
# binaries, which clip.py's usable() makes mandatory. This block used to probe
# GStreamer and warn that "the beep will be silent" -- copied wholesale from a
# beeper project into an extension that produces no audio at all, naming
# packages it does not need while never checking the two it does.
missing=""
for tool in wl-copy wl-paste; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    warn "Not found:$missing"
    warn "Install the wl-clipboard package, or this extension will stand aside."
else
    info "wl-copy and wl-paste found."
fi

if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    warn "WAYLAND_DISPLAY is not set in this shell; the extension stands aside"
    warn "on anything that is not a Wayland session, which is harmless."
fi

[ -d "$SOURCE_DIR" ] || error "Source directory '$SOURCE_DIR' not found."

rm -rf "$SOURCE_DIR/__pycache__"

mkdir -p "$ADDON_DIR"
# Remove files that no longer exist in the source, so the installed package --
# and therefore its approval hash -- matches the source.
find "$ADDON_DIR" -maxdepth 1 -name '*.py' -delete
rm -rf "$ADDON_DIR/__pycache__"
cp "$SOURCE_DIR"/*.py "$ADDON_DIR/"
info "Installed extension package to $ADDON_DIR"

if orca --approve-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Approved '$ADDON_NAME' with Orca."
else
    error "Could not approve the extension. Run: orca --approve-extension $ADDON_NAME"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "  Restart Orca to activate:"
echo "    orca --replace &"
echo ""
echo "  Settings: Orca Preferences -> User Extensions -> Wayland Clipboard"
echo ""
echo "  Needs wl-copy and wl-paste (the wl-clipboard package), and does"
echo "  nothing unless WAYLAND_DISPLAY is set."
echo ""
