"""Shared terminal presentation for the DuckMind, Bleo and GNG chat CLIs.

The same module is vendored by duckmind, bleo and gng so each package stays
dependency-free and installable on its own.  The visual grammar is deliberately
small: an emoji + coloured name, a two-line welcome banner, an accent-coloured
prompt, plain streamed answers, and interactive model pickers.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import select
import shutil
import sys
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, TextIO

try:
    import termios
except ImportError:  # pragma: no cover - Windows fallback
    termios = None

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_CLEAR_LINE = "\r\x1b[2K"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


@dataclass(frozen=True)
class Theme:
    """Identity of one CLI.

    ``badge`` renders the accent as black text on a white block.  That keeps
    GNG's black accent visible on dark terminals without hard-coding a
    background colour.
    """

    name: str
    emoji: str
    accent: int
    tagline: str
    badge: bool = False


@dataclass(frozen=True)
class PickerItem:
    """One selectable row in an interactive picker."""

    value: str
    label: str
    detail: str = ""


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def color_enabled(stream: Optional[TextIO] = None) -> bool:
    stream = stream if stream is not None else sys.stdout
    force = os.environ.get("FORCE_COLOR", "")
    if force and force != "0":
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return _isatty(stream)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    if 0x1F300 <= ord(char) <= 0x1FAFF:
        return 2
    return 1


def visible_width(text: str) -> int:
    return sum(_char_width(char) for char in strip_ansi(text))


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    result = []
    used = 0
    for char in text:
        char_width = _char_width(char)
        if used + char_width > width:
            if result:
                result[-1] = "…"
            return "".join(result)
        result.append(char)
        used += char_width
    return "".join(result)


def _pad(text: str, length: int, right: bool = False) -> str:
    gap = " " * max(0, length - visible_width(text))
    return gap + text if right else text + gap


def human_count(value: Optional[int]) -> str:
    if not value:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def read_text_file(path: str, limit: int = 1_000_000) -> str:
    file = Path(path)
    if file.stat().st_size > limit:
        raise ValueError(f"file is larger than {limit // 1000} kB")
    return file.read_text(encoding="utf-8")


def image_data_url(path: str, limit: int = 20 * 1024 * 1024) -> str:
    file = Path(path)
    if file.stat().st_size > limit:
        raise ValueError("image is larger than 20 MB")
    mime = mimetypes.guess_type(str(file))[0] or "image/png"
    if not mime.startswith("image/"):
        raise ValueError("not an image file")
    encoded = base64.b64encode(file.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def truncated(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@contextmanager
def _editor_terminal(fd: int):
    """Disable line buffering, echo and signal keys, but keep output processing."""
    if termios is None:
        yield
        return
    previous = termios.tcgetattr(fd)
    current = termios.tcgetattr(fd)
    current[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
    current[6][termios.VMIN] = 1
    current[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, current)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


class _KeyReader:
    """Read one logical key from a terminal in non-canonical mode."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def _ready(self, timeout: float = 0.02) -> bool:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return bool(ready)

    def read(self):
        try:
            first = os.read(self.fd, 1)
        except InterruptedError:
            return "ignore"
        except OSError:
            return "eof"
        if not first:
            return "eof"

        if first == b"\x1b":
            if not self._ready():
                return "esc"
            second = os.read(self.fd, 1)
            if second not in (b"[", b"O"):
                return "ignore"
            third = os.read(self.fd, 1)
            if third == b"A":
                return "up"
            if third == b"B":
                return "down"
            if third == b"C":
                return "right"
            if third == b"D":
                return "left"
            if third in (b"H",):
                return "home"
            if third in (b"F",):
                return "end"
            if third in b"12345678":
                sequence = bytearray(third)
                while len(sequence) < 8 and self._ready(0.005):
                    char = os.read(self.fd, 1)
                    if not char:
                        break
                    sequence += char
                    if char in b"~ABCDHF":
                        break
                code = sequence.decode("ascii", "ignore")
                if code in ("1~", "7~", "H"):
                    return "home"
                if code in ("4~", "8~", "F"):
                    return "end"
                if code == "3~":
                    return "delete"
                if code == "5~":
                    return "page-up"
                if code == "6~":
                    return "page-down"
            return "ignore"

        mapping = {
            b"\r": "enter",
            b"\n": "enter",
            b"\t": "tab",
            b"\x7f": "backspace",
            b"\x08": "backspace",
            b"\x01": "ctrl-a",
            b"\x03": "ctrl-c",
            b"\x04": "ctrl-d",
            b"\x05": "ctrl-e",
            b"\x0b": "ctrl-k",
            b"\x0c": "ctrl-l",
            b"\x15": "ctrl-u",
            b"\x17": "ctrl-w",
        }
        if first in mapping:
            return mapping[first]
        if first[0] < 0x80:
            return ("char", first.decode("ascii"))
        length = 2
        if first[0] & 0xF0 == 0xE0:
            length = 3
        elif first[0] & 0xF8 == 0xF0:
            length = 4
        data = bytearray(first)
        while len(data) < length:
            char = os.read(self.fd, 1)
            if not char:
                break
            data += char
        return ("char", data.decode("utf-8", "replace"))


