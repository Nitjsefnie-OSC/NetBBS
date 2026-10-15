"""The 16 classic ANSI colours for terminals that show no more (issue #1348)."""

from __future__ import annotations

import asyncio
import re

import pytest

from netbbs.net.color_depth_preference import apply_color_depth, sixteen_colors_for
from netbbs.net.telnet import TelnetSession
from netbbs.rendering.ansi import bg, colored, fg_rgb
from netbbs.rendering.basic_colors import BasicColorFilter, basic_for_index, nearest_basic

ESC = "\x1b"
# Any code a 16-colour terminal misreads: 256-colour or truecolor.
_EXTENDED = re.compile(r"\x1b\[[0-9;]*\b[34]8;[25];")


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [
        ((0, 0, 0), 0),
        ((20, 20, 20), 0),
        ((68, 68, 68), 8),  # a dim interface grey stays readable
        ((170, 170, 170), 7),
        ((255, 255, 255), 15),
        ((215, 0, 0), 1),
        ((255, 60, 60), 9),
        ((95, 135, 175), 6),  # a muted blue keeps its tint, not grey
        ((0, 175, 175), 6),
        ((215, 175, 0), 11),  # amber
        ((135, 0, 175), 5),
        ((0, 135, 0), 2),
        ((135, 255, 135), 10),
    ],
)
def test_nearest_basic_by_what_the_eye_sees(rgb, expected):
    assert nearest_basic(rgb) == expected


def test_the_first_sixteen_palette_indexes_are_already_basic():
    assert [basic_for_index(i) for i in range(16)] == list(range(16))


def test_a_256_color_foreground_becomes_a_basic_one():
    out = BasicColorFilter().text(f"{ESC}[38;5;160mred{ESC}[0m")
    assert out == f"{ESC}[31mred{ESC}[0m"


def test_a_bright_foreground_is_bold_and_never_leaks_into_the_next_span():
    f = BasicColorFilter()
    out = f.text(f"{ESC}[38;5;9mbright{ESC}[38;5;160mdark")
    assert out == f"{ESC}[31;1mbright{ESC}[31;22mdark"


def test_bold_the_stream_asked_for_survives_a_dark_colour():
    f = BasicColorFilter()
    out = f.text(f"{ESC}[1;38;5;9mA{ESC}[38;5;160mB{ESC}[22mC")
    assert out == f"{ESC}[31;1mA{ESC}[31mB{ESC}[22mC"


def test_a_background_is_one_of_the_eight():
    assert BasicColorFilter().text(f"{ESC}[48;5;196mx") == f"{ESC}[41mx"


def test_truecolor_is_mapped_too():
    assert BasicColorFilter().text(f"{fg_rgb(0, 175, 175)}x") == f"{ESC}[36mx"


def test_aixterm_bright_codes_become_bold():
    assert BasicColorFilter().text(f"{ESC}[94mx{ESC}[0m") == f"{ESC}[34;1mx{ESC}[0m"


def test_other_attributes_and_plain_text_pass_unchanged():
    f = BasicColorFilter()
    text = f"plain {ESC}[4mline{ESC}[24m {ESC}[2J{ESC}[1;1H{ESC}[?25l"
    assert f.text(text) == text


def test_nothing_netbbs_draws_reaches_the_terminal_as_an_extended_code():
    text = colored("prompt", fg_color=245) + colored(" banner", fg_color=(255, 135, 0), bg_color=17) + bg(7)
    assert not _EXTENDED.search(BasicColorFilter().text(text))


def test_a_raw_stream_split_inside_a_sequence_is_rejoined():
    f = BasicColorFilter()
    out = f.chunk(b"ab\x1b[38;5") + f.chunk(b";160mc")
    assert out == b"ab\x1b[31mc"


def test_raw_bytes_outside_sequences_are_untouched():
    data = bytes([0xC9, 0xCD, 0xBB]) + "é".encode("utf-8")
    assert BasicColorFilter().chunk(data) == data


# -- the session ---------------------------------------------------------------


class _Writer:
    def __init__(self):
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False


def _telnet(*, sixteen: bool):
    writer = _Writer()
    session = TelnetSession(asyncio.StreamReader(), writer)
    session.sixteen_colors = session.detected_sixteen_colors = sixteen
    return session, writer


def test_a_sixteen_color_session_sends_no_extended_code_and_keeps_its_copy():
    async def scenario():
        session, writer = _telnet(sixteen=True)
        await session.write_line(colored("Username:", fg_color=245))
        await session.write_raw(b"\x1b[38;5;160mdoor\x1b[0m")
        return writer.data.decode("latin-1"), session

    data, session = asyncio.run(scenario())
    assert not _EXTENDED.search(data) and "\x1b[5" not in data
    assert "door" in data and "Username:" in data
    assert session.screen_copy().text_rows()[0].startswith("Username:")


def test_a_256_color_session_is_unchanged():
    async def scenario():
        session, writer = _telnet(sixteen=False)
        await session.write(colored("x", fg_color=245))
        return writer.data.decode("latin-1")

    assert "38;5;245" in asyncio.run(scenario())


def test_a_binary_transfer_is_never_rewritten():
    async def scenario():
        session, writer = _telnet(sixteen=True)
        frame = b"\x1b[38;5;160m" + bytes(range(0x20))
        with session.binary_transfer():
            await session.write_raw(frame)
        return frame, writer.data

    frame, data = asyncio.run(scenario())
    assert data == frame


@pytest.mark.parametrize(
    ("detected", "override", "expected"),
    [(True, None, True), (False, None, False), (True, "256", False), (True, "truecolor", False), (False, "16", True)],
)
def test_the_preference_replaces_what_was_detected(detected, override, expected):
    async def scenario():
        session, _writer = _telnet(sixteen=detected)
        chosen = sixteen_colors_for(session, override)
        apply_color_depth(session, override)
        return chosen, session.sixteen_colors

    assert asyncio.run(scenario()) == (expected, expected)


def test_a_background_is_never_bright_and_dark_art_stays_dark():
    assert nearest_basic((255, 60, 60), background=True) == 1
    assert nearest_basic((30, 20, 50), background=True) == 0  # a night sky, not blue
    assert basic_for_index(12, background=True) == 4


def test_a_late_colorterm_does_not_undo_the_callers_choice():
    # Claude review of #1349: a NEW-ENVIRON reply after sign-in re-runs
    # detection; only what auto follows may change.
    async def scenario():
        session, _writer = _telnet(sixteen=True)
        apply_color_depth(session, "16")
        session._colorterm = "truecolor"
        session._apply_terminal_colors()
        chosen = session.sixteen_colors, session.detected_sixteen_colors
        apply_color_depth(session, None)
        return chosen, session.sixteen_colors

    assert asyncio.run(scenario()) == ((True, False), False)
