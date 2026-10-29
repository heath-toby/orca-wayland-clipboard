#!/usr/bin/env python3
"""Tests for the Wayland clipboard bridge.

These drive the real selection, because that is the only thing that proves
anything here: Orca's own `Gtk.Clipboard` fallback *looks* like it works and
silently does not. Whatever was on the clipboard before the run is put back
afterwards, text or not.

Run after ./install.sh -- it tests the installed package.

Usage: python3 tests-clipboard.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import orca.extension_loader  # noqa: F401  (import order: see install.sh)

sys.path.insert(0, os.path.expanduser("~/.local/share/orca/extensions"))

from orca import clipboard  # noqa: E402

from wayland_clipboard import clip, patches  # noqa: E402

rows, fails = [], 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    rows.append((name, want, got, "ok" if ok else "FAIL"))


def wl_paste_text():
    result = subprocess.run(
        ["wl-paste", "--type", "text/plain", "--no-newline"],
        capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


# Remember what was there, so the run leaves no trace.
saved_types = subprocess.run(["wl-paste", "--list-types"],
                             capture_output=True, text=True,
                             check=False).stdout.split()
saved_text = wl_paste_text()

# --- the environment this exists for ------------------------------------
check("this is a Wayland session", clip.on_wayland(), True)
check("wl-copy and wl-paste are installed", clip.tools_present(), True)
check("so the bridge is usable", clip.usable(), True)

# --- writing and reading through the bridge -----------------------------
marker = f"orca-wayland-clipboard-{int(time.time())}"
check("the bridge writes to the selection", clip.write(marker), True)
time.sleep(0.4)
check("...and wl-paste sees it", wl_paste_text(), marker)
check("...and the bridge reads it back", clip.read(), marker)

unicode_marker = "Здравствуйте — ünïcödé ✓"
check("unicode survives the round trip",
      clip.write(unicode_marker) and (time.sleep(0.4) or clip.read()),
      unicode_marker)

check("a multi-line value survives too",
      clip.write("one\ntwo\nthree") and (time.sleep(0.4) or clip.read()),
      "one\ntwo\nthree")

# --- Orca's own fallback manager, before and after patching -------------
# Orca's version is given its own chance first, and is expected to fail. If it
# ever starts working, this row fails and the extension can be retired.
patches.uninstall()
manager = clipboard._ClipboardManagerFallback(lambda _text: None)
manager.connect()
orca_marker = f"orca-gtk-{int(time.time())}"
clip.write("something else entirely")
time.sleep(0.3)
manager.set_contents(orca_marker)
time.sleep(0.5)
check("Orca's own Gtk.Clipboard write still does nothing here",
      wl_paste_text() == orca_marker, False)
check("...and its read cannot see what is there either",
      manager._get_contents() == "something else entirely", False)

patches.install()
check("installing reports itself done", patches._installed, True)
patched_marker = f"orca-patched-{int(time.time())}"
manager.set_contents(patched_marker)
time.sleep(0.4)
check("with the bridge in place, Orca's manager writes for real",
      wl_paste_text(), patched_marker)
check("...and reads it back", manager._get_contents(), patched_marker)

# --- append_text, which needs both halves to work -----------------------
# This is the command that was worst off: it reads, concatenates, and writes
# back, so a failing read silently threw away whatever was on the clipboard.
clip.write("first line")
time.sleep(0.3)
old = manager._get_contents()
manager.set_contents(f"{old}\nsecond line")
time.sleep(0.4)
check("appending keeps what was already there",
      wl_paste_text(), "first line\nsecond line")

# --- falling back rather than raising ------------------------------------
saved_write = patches._write
patches._write = lambda text: False
fell_back = []
saved_orig = patches._orig_set_contents
patches._orig_set_contents = lambda self, text: fell_back.append(text)
manager.set_contents("unreachable")
check("a failed write falls back to Orca rather than raising",
      fell_back, ["unreachable"])
patches._write = saved_write
patches._orig_set_contents = saved_orig


def _raises(*_args, **_kwargs):
    raise RuntimeError("no wl-paste")


saved_read = patches._read
patches._read = _raises
read_fell_back = []
saved_orig_get = patches._orig_get_contents
patches._orig_get_contents = lambda self: read_fell_back.append(1) or "orca read"
check("a read that raises falls back too", manager._get_contents(), "orca read")
patches._read = saved_read
patches._orig_get_contents = saved_orig_get

# --- the hang: a selection owner that cannot answer ----------------------
# This is the nine seconds. wl-paste has no timeout of its own: it waits on
# whoever owns the selection, and the owner answers from its own event loop.
# Orca can be that owner -- anything copied out of the speech monitor belongs
# to Orca's own GtkTextView -- so a blocking read asks Orca for the data from
# inside the only thread that could provide it. A frozen wl-copy stands in for
# a frozen Orca here; the question is whether the read gives up promptly and
# keeps pumping while it waits.
owner = subprocess.Popen(
    ["wl-copy", "--foreground", "--type", "text/plain;charset=utf-8"],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
owner.stdin.write(b"frozen owner")
owner.stdin.close()
time.sleep(0.4)
check("a live owner answers normally", clip.read(), "frozen owner")

pumped = []
try:
    os.kill(owner.pid, signal.SIGSTOP)
    started = time.monotonic()
    frozen_read = clip.read(timeout=0.3, pump=lambda: pumped.append(1))
    elapsed = time.monotonic() - started
finally:
    os.kill(owner.pid, signal.SIGCONT)

check("a frozen owner gives None, not a wrong answer", frozen_read, None)
check("...and gives up in well under a second", elapsed < 1.0, True)
check("...having pumped the caller's loop while it waited", bool(pumped), True)

# Re-entry: pumping dispatches Orca code, which may ask for the clipboard
# again. The nested read must decline rather than wait on a pipe nobody is
# draining.
nested = []
try:
    os.kill(owner.pid, signal.SIGSTOP)
    clip.read(timeout=0.3, pump=lambda: nested.append(clip.read(timeout=0.3)))
finally:
    os.kill(owner.pid, signal.SIGCONT)
    owner.kill()
    owner.wait()
check("a nested read declines instead of waiting",
      bool(nested) and set(nested) == {None}, True)
check("...and the guard is released afterwards", clip._reading, False)

# --- a clipboard bigger than the pipe buffer -----------------------------
# The pipe holds 64KB. Collecting the output only after the process exits
# would stall wl-paste on its own write and look exactly like the deadlock.
big = "x" * 300000
clip.write(big)
time.sleep(0.4)
check("300KB survives the read", clip.read(), big)

# --- reading inside our own write ----------------------------------------
# wl-copy does not have the selection the instant it is handed the text.
# Orca's append command reads before it writes, so a "" here is how it used to
# throw away what it was appending to.
clip.write("written just now")
check("a read inside our own write is not empty", clip.read(), "written just now")

# --- uninstall ----------------------------------------------------------
patches.uninstall()
check("uninstall puts Orca's own methods back",
      clipboard._ClipboardManagerFallback.set_contents
      is not patches._patched_set_contents, True)
check("...and forgets what it was holding",
      (patches._orig_set_contents, patches._orig_get_contents), (None, None))
patches.install()

# --- put the user's clipboard back --------------------------------------
if saved_text is not None:
    clip.write(saved_text)
    restored = "text restored"
elif saved_types:
    restored = f"left alone: held {' '.join(saved_types)[:40]}"
else:
    clip.write("")
    restored = "was empty, left empty"
check("the clipboard was put back", bool(restored), True)

def short(value):
    """Keep a 300KB clipboard from taking the whole terminal with it."""
    text = repr(value)
    return text if len(text) <= 48 else f"{text[:45]}...({len(text)} chars)"


width = max(len(r[0]) for r in rows)
print()
for name, want, got, status in rows:
    print(f"  {status:4s} {name:<{width}}  want={short(want)}  got={short(got)}")
print(f"\n{len(rows) - fails}/{len(rows)} rows green  ({restored})")
sys.exit(1 if fails else 0)
