"""Draw Harbor Lights' main menu: the art the website's main-menu capture shows.

    python scripts/website_harbor_lights_menu.py harbor-lights.ans

Harbor Lights is the node every website capture is taken on. Its main menu is
slot art (SysOp Handbook, "The main menu as your own art"): a night harbour
with a lighthouse whose beam sweeps across the sky, the node's name in block
letters, and a framed panel with every menu item drawn into it. NetBBS fills
the `{tokens}` with each caller's own values, blanks drawn items a caller
can't use, and puts the ones it doesn't draw (Games, Topics, Approvals...)
into the `{menu}` row.

It is drawn here rather than by hand so it can be redrawn: every cell is a
character and two colours on an 80x21 canvas, written out as 24-bit ANSI.
Only characters classic terminals have (CP437) are used.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

WIDTH, HEIGHT = 80, 21
RGB = tuple[int, int, int]

# -- palette ---------------------------------------------------------------
SKY_TOP, SKY_LOW = (6, 10, 32), (20, 34, 74)
WATER_TOP, WATER_LOW = (10, 30, 58), (4, 16, 34)
STAR = (196, 210, 255)
BEAM = (255, 226, 120)
LANTERN = (255, 244, 186)
RED, WHITE, ROCK = (196, 52, 46), (232, 232, 224), (70, 74, 86)
TITLE_FROM, TITLE_TO = (255, 214, 102), (255, 112, 84)
PANEL_BG, FRAME = (9, 18, 36), (64, 150, 168)
HEADING, LABEL, KEY, BRACKET = (110, 196, 206), (206, 214, 228), (255, 200, 80), (120, 132, 156)
MUTED, SLOT = (130, 146, 176), (236, 240, 250)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


class Canvas:
    def __init__(self) -> None:
        self.cells = [[(" ", MUTED, PANEL_BG, False) for _ in range(WIDTH)] for _ in range(HEIGHT)]

    def put(self, row: int, col: int, text: str, fg: RGB, bg: RGB | None = None, bold: bool = False) -> None:
        for offset, char in enumerate(text):
            if 0 <= col + offset < WIDTH:
                old = self.cells[row][col + offset]
                cell_bg = bg if bg is not None else old[2]
                if char == "█":
                    # A full block is a cell painted in its colour. A terminal
                    # shows the two alike; a web page with tall lines leaves a
                    # gap above and below a glyph, but not a background.
                    char, cell_bg = " ", fg
                self.cells[row][col + offset] = (char, fg, cell_bg, bold)

    def fill(self, row: int, bg: RGB) -> None:
        self.cells[row] = [(" ", MUTED, bg, False) for _ in range(WIDTH)]

    def bg(self, row: int, col: int) -> RGB:
        return self.cells[row][col][2]

    def ansi(self) -> bytes:
        out = []
        for row in self.cells:
            last = None
            line = []
            for char, fg, bg, bold in row:
                style = (fg, bg, bold)
                if style != last:
                    line.append("\x1b[0;{}38;2;{};{};{};48;2;{};{};{}m".format("1;" if bold else "", *fg, *bg))
                    last = style
                line.append(char)
            out.append("".join(line) + "\x1b[0m\r\n")
        return "".join(out).encode("utf-8")


# -- the block letters -------------------------------------------------------
FONT = {
    "H": ["█  █", "█  █", "████", "█  █", "█  █"],
    "A": [" ██ ", "█  █", "████", "█  █", "█  █"],
    "R": ["███ ", "█  █", "███ ", "█ █ ", "█  █"],
    "B": ["███ ", "█  █", "███ ", "█  █", "███ "],
    "O": [" ██ ", "█  █", "█  █", "█  █", " ██ "],
    "L": ["█   ", "█   ", "█   ", "█   ", "████"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "G": [" ███", "█   ", "█ ██", "█  █", " ███"],
    "T": ["███", " █ ", " █ ", " █ ", " █ "],
    "S": [" ███", "█   ", " ██ ", "   █", "███ "],
    " ": ["  ", "  ", "  ", "  ", "  "],
}


def draw_title(canvas: Canvas, text: str, row: int, col: int) -> None:
    glyphs = [FONT[c] for c in text]
    width = sum(len(g[0]) + 1 for g in glyphs) - 1
    x = col
    for glyph in glyphs:
        for dy, line in enumerate(glyph):
            for dx, char in enumerate(line):
                if char != " ":
                    shade = mix(TITLE_FROM, TITLE_TO, (x + dx - col) / max(1, width - 1))
                    canvas.put(row + dy, x + dx, char, shade, bold=True)
        x += len(glyph[0]) + 1


# -- the scene ---------------------------------------------------------------
def draw_scene(canvas: Canvas) -> None:
    for row in range(7):
        canvas.fill(row, mix(SKY_TOP, SKY_LOW, row / 6))
    for row in (7, 8):
        canvas.fill(row, mix(WATER_TOP, WATER_LOW, row - 7))

    # Stars stay out of the letters' box: one in a gap reads as a flaw.
    stars = random.Random(1994)
    placed = 0
    while placed < 22:
        row, col = stars.randrange(0, 7), stars.randrange(15, WIDTH)
        if 1 <= row <= 5 and 19 <= col:
            continue
        if canvas.cells[row][col][0] == " ":
            canvas.put(row, col, stars.choice("·∙·*"), mix(STAR, canvas.bg(row, col), stars.random() * 0.5))
            placed += 1
    canvas.put(0, 3, "∙", STAR)
    canvas.put(5, 1, "·", STAR)

    draw_title(canvas, "HARBOR LIGHTS", 1, 20)

    # The beam: brightest at the lantern, fading as it crosses the sky. It
    # passes behind the letters, so only sky cells take it.
    for col in range(11, 20):
        t = (col - 11) / 8
        char = "▓▒▒░░░·∙·"[col - 11]
        canvas.put(2, col, char, mix(BEAM, canvas.bg(2, col), t * 0.8))
    for col, char in zip(range(13, 19), "░░·∙· "):
        canvas.put(1, col, char, mix(BEAM, canvas.bg(1, col), 0.55))
        canvas.put(3, col, char, mix(BEAM, canvas.bg(3, col), 0.6))
    for col, char in zip(range(1, 6), "·░▒▒▓"):
        canvas.put(2, col, char, mix(BEAM, canvas.bg(2, col), 0.75 - col * 0.12))

    # The lighthouse, red and white bands widening to the rocks.
    canvas.put(0, 8, "▄", RED)
    canvas.put(1, 7, "▄█▄", RED)
    canvas.put(2, 6, "▐", ROCK)
    canvas.put(2, 7, "███", LANTERN, bold=True)
    canvas.put(2, 10, "▌", ROCK)
    canvas.put(3, 5, "▀▀▀▀▀▀▀", ROCK)
    canvas.put(4, 6, "█████", WHITE)
    canvas.put(5, 6, "█████", RED)
    canvas.put(6, 5, "███████", WHITE)
    canvas.put(7, 4, "▄", ROCK)
    canvas.put(7, 5, "███████", RED)
    canvas.put(7, 12, "▄▄", ROCK)
    canvas.put(7, 2, "▄▄", ROCK)

    # A small boat riding in, its lamp lit.
    canvas.put(6, 64, "│", (150, 150, 160))
    canvas.put(6, 65, "▄", BEAM)
    canvas.put(7, 61, "▀█████▀", (110, 74, 52))

    # The water, and the beam and lantern reflected in it.
    # Only open water takes a wave: a blank cell in the tower's or the
    # hull's colour is part of them, not of the sea.
    waves = random.Random(2026)
    for row in (7, 8):
        water = mix(WATER_TOP, WATER_LOW, row - 7)
        for col in range(WIDTH):
            if canvas.cells[row][col][0] == " " and canvas.bg(row, col) == water:
                canvas.put(row, col, waves.choice("≈≈~ "), mix((60, 110, 160), canvas.bg(row, col), waves.random() * 0.5))
    for col in range(5, 20):
        glow = 1 - abs(col - 8) / 12
        canvas.put(8, col, "≈" if col % 2 else "~", mix(BEAM, canvas.bg(8, col), 1 - glow * 0.85))
    canvas.put(8, 62, "~≈~", mix(BEAM, canvas.bg(8, 62), 0.4))


# -- the menu panel ----------------------------------------------------------
COLUMNS = (
    (3, "EXPLORE", ["[M]essage boards", "[C]hat", "[F]iles", "[N]ew scan", "[/] Find"]),
    (22, "MAIL & PEOPLE", ["[E]-mail", "[D]irectory", "[W]ho's online", "[R]ecent callers", "[O]perators"]),
    (42, "YOU", ["[P]rofile", "[H]istory", "[V]erify", "[?] Help"]),
    (61, "NODE", ["[S]ysOp", "[L]ogoff"]),
)


def draw_item(canvas: Canvas, row: int, col: int, item: str) -> None:
    close = item.index("]")
    canvas.put(row, col, "[", BRACKET)
    canvas.put(row, col + 1, item[1:close], KEY, bold=True)
    canvas.put(row, col + close, "]", BRACKET)
    canvas.put(row, col + close + 1, item[close + 1:], LABEL)


def draw_panel(canvas: Canvas) -> None:
    for row in range(9, HEIGHT):
        canvas.fill(row, PANEL_BG)
    canvas.put(9, 2, "Est. 1994", MUTED)
    canvas.put(9, 12, "·", FRAME)
    canvas.put(9, 14, "a safe harbor for callers", MUTED)
    canvas.put(9, 59, "{date 10}", SLOT)
    canvas.put(9, 71, "{time 5}", SLOT, bold=True)

    canvas.put(10, 0, "┌" + "─" * (WIDTH - 2) + "┐", FRAME)
    for row in range(11, 18):
        canvas.put(row, 0, "│", FRAME)
        canvas.put(row, WIDTH - 1, "│", FRAME)
    canvas.put(18, 0, "└" + "─" * (WIDTH - 2) + "┘", FRAME)
    for col, heading, items in COLUMNS:
        canvas.put(11, col, heading, HEADING, bold=True)
        for offset, item in enumerate(items):
            draw_item(canvas, 12 + offset, col, item)
    # Whatever a caller has that isn't drawn above: Games, Topics, Approvals.
    canvas.put(17, 3, "{menu 74x1}", LABEL)

    # A field is as wide as its number, not its token, so each label goes
    # before its value and the four sit in even columns.
    canvas.put(19, 2, "{user 16}", SLOT, bold=True)
    canvas.put(19, 22, "level", MUTED)
    canvas.put(19, 28, "{level 3}", SLOT)
    canvas.put(19, 42, "mail", MUTED)
    canvas.put(19, 47, "{mail 3}", SLOT)
    canvas.put(19, 60, "online", MUTED)
    canvas.put(19, 67, "{online 3}", SLOT)
    canvas.put(20, 2, "{prompt}", KEY, bold=True)


def art() -> bytes:
    canvas = Canvas()
    draw_scene(canvas)
    draw_panel(canvas)
    return canvas.ansi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help=".ans file to write")
    args = parser.parse_args()
    args.output.write_bytes(art())
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
