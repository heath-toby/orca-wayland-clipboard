"""Wayland Clipboard: make Orca's clipboard commands work on Wayland.

Orca 51 user extension. Install to $XDG_DATA_HOME/orca/extensions/ and
approve with ``orca --approve-extension wayland_clipboard``; Orca re-checks
the hash of every file in the package on each start, so re-approve after
editing.
"""

__version__ = "1.1.0"

from orca.extension import Extension

# Import submodules as `from .module import name`, never as
# `from . import module`. The loader imports us as
# orca_user_extension.<name> but never creates `orca_user_extension`
# itself, and `from . import module` goes through __import__ with the
# full dotted path, which insists on importing that absent grandparent.
from .patches import install as _install
from .patches import uninstall as _uninstall


class WaylandClipboard(Extension):
    """Routes Orca's clipboard through wl-copy and wl-paste."""

    GROUP_LABEL = "Wayland Clipboard"
    DESCRIPTION = (
        "Makes Orca's copy and clipboard commands work on Wayland. Orca falls "
        "back to Gtk.Clipboard when no clipboard manager is running, and that "
        "cannot own or read the selection from a window that never has focus, "
        "so copying silently does nothing. This routes it through wl-copy and "
        "wl-paste, which speak the data-control protocols made for this case."
    )
    VERSION = "1.1.0"  # keep in sync with __version__; read by AST, must be a literal
    AUTHOR = "Toby"

    # No preferences: there is nothing to configure. It either applies, on a
    # Wayland session with wl-clipboard installed, or it stands aside.

    def on_ready(self) -> None:
        """Replaces the clipboard methods once Orca is up."""

        _install()

    def on_enabled(self) -> None:
        """Replaces them again after a reload."""

        _install()

    def on_disabled(self) -> None:
        """Restores Orca's own clipboard methods."""

        _uninstall()
