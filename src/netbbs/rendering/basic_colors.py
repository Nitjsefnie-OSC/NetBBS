"""
The 16 classic ANSI colours, for a terminal that shows no more (issue
#1348; design doc §3.2, "Colour depth per session").

NetBBS draws in 256 colours (`netbbs.rendering.ansi`), and in truecolor
where the terminal shows it. A classic BBS terminal that knows only the
16 does not skip `ESC[38;5;Nm`: it reads the parameters one by one, so
the `5` turns on blink and the palette index lands on whatever attribute
has that number -- 7 reverses, 8 conceals, 30-47 pick a colour. The
stock fTelnet embed on BBS directory sites is one such terminal.

`BasicColorFilter` rewrites a session's output on its way out: every
256-colour and truecolor SGR becomes the nearest of the 16, a bright
foreground as bold plus 30-37 (what ANSI.SYS and every terminal after it
reads), a background one of the eight. It keeps what the stream itself
asked of bold apart from the bold it adds for brightness, so a bright
colour never leaves the next span bold.

The nearest colour is the closest in CIELAB to the 16 as classic
terminals draw them (the VGA palette), so dark art stays dark instead of
turning into full-strength blue and magenta; an interface colour picked
for its tint (a muted blue, a warm amber) never becomes a grey.
"""

from __future__ import annotations

import math
import re

# ESC [ params m, with only digits and `;` between: the forms NetBBS and
# the art it shows write. A sequence with `:` sub-parameters passes as is.
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# The start of an escape sequence cut off by the end of a chunk.
_PARTIAL = re.compile(r"\x1b(?:\[[0-9;:?]*)?\Z")
# The longest cut-off sequence held back for the next chunk; anything
# longer is not an SGR this filter would rewrite.
_MAX_PARTIAL = 64

# The xterm palette's first 16, as the ANSI colours they stand for.
_XTERM_16 = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)

# How much colour (CIELAB chroma) makes a colour tinted rather than grey.
_TINT_CHROMA = 20.0


