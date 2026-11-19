"""
Make the terminal's own background match the Lumi theme while the TUI runs.

A TUI can only paint character cells; the terminal's window padding keeps the profile color (e.g. blue
around the app). So for the duration of the TUI we change the terminal background itself and restore it on exit:
  - Terminal.app: AppleScript on the tab that owns our tty (it ignores escape sequences for this)
  - other terminals (iTerm2, Ghostty, kitty, WezTerm, VS Code …): OSC 11 to set, OSC 111 to reset
Disable with LUMI_CREW_TERMINAL_BG=0.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable

_TAB_SCRIPT = '''
on run argv
  set target to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is target then
          if (count of argv) is 1 then
            set c to background color of t
            return ((item 1 of c) as text) & "," & ((item 2 of c) as text) & "," & ((item 3 of c) as text)
          else
            set background color of t to {(item 2 of argv) as integer, (item 3 of argv) as integer, (item 4 of argv) as integer}
            return "ok"
          end if
        end if
      end repeat
    end repeat
  end tell
  return ""
end run
'''


def _osascript(*args: str) -> str:
    r = subprocess.run(["osascript", "-e", _TAB_SCRIPT, *args], capture_output=True, text=True, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else ""


def _tty() -> str | None:
    for stream in (sys.stdout, sys.stdin, sys.stderr):
        try:
            return os.ttyname(stream.fileno())
        except (OSError, ValueError, AttributeError):
            continue
    return None


def hex_to_rgb16(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) * 257 for i in (0, 2, 4))      # 8-bit → Terminal.app's 16-bit


def apply(color: str) -> Callable[[], None]:
    """Set the terminal background to `color`; returns a function that restores the previous one."""
    def noop():
        pass

    if os.environ.get("LUMI_CREW_TERMINAL_BG", "1") == "0" or not sys.stdout.isatty():
        return noop

    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        tty = _tty()
        if not tty or not shutil.which("osascript"):
            return noop
        try:
            before = _osascript(tty)
            if not before:
                return noop
            if _osascript(tty, *map(str, hex_to_rgb16(color))) != "ok":
                return noop
        except (OSError, subprocess.SubprocessError):
            return noop

        def restore():
            try:
                _osascript(tty, *before.split(","))
            except (OSError, subprocess.SubprocessError):
                pass
        return restore

    # xterm-compatible terminals
    sys.stdout.write(f"\033]11;{color}\007")
    sys.stdout.flush()

    def reset():
        sys.stdout.write("\033]111\007")
        sys.stdout.flush()
    return reset
