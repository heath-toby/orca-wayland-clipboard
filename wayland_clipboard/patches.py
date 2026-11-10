"""Point Orca's fallback clipboard manager at wl-copy and wl-paste.

``orca.clipboard_manager`` picks a manager at connect time: Klipper, then
GPaste 3, then GPaste 2, then ``_ClipboardManagerFallback``, which is
``Gtk.Clipboard``.
On a Wayland session with none of those managers running, the fallback is what
you get -- and it does not work (see clip.py).

Both methods are called through the instance -- ``self._manager.set_contents``
and ``self._get_contents()`` -- so patching the class takes effect on the
manager Orca has already built. That is not true of everything in Orca: its
key watcher hands AT-SPI a *bound* method at startup, so patching that class
achieves nothing. Checked rather than assumed, which is the lesson of a long
afternoon.

If anything here fails, Orca's own method runs instead. A clipboard that does
nothing is the status quo; a clipboard that raises would break the commands
that use it.
"""

from __future__ import annotations

import logging

# Orca renamed this module from `clipboard` to `clipboard_manager`. Until
# 1.2.2 this add-on imported the old name, so on an Orca that had been updated
# the import raised and **the whole extension silently failed to load** -- Orca
# catches a failed import, logs it at WARNING and carries on, so the only
# symptom was that copying stopped working again. The current name is tried
# first and the old one kept as a fallback, since the class and both method
# names are unchanged either way.
try:
    from orca import clipboard_manager as clipboard
except ImportError:  # Orca before the rename
    from orca import clipboard

# orca.clipboard_manager has already required Gtk 3.0 and Gdk 3.0 by the time
# this is imported, so these are safe and are the same objects Orca itself
# holds.
from gi.repository import Gdk, GLib, Gtk

from .clip import PUMP_LIMIT as _PUMP_LIMIT
from .clip import read as _read
from .clip import usable as _usable
from .clip import write as _write

_log = logging.getLogger("orca-wayland-clipboard")

_orig_set_contents = None
_orig_get_contents = None
_installed = False


def _patched_set_contents(self, text: str) -> None:
    """Put text on the selection with wl-copy."""
    try:
        if _write(text):
            _log.debug("WaylandClipboard: set %d characters via wl-copy", len(text))
            return
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: set_contents fell back: %s", error)
    _orig_set_contents(self, text)


def _pump() -> None:
    """Let Orca's main loop run a little, mid-read.

    This is the whole reason the read is not a plain ``subprocess.run``. The
    selection is served lazily by whoever owns it, and when the owner is Orca
    -- which it is for anything copied out of the speech monitor's text view
    -- the only thread that can answer the request is the one doing the
    asking. Pumping hands it back for long enough to answer.

    Bounded, because an unbounded pump inside a key handler is a different
    way to hang.
    """
    context = GLib.MainContext.default()
    for _ in range(_PUMP_LIMIT):
        if not context.pending():
            return
        context.iteration(False)


def _local_text() -> str | None:
    """What Gtk.Clipboard holds locally, or None.

    Only consulted after a read has failed. When this process owns the
    selection, GTK answers from its own copy instantly -- measured at 0.000s,
    against 0.024s to return None for a selection owned by somebody else. It
    can be stale, which is why it is second and not first; but a stale answer
    beats the empty string Orca would otherwise report.
    """
    try:
        gtk_clipboard = Gtk.Clipboard.get(Gdk.Atom.intern("CLIPBOARD", False))
        return gtk_clipboard.wait_for_text()
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: Gtk.Clipboard read failed: %s", error)
        return None


def _patched_get_contents(self) -> str:
    """Read the selection with wl-paste, pumping the main loop while it works.

    Orca's own version returns "" both for "no text" and for "could not ask",
    so it cannot tell them apart. This one can, and only falls back when the
    answer is genuinely unknown.
    """
    try:
        text = _read(pump=_pump)
        if text is not None:
            return text
        local = _local_text()
        if local is not None:
            _log.debug("WaylandClipboard: answered from Gtk's local copy")
            return local
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: get_contents fell back: %s", error)
    return _orig_get_contents(self)


def install() -> None:
    """Replace the fallback manager's two methods, if there is any point."""
    global _orig_set_contents, _orig_get_contents, _installed
    if _installed:
        return
    if not _usable():
        _log.info("WaylandClipboard: not a Wayland session, or wl-clipboard is "
                  "not installed; leaving Orca's clipboard alone.")
        return
    manager = getattr(clipboard, "_ClipboardManagerFallback", None)
    if manager is None:
        _log.warning("WaylandClipboard: this Orca has no "
                     "_ClipboardManagerFallback; leaving the clipboard alone.")
        return
    _orig_set_contents = manager.set_contents
    _orig_get_contents = manager._get_contents  # pylint: disable=protected-access
    manager.set_contents = _patched_set_contents
    manager._get_contents = _patched_get_contents  # pylint: disable=protected-access
    _installed = True
    _log.info("WaylandClipboard: Orca's clipboard now goes through wl-copy "
              "and wl-paste.")


def uninstall() -> None:
    """Put Orca's own methods back."""
    global _orig_set_contents, _orig_get_contents, _installed
    if not _installed:
        return
    manager = clipboard._ClipboardManagerFallback  # pylint: disable=protected-access
    manager.set_contents = _orig_set_contents
    manager._get_contents = _orig_get_contents  # pylint: disable=protected-access
    _orig_set_contents = None
    _orig_get_contents = None
    _installed = False
    _log.info("WaylandClipboard: restored Orca's own clipboard methods.")