def palette_rgb(index: int) -> tuple[int, int, int]:
    """The colour xterm shows for 256-palette `index`."""
    if index < 16:
        return _XTERM_16[index]
    if index < 232:
        index -= 16
        return (_CUBE_LEVELS[index // 36], _CUBE_LEVELS[index // 6 % 6], _CUBE_LEVELS[index % 6])
    level = 8 + 10 * (index - 232)
    return (level, level, level)


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """CIELAB (D65) for an sRGB colour: distances in it follow what the eye
    sees, which RGB distances do not."""

    def linear(channel: int) -> float:
        c = max(0, min(255, channel)) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


# The 16 as classic terminals draw them: the VGA palette, which SyncTERM
# and fTelnet use, not xterm's.
_VGA = (
    (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
    (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
    (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
    (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
)
_VGA_LAB = tuple(_lab(rgb) for rgb in _VGA)
_GREYS = frozenset({7, 8, 15})


def nearest_basic(rgb: tuple[int, int, int], *, background: bool = False) -> int:
    """The nearest of the 16 ANSI colours (0-7 normal, 8-15 bright) to
    `rgb` as a classic terminal draws them, by distance in CIELAB; a
    `background` is one of the eight. A colour with a clear tint never
    becomes a grey -- a muted interface blue stays blue -- but a dark one
    may still become black."""
    lab = _lab(rgb)
    tinted = math.hypot(lab[1], lab[2]) >= _TINT_CHROMA
    candidates = range(8) if background else range(16)

    def distance(index: int) -> float:
        return sum((p - q) ** 2 for p, q in zip(lab, _VGA_LAB[index]))

    best = min(candidates, key=distance)
    if tinted and best in _GREYS:
        best = min((i for i in candidates if i not in _GREYS), key=distance)
    return best


def basic_for_index(index: int, *, background: bool = False) -> int:
    """`nearest_basic` for a 256-palette index; the first 16 are already
    basic colours."""
    if 0 <= index < 16:
        return index % 8 if background else index
    return nearest_basic(palette_rgb(max(0, min(255, index))), background=background)


def _int(text: str) -> int:
    return int(text) if text else 0


class BasicColorFilter:
    """Rewrites one session's output to the 16 colours. Keeps the state
    the terminal has -- whether the stream asked for bold, whether the
    current foreground is bright -- across calls, since one write's
    colours carry into the next."""

    def __init__(self) -> None:
        self._bold = False  # the stream asked for bold
        self._bright = False  # the current foreground is a bright colour
        self._wire_bold = False  # what the terminal was last told
        self._pending = ""  # a cut-off sequence from the last raw chunk

    def text(self, text: str) -> str:
        """`text` as the terminal should get it. A sequence cut off at the
        end goes out unchanged."""
        if self._pending:
            text, self._pending = self._pending + text, ""
        if "\x1b[" not in text:
            return text
        return _SGR.sub(self._rewrite, text)

    def chunk(self, data: bytes) -> bytes:
        """`text` for a door's raw byte stream, in which a sequence may be
        split between chunks: the start of one is held back until the rest
        arrives. Escape sequences are ASCII, so the bytes go through
        Latin-1 unchanged whatever the stream's character set."""
        text = self._pending + data.decode("latin-1")
        self._pending = ""
        cut = _PARTIAL.search(text)
        if cut is not None and len(text) - cut.start() <= _MAX_PARTIAL:
            text, self._pending = text[:cut.start()], text[cut.start():]
        if "\x1b[" in text:
            text = _SGR.sub(self._rewrite, text)
        return text.encode("latin-1")

    def flush(self) -> bytes:
        """Whatever was held back, unchanged: the stream ended or gave way
        to a binary transfer."""
        pending, self._pending = self._pending, ""
        return pending.encode("latin-1")

    def _rewrite(self, match: re.Match[str]) -> str:
        params = match.group(1).split(";")
        out: list[str] = []
        index = 0
        while index < len(params):
            code = _int(params[index])
            index += 1
            if code == 0:
                self._bold = self._bright = self._wire_bold = False
                out.append("0")
            elif code == 1:
                self._bold = True
            elif code == 22:
                self._bold = False
            elif 30 <= code <= 37 or code == 39:
                self._bright = False
                out.append(str(code))
            elif 90 <= code <= 97:
                self._bright = True
                out.append(str(code - 60))
            elif 100 <= code <= 107:
                # A bright background: art drawn for iCE colours, which
                # `netbbs.net.session.prepare_art_text` already handles.
                out.append(str(code))
            elif code in (38, 48):
                color, index = self._extended(params, index, background=code == 48)
                if color is None:
                    continue
                if code == 38:
                    self._bright = color >= 8
                    out.append(str(30 + color % 8))
                else:
                    out.append(str(40 + color % 8))
            else:
                out.append(str(code))
        wanted = self._bold or self._bright
        if wanted != self._wire_bold:
            out.append("1" if wanted else "22")
            self._wire_bold = wanted
        if not out:
            return ""
        return f"\x1b[{';'.join(out)}m"

    @staticmethod
    def _extended(params: list[str], index: int, *, background: bool) -> tuple[int | None, int]:
        """The basic colour a `38`/`48` asks for, read from the parameters
        after it, and where the next parameter starts."""
        kind = _int(params[index]) if index < len(params) else None
        if kind == 5 and index + 1 < len(params):
            return basic_for_index(_int(params[index + 1]), background=background), index + 2
        if kind == 2 and index + 3 < len(params):
            rgb = (_int(params[index + 1]), _int(params[index + 2]), _int(params[index + 3]))
            return nearest_basic(rgb, background=background), index + 4
        # Malformed: drop the rest, as a terminal that knows the form would.
        return None, len(params)
