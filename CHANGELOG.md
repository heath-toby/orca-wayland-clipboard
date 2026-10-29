# Changelog

## 1.1.0

**The read no longer blocks Orca's main loop.** Reported by Toby: *"copying from the
speech history panel takes about nine years."*

The cause is the one thing a clipboard bridge must not do. A Wayland selection is served
lazily: whoever owns it hands over the data from its own event loop when asked.
`wl-paste` therefore waits on the owner, and it has no timeout of its own -- measured at
a clean six seconds and still counting against an owner held under `SIGSTOP`.

Orca can be that owner. The speech monitor is a `Gtk.TextView` inside Orca's own process,
so anything copied out of it makes Orca's GTK the selection owner. 1.0.0 then read the
clipboard with a blocking `subprocess.run`, which asked Orca for the data from inside the
single thread that could have provided it. Nothing could happen until the 2-second
timeout expired, and Orca's fallback manager re-reads the clipboard on every owner change
and on every text-changed event it has to classify. Four or five of those is nine
seconds.

- The read drains `wl-paste` without ever blocking on it, and calls back into the
  caller's main loop while it waits. That both breaks the deadlock and lets the owner
  answer, Orca included.
- The backstop timeout is 2.0s -> **0.5s**. A local selection transfer is single-digit
  milliseconds; this is not a budget but a limit on how long a screen reader may be
  silent for a clipboard that is never going to arrive.
- The pipe is drained as it fills rather than after the process exits, so a clipboard
  larger than the 64KB pipe buffer cannot stall `wl-paste` on its own write and
  reproduce the same hang from the other end. Tested at 300KB.
- Re-entry is refused. Pumping the main loop can dispatch Orca code that asks for the
  clipboard again, and a nested read would wait on a pipe nobody is draining.
- If the read does give up, `Gtk.Clipboard`'s local copy is consulted before falling
  back to Orca. When this process owns the selection GTK answers from its own copy
  instantly (0.000s, against 0.024s to return None for a selection owned by somebody
  else). It can be stale, which is why it is second; a stale answer still beats the
  empty string Orca would otherwise report.
- A read landing inside our own write now answers with what was written. `wl-copy` does
  not have the selection the instant it is handed the text -- until the compositor gives
  it over, the *previous* owner is still answering, and a test caught a 300KB string
  coming back where the new value was expected. Orca reads before it appends, so that
  gap is how an append could graft itself onto the wrong thing.

27 test rows, including a frozen selection owner, the nested read, and the 300KB
round trip.

## 1.0.0

First release.

Orca's clipboard commands do nothing on a Wayland session with no clipboard manager
running: it falls back to `Gtk.Clipboard`, which cannot own or read the selection without
a focused surface and an input serial. Reported by Toby: *"copy from the speech history
viewer does nothing, because it can't paste correctly."*

Measured before fixing: `Gtk.Clipboard.set_text` plus `store()`, with the main context
pumped for half a second, left the selection untouched; and `wait_for_text()` returned
`None` even for text the same process had just put there with `wl-copy`.

The fallback manager's `set_contents` and `_get_contents` now go through `wl-copy` and
`wl-paste`, which speak the data-control protocols made for this case. Both are called
through the instance, so patching the class reaches the manager Orca has already built.

- Falls back to Orca's own method on any failure. A clipboard that does nothing is the
  status quo; one that raises would break the commands using it.
- Stands aside entirely when `WAYLAND_DISPLAY` is unset or wl-clipboard is missing.
- `read()` distinguishes "the clipboard holds no text" from "could not find out", which
  Orca's own version cannot, and only falls back for the latter.
- 19 test rows against the real selection, restoring it afterwards. Two assert the premise
  rather than the fix, so the extension announces its own obsolescence if upstream improves.