class UI:
    """Theme-aware stdout/stderr helpers used by all three REPLs."""

    def __init__(
        self,
        theme: Theme,
        *,
        out: Optional[TextIO] = None,
        err: Optional[TextIO] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.theme = theme
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self.enabled = color_enabled(self.out) if enabled is None else enabled
        self.interactive = _isatty(self.out) and _isatty(sys.stdin) and termios is not None
        self.history: list[str] = []

    # ---------------------------------------------------------- primitives

    def _style(self, text: str, code: str) -> str:
        if not self.enabled or not text:
            return text
        return f"{code}{text}\x1b[0m"

    def accent(self, text: str, *, badge: Optional[bool] = None) -> str:
        if not self.enabled or not text:
            return text
        if badge is None:
            badge = self.theme.badge
        if badge:
            return f"\x1b[1;30;107m{text}\x1b[0m"
        return f"\x1b[1;38;5;{self.theme.accent}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        return self._style(text, "\x1b[1;97m")

    def dim(self, text: str) -> str:
        return self._style(text, "\x1b[90m")

    def red(self, text: str) -> str:
        return self._style(text, "\x1b[1;31m")

    # -------------------------------------------------------------- chrome

    def brand(self) -> str:
        if self.theme.badge:
            return f"{self.theme.emoji}  {self.accent(f' {self.theme.name} ')}"
        return f"{self.theme.emoji}  {self.accent(self.theme.name)}"

    def banner(
        self,
        version: str,
        facts: Sequence[str] = (),
        hint: str = "/help commands · /models switch · Ctrl+D quit",
    ) -> None:
        print(f"  {self.brand()}  {self.dim('v' + version)}  {self.dim('·')}  {self.dim(self.theme.tagline)}", file=self.out)
        if facts:
            print("      " + self.dim(" · ".join(facts)), file=self.out)
        print("      " + self.dim(hint), file=self.out)
        print(file=self.out)

    def prompt(self) -> str:
        return f"{self.theme.emoji} {self.accent('❯')} "

    def info(self, message: str) -> None:
        print(message, file=self.out)

    def hint(self, message: str) -> None:
        print(self.dim(message), file=self.out)

    def success(self, message: str) -> None:
        print(f"{self.accent('✓')} {message}", file=self.out)

    def error(self, message: str, prefix: Optional[str] = None) -> None:
        label = prefix or self.theme.name.lower()
        print(f"{self.red(label + ':')} {message}", file=self.err)

    def section(self, title: str) -> None:
        print(self.accent(title), file=self.out)

    def table(
        self,
        title: Optional[str],
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        align_right: Iterable[int] = (),
        footer: Optional[str] = None,
    ) -> None:
        if title:
            print(self.accent(title), file=self.out)
        right = set(align_right)
        header_cells = [self.dim(header.upper()) for header in headers]
        widths = []
        for index, header in enumerate(header_cells):
            cells = [header] + [row[index] for row in rows]
            widths.append(max(visible_width(cell) for cell in cells))
        indent = "  "
        print(indent + "  ".join(_pad(cell, widths[i], i in right) for i, cell in enumerate(header_cells)), file=self.out)
        for row in rows:
            print(indent + "  ".join(_pad(cell, widths[i], i in right) for i, cell in enumerate(row)), file=self.out)
        if footer:
            print(self.dim(footer), file=self.out)
        print(file=self.out)

    def commands(self, entries: Sequence[tuple[str, str]]) -> None:
        self.section("Commands")
        width = max(len(command) for command, _ in entries) + 2
        for command, description in entries:
            print(f"  {self.accent(command.ljust(width))}{self.dim(description)}", file=self.out)
        print(file=self.out)

    # ------------------------------------------------------------ progress

    @contextmanager
    def thinking(self, label: str = "thinking"):
        """Animate a dim one-line status while a blocking request runs."""
        if not self.enabled or not _isatty(self.err):
            yield
            return
        stop = threading.Event()
        frames = ("·  ", "·· ", "···")

        def animate() -> None:
            index = 0
            while not stop.is_set():
                self.err.write(f"{_CLEAR_LINE}{self.dim(label + ' ' + frames[index % len(frames)])}")
                self.err.flush()
                index += 1
                stop.wait(0.25)

        worker = threading.Thread(target=animate, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=1)
            self.err.write(_CLEAR_LINE)
            self.err.flush()

    # ------------------------------------------------------- line editing

    def readline(
        self,
        prompt: str,
        *,
        completions: Sequence[str] = (),
        on_tab: Optional[Callable[[], Optional[str]]] = None,
        on_ctrl_w: Optional[Callable[[], Optional[str]]] = None,
    ):
        """Read one line, with Tab/Ctrl+W hooks on a real terminal.

        Non-interactive sessions (tests, pipes) fall back to built-in input().
        """
        if not self.interactive:
            return input(prompt)

        fd = sys.stdin.fileno()
        reader = _KeyReader(fd)
        buffer = ""
        cursor = 0
        history_index = len(self.history)
        draft = ""

        def render() -> None:
            columns = max(20, shutil.get_terminal_size((80, 24)).columns)
            available = max(1, columns - visible_width(prompt) - 1)
            start = 0
            if visible_width(buffer[:cursor]) > available:
                start = cursor
                used = 0
                while start > 0:
                    char_width = _char_width(buffer[start - 1])
                    if used + char_width > available:
                        break
                    used += char_width
                    start -= 1
            used = visible_width(buffer[start:cursor])
            end = cursor
            while end < len(buffer):
                char_width = _char_width(buffer[end])
                if used + char_width > available:
                    break
                used += char_width
                end += 1
            visible = buffer[start:end]
            cursor_column = visible_width(buffer[start:cursor])
            self.out.write("\r\x1b[2K" + prompt + visible)
            back = visible_width(visible) - cursor_column
            if back > 0:
                self.out.write(f"\x1b[{back}D")
            self.out.flush()

        def status(message: str) -> None:
            self.out.write("\r\x1b[2K" + message + "\n")
            self.out.flush()

        with _editor_terminal(fd):
            self.out.write(_HIDE_CURSOR)
            self.out.flush()
            try:
                render()
                while True:
                    key = reader.read()

                    if isinstance(key, tuple) and key[0] == "char":
                        char = key[1]
                        if char.isprintable():
                            buffer = buffer[:cursor] + char + buffer[cursor:]
                            cursor += 1
                    elif key == "enter":
                        self.out.write("\n")
                        self.out.flush()
                        if buffer.strip():
                            self.history.append(buffer)
                        return buffer
                    elif key == "backspace":
                        if cursor > 0:
                            buffer = buffer[: cursor - 1] + buffer[cursor:]
                            cursor -= 1
                    elif key == "delete":
                        if cursor < len(buffer):
                            buffer = buffer[:cursor] + buffer[cursor + 1:]
                    elif key == "left":
                        cursor = max(0, cursor - 1)
                    elif key == "right":
                        cursor = min(len(buffer), cursor + 1)
                    elif key in ("home", "ctrl-a"):
                        cursor = 0
                    elif key in ("end", "ctrl-e"):
                        cursor = len(buffer)
                    elif key == "ctrl-u":
                        buffer = buffer[cursor:]
                        cursor = 0
                    elif key == "ctrl-k":
                        buffer = buffer[:cursor]
                    elif key == "ctrl-l":
                        self.out.write("\x1b[2J\x1b[H")
                        self.out.flush()
                    elif key == "ctrl-c":
                        self.out.write("^C\n")
                        self.out.flush()
                        return ""
                    elif key in ("ctrl-d", "eof"):
                        if not buffer:
                            raise EOFError
                        if cursor < len(buffer):
                            buffer = buffer[:cursor] + buffer[cursor + 1:]
                    elif key == "tab":
                        if buffer.startswith("/"):
                            if " " not in buffer:
                                matches = [command for command in completions if command.startswith(buffer)]
                                if len(matches) == 1:
                                    buffer = matches[0] + " "
                                    cursor = len(buffer)
                                elif matches:
                                    prefix = os.path.commonprefix(matches)
                                    if len(prefix) > len(buffer):
                                        buffer = prefix
                                        cursor = len(buffer)
                                    else:
                                        status("  ".join(matches))
                        elif on_tab is not None:
                            message = on_tab()
                            if message:
                                status(message)
                    elif key == "ctrl-w":
                        if on_ctrl_w is not None:
                            message = on_ctrl_w()
                            if message:
                                status(message)
                        else:
                            start = cursor
                            while start > 0 and buffer[start - 1].isspace():
                                start -= 1
                            while start > 0 and not buffer[start - 1].isspace():
                                start -= 1
                            buffer = buffer[:start] + buffer[cursor:]
                            cursor = start
                    elif key == "up":
                        if self.history:
                            if history_index == len(self.history):
                                draft = buffer
                            if history_index > 0:
                                history_index -= 1
                                buffer = self.history[history_index]
                                cursor = len(buffer)
                    elif key == "down":
                        if history_index < len(self.history):
                            history_index += 1
                            if history_index == len(self.history):
                                buffer = draft
                            else:
                                buffer = self.history[history_index]
                            cursor = len(buffer)
                    elif key == "page-up":
                        cursor = max(0, cursor - 10)
                    elif key == "page-down":
                        cursor = min(len(buffer), cursor + 10)

                    render()
            finally:
                self.out.write(_SHOW_CURSOR)
                self.out.flush()

    # ------------------------------------------------------------ pickers

    def pick(
        self,
        title: str,
        items: Sequence[PickerItem],
        *,
        current: Optional[str] = None,
        footer: str = "type to filter · ↑/↓ move · Enter select · Esc cancel",
    ) -> Optional[str]:
        """Open an inline interactive picker and return the chosen value."""
        if not self.interactive or not items:
            return None

        fd = sys.stdin.fileno()
        reader = _KeyReader(fd)
        query = ""
        cursor = 0
        height = 0
        terminal = shutil.get_terminal_size((80, 24))
        max_rows = max(1, min(12, len(items), terminal.lines - 4))

        def filtered():
            needle = query.casefold()
            if not needle:
                return list(items)
            return [
                item for item in items
                if needle in item.label.casefold() or needle in item.detail.casefold()
            ]

        if current:
            for index, item in enumerate(items):
                if item.value == current:
                    cursor = index
                    break

        def render() -> None:
            nonlocal cursor, height
            visible = filtered()
            if visible:
                cursor = min(cursor, len(visible) - 1)
            else:
                cursor = 0
            start = 0
            if len(visible) > max_rows:
                start = min(max(0, cursor - max_rows + 1), len(visible) - max_rows)
            width = max(20, shutil.get_terminal_size((80, 24)).columns)
            lines = [self.accent(title)]
            search = "  " + self.dim("filter: ") + (query or self.dim("type to filter"))
            if query:
                search += self.accent("▏")
            lines.append(search)
            row_count = min(max_rows, len(visible)) if visible else 1
            for row in range(row_count):
                index = start + row
                if index >= len(visible):
                    lines.append("  " + self.dim("(no matches)"))
                    continue
                item = visible[index]
                selected = index == cursor
                marker = self.accent("❯") if selected else " "
                current_marker = self.accent("●") if item.value == current else " "
                prefix = f" {marker} {current_marker} "
                available = max(1, width - visible_width(prefix) - 2)
                label = item.label
                detail = item.detail
                if visible_width(label) > available:
                    label = _clip(label, available)
                    detail = ""
                else:
                    detail = _clip(detail, max(0, available - visible_width(label) - 2))
                line = prefix + (self.bold(label) if selected else label)
                if detail:
                    line += "  " + self.dim(detail)
                lines.append(line)
            lines.append(self.dim(_clip(footer, width)))
            new_height = len(lines)
            if height:
                self.out.write(f"\x1b[{height}A")
            for index in range(new_height):
                self.out.write("\x1b[2K")
                self.out.write(lines[index])
                self.out.write("\n")
            self.out.write("\x1b[J")
            self.out.flush()
            height = new_height

        chosen = None
        with _editor_terminal(fd):
            self.out.write(_HIDE_CURSOR)
            self.out.flush()
            try:
                while True:
                    render()
                    key = reader.read()
                    visible = filtered()
                    if key == "up" and visible:
                        cursor = (cursor - 1) % len(visible)
                    elif key == "down" and visible:
                        cursor = (cursor + 1) % len(visible)
                    elif key == "home":
                        cursor = 0
                    elif key == "end" and visible:
                        cursor = len(visible) - 1
                    elif key == "page-up":
                        cursor = max(0, cursor - max_rows)
                    elif key == "page-down" and visible:
                        cursor = min(len(visible) - 1, cursor + max_rows)
                    elif key == "tab" and visible:
                        cursor = (cursor + 1) % len(visible)
                    elif key == "enter":
                        if visible:
                            chosen = visible[cursor].value
                            break
                    elif key in ("esc", "ctrl-c", "ctrl-d", "eof"):
                        break
                    elif key == "backspace":
                        if query:
                            query = query[:-1]
                            cursor = 0
                    elif key == "ctrl-u":
                        query = ""
                        cursor = 0
                    elif isinstance(key, tuple) and key[0] == "char":
                        char = key[1]
                        if char.isprintable():
                            query += char
                            cursor = 0
            finally:
                if height:
                    self.out.write(f"\x1b[{height}A\x1b[J")
                self.out.write(_SHOW_CURSOR)
                self.out.flush()
        return chosen
