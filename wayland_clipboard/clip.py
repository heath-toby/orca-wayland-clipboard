"""Give Orca a clipboard that works on Wayland.

Orca's clipboard commands -- copy the flat review line, append to the
clipboard, speak the clipboard contents -- go through a manager abstraction in
``orca/clipboard.py``. When neither Klipper nor GPaste is running, which is the
case on a plain Wayland session, it falls back to ``Gtk.Clipboard``.

**That fallback silently does nothing here.** Measured: setting text with
``Gtk.Clipboard.set_text`` followed by ``store()``, then pumping the main
context for half a second, leaves the selection untouched -- ``wl-paste``
still reported the image that was on the clipboard beforehand. Reading is no
better: ``wait_for_text()`` returns None even for text this process has just
put there with ``wl-copy``. Both are the same cause. A Wayland client may only
own or read the selection with a focused surface and a valid input serial, and
Orca has neither; it is a screen reader, not a window you are typing into.

The data-control protocols exist precisely for this case, and the compositor
here advertises both ``ext_data_control_manager_v1`` and
``zwlr_data_control_manager_v1``. ``wl-copy`` and ``wl-paste`` speak them, so
this hands the work to them.

Two traps, both of which have already cost real time.

**Never wait on wl-copy.** It daemonises and stays alive to serve the
selection, so waiting on it hangs until the next copy replaces it. Write the
text, close stdin, let it go. That one cost a forty-second hang.

**Never block the main loop on wl-paste.** The clipboard is handed over
lazily: whoever owns the selection must answer the request from its own event
loop. If the owner cannot run, ``wl-paste`` waits for it -- measured at a
clean six seconds and counting against an owner held under SIGSTOP, with no
timeout of its own. Orca can be that owner: copy from the speech monitor and
the selection belongs to Orca's own ``GtkTextView``. A blocking read then asks
Orca for the data from inside the one thread that could give it, and nothing
happens until the timeout expires. Several of those per copy is the nine
seconds this version fixes. So the read drains the pipe without blocking and
pumps the caller's main loop while it waits -- which both breaks the deadlock
and lets the owner answer.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import time
from typing import Callable

_log = logging.getLogger("orca-wayland-clipboard")

# text/plain;charset=utf-8 is what GTK and most toolkits offer and ask for.
_MIME = "text/plain;charset=utf-8"

# A selection transfer between two local clients is single-digit milliseconds.
# This is not a budget, it is a backstop for a transfer that is never going to
# arrive, and it is deliberately short: every millisecond spent here is a
# millisecond of silence from a screen reader.
_READ_TIMEOUT = 0.5

# How long to wait on the pipe between pumps of the caller's main loop.
_POLL_SECONDS = 0.002

# At most this many main-loop iterations per pump, so that a busy loop cannot
# keep us here past the deadline.
PUMP_LIMIT = 16

# A write is not visible to a reader instantly: wl-copy has to be handed the
# selection by the compositor first, and until it is, the *previous* owner is
# still answering. Measured at under 3ms, but a read landing inside that gap
# gets the outgoing value -- and Orca reads before it appends, so that is how
# an append used to graft itself onto whatever had been on the clipboard
# before. Inside this window, answer with what we wrote: the clipboard is
# about to hold it, and a few milliseconds early is the correct answer.
_WRITE_MEMO_SECONDS = 0.3

_last_written: str | None = None
_last_written_at = 0.0

# Guards against re-entry: pumping the main loop can dispatch Orca code that
# asks for the clipboard again, and a nested read would be waiting on a pipe
# nobody is draining.
_reading = False


def on_wayland() -> bool:
    """True if this is a Wayland session."""
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def tools_present() -> bool:
    """True if wl-copy and wl-paste are both available."""
    return bool(shutil.which("wl-copy") and shutil.which("wl-paste"))


def usable() -> bool:
    """True if this extension can do anything useful."""
    return on_wayland() and tools_present()


def write(text: str) -> bool:
    """Put text on the clipboard. Returns True if it was handed over.

    Does not wait for wl-copy: it has to outlive this call to keep serving the
    selection, so waiting would hang until the next copy replaced it.
    """
    global _last_written, _last_written_at
    try:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            ["wl-copy", "--type", _MIME],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if process.stdin is None:
            return False
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
        _last_written = text
        _last_written_at = time.monotonic()
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: wl-copy failed: %s", error)
        return False


def _drain(
    process: subprocess.Popen,
    timeout: float,
    pump: Callable[[], None] | None,
) -> tuple[list[bytes], bool]:
    """Read everything wl-paste has to say without ever blocking on it.

    Returns the chunks read and whether the deadline passed first. The pipe is
    drained as it fills rather than at the end, because a clipboard larger
    than the pipe buffer would otherwise stall wl-paste on the write and look
    exactly like the deadlock this is here to avoid.
    """
    assert process.stdout is not None
    handle = process.stdout.fileno()
    os.set_blocking(handle, False)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while True:
        try:
            chunk = os.read(handle, 65536)
        except BlockingIOError:
            chunk = None
        except OSError:
            break
        if chunk == b"":
            break
        if chunk:
            chunks.append(chunk)
            continue
        if time.monotonic() >= deadline:
            return chunks, True
        if pump is not None:
            pump()
        select.select([handle], [], [], _POLL_SECONDS)
    try:
        process.wait(timeout=max(_POLL_SECONDS, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return chunks, True
    return chunks, False


def read(
    timeout: float = _READ_TIMEOUT,
    pump: Callable[[], None] | None = None,
) -> str | None:
    """The clipboard's text, "" if it holds no text, or None on failure.

    The distinction matters: "" means the clipboard is genuinely textless --
    an image, say -- and None means we could not find out, in which case the
    caller should fall back rather than report an empty clipboard.

    ``pump`` is called repeatedly while waiting, and should iterate the
    caller's main loop once or twice. Pass it from anything with a main loop:
    without it, a selection owned by this very process can never answer.
    """
    global _reading
    if _reading:
        # Re-entered through the pump. Not an error, and not something to
        # guess at: say we do not know and let the caller fall back.
        _log.debug("WaylandClipboard: nested read; deferring to the caller")
        return None
    try:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            ["wl-paste", "--type", "text/plain", "--no-newline"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: wl-paste failed to start: %s", error)
        return None

    _reading = True
    try:
        chunks, timed_out = _drain(process, timeout, pump)
    except Exception as error:  # pylint: disable=broad-exception-caught
        _log.warning("WaylandClipboard: wl-paste failed: %s", error)
        chunks, timed_out = [], True
    finally:
        _reading = False
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    if timed_out:
        process.kill()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        _log.warning(
            "WaylandClipboard: the selection owner did not answer in %.0fms; "
            "leaving this read to the caller", timeout * 1000)
        return None

    if process.returncode != 0:
        # wl-paste exits non-zero when the selection holds nothing it can give
        # us as text, which is a real answer rather than a failure.
        return ""

    # Decode defensively: the selection is whatever another application put
    # there, and it is not obliged to be valid UTF-8.
    text = b"".join(chunks).decode("utf-8", "replace")
    if (_last_written is not None
            and text != _last_written
            and time.monotonic() - _last_written_at < _WRITE_MEMO_SECONDS):
        # Inside the handover. What came back is the outgoing owner's value,
        # measured once as a 300KB string where "" had been expected -- so
        # this cannot be conditioned on the answer looking empty.
        _log.debug("WaylandClipboard: read landed inside our own write; "
                   "answering from what we wrote")
        return _last_written
    return text
