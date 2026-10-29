# Wayland Clipboard for Orca

Makes Orca's copy and clipboard commands work on a Wayland session.

## The problem

Orca picks a clipboard manager at startup: Klipper, then GPaste 3, then
GPaste 2, then a fallback that uses `Gtk.Clipboard`. On a plain Wayland
session with none of those managers running, you get the fallback — and it
silently does nothing.

Measured on Niri: setting text with `Gtk.Clipboard.set_text` followed by
`store()`, then pumping the main context for half a second, leaves the
selection untouched. Reading is no better — `wait_for_text()` returns `None`
even for text this very process has just put on the clipboard with `wl-copy`.

Both have the same cause. A Wayland client may only own or read the selection
if it has a focused surface and a valid input serial, and Orca has neither. It
is a screen reader, not a window you type into.

The practical result is that "copy to clipboard" does nothing at all, "speak
the clipboard contents" reports nothing, and worst of the three, "append to
clipboard" reads nothing, concatenates onto nothing, and writes nothing —
quietly discarding what was there.

## What this does

Routes the fallback manager's two methods through `wl-copy` and `wl-paste`,
which speak the `ext_data_control_manager_v1` and
`zwlr_data_control_manager_v1` protocols made for exactly this case. Both
methods are called through the instance, so patching the class takes effect on
the manager Orca has already built.

If anything fails, Orca's own method runs instead. A clipboard that does
nothing is the status quo; one that raises would break the commands using it.

One trap on the write side, which cost a forty-second hang the first time it
was tried: **`wl-copy` daemonises and stays alive to serve the selection.**
Never wait on it and never capture its pipes — write the text, close stdin, let
it go.

## Why the read does not block

A Wayland selection is handed over lazily: the owner provides the data from its
own event loop when a reader asks. `wl-paste` waits on that, and it has no
timeout of its own — six seconds and still counting, measured against an owner
frozen under `SIGSTOP`.

Orca can be that owner. The speech monitor is a `Gtk.TextView` in Orca's own
process, so copying out of it gives Orca the selection. Reading it back with a
blocking `subprocess.run` then asks Orca for the data from inside the one thread
that could have answered, and nothing happens until the timeout expires. Four or
five of those per copy was the nine-second copy in 1.0.0.

So the read drains the pipe without blocking and hands the caller's main loop
back while it waits, which both breaks the deadlock and lets the owner answer.
It drains as the pipe fills rather than after the process exits, so a clipboard
larger than the 64KB pipe buffer cannot stall `wl-paste` on its own write and
reproduce the same hang from the other end. It refuses re-entry, since pumping
can dispatch Orca code that asks for the clipboard again. It gives up after
0.5 s rather than 2 s — a local transfer is single-digit milliseconds, so this
is not a budget but a limit on how long a screen reader may be silent for a
clipboard that is never going to arrive. And if it does give up, it consults
`Gtk.Clipboard`'s local copy before falling back to Orca's own method.

## Install

```
./install.sh
orca --replace &
```

To remove it again:

```
./uninstall.sh
orca --replace &
```

`install.sh` copies the package to
`$XDG_DATA_HOME/orca/extensions/wayland_clipboard` and approves it with
`orca --approve-extension`. Orca re-checks the hash of every file in the package
on each start, so re-run the installer after editing anything.

Needs `wl-copy` and `wl-paste` from the `wl-clipboard` package. The extension
stands aside entirely if `WAYLAND_DISPLAY` is unset or the tools are missing,
so it is harmless on X11.

## Settings

None. There is nothing to configure: it either applies or it stands aside.

## Tests

```
./install.sh && python3 tests-clipboard.py
```

35 rows, driving the real selection, because that is the only thing that
proves anything here — Orca's own fallback *looks* like it works. Whatever was
on the clipboard before the run is put back afterwards.

Two of the rows are about the premise rather than the fix: that Orca's own
`Gtk.Clipboard` write still does nothing, and that its read still cannot see
what is there. If either ever starts working, those rows fail and this
extension can be retired.

Several more are about the hang. One freezes a `wl-copy` owner with `SIGSTOP`
and requires the read to give up in well under a second, having pumped the
caller's loop while it waited; one re-enters the read from inside that pump and
requires the nested call to decline rather than wait on a pipe nobody is
draining; one round-trips 300KB, which is larger than the pipe buffer; and one
reads back inside the gap between handing `wl-copy` the text and the compositor
handing it the selection, where the *previous* owner is still answering.
