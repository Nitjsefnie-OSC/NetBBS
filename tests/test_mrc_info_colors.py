"""The hub's answer to `/mrc info`: its `Label: value` rows drawn like the
rows of our own blocks, everything else left as the hub draws it."""

from __future__ import annotations

from types import SimpleNamespace

from netbbs.net import chat_flow
from netbbs.rendering.ansi import fg, strip_ansi
from netbbs.rendering.theme import HEADER_COLOR, LABEL_COLOR, MRC_SITE_COLOR, MUTED_COLOR, VALUE_COLOR


def test_a_label_and_value_row_is_coloured_and_keeps_its_alignment():
    row = chat_flow._info_row("*.:  BBS name: Reliable Link")
    assert strip_ansi(row) == "*.:  BBS name: Reliable Link"
    assert fg(LABEL_COLOR) + "BBS name:" in row and fg(VALUE_COLOR) + "Reliable Link" in row


def test_a_slash_separated_value_gets_one_colour_per_part():
    for text in ("*.:  Platform: NETBBS / NetBSD.x86_64 / 7.22.0", "*.:   Version: NETBBS/NetBSD.x86_64/7.22.0"):
        row = chat_flow._info_row(text)
        assert strip_ansi(row) == text
        assert fg(HEADER_COLOR) + "NETBBS" in row and fg(MRC_SITE_COLOR) + "NetBSD.x86_64" in row
        assert fg(MUTED_COLOR) + ("/" if "/N" in text else " / ") in row


def test_a_url_is_one_value_not_parts():
    row = chat_flow._info_row("*.:       Web: https://ReLink.NetBBS.org/")
    assert fg(VALUE_COLOR) + "https://ReLink.NetBBS.org/" in row


def test_the_frame_and_any_other_shape_keep_the_hubs_look():
    for text in ("*. ______________ /info __", "*.:__", "", "nothing like a label here"):
        assert chat_flow._info_row(text) is None


def test_only_the_answer_to_mrc_info_is_redrawn(monkeypatch):
    monkeypatch.setattr(chat_flow, "mrc_colors_enabled", lambda db, viewer: True)
    notice = SimpleNamespace(text="|11*.:  |07Sysop: |15Thiesi")
    ours = chat_flow._render_hub_reply_row(None, None, notice, "/mrc info 83")
    hubs = chat_flow._render_hub_reply_row(None, None, notice, "/mrc motd")
    assert strip_ansi(ours) == strip_ansi(hubs) == " *.:  Sysop: Thiesi"
    assert fg(LABEL_COLOR) + "Sysop:" in ours and fg(LABEL_COLOR) + "Sysop:" not in hubs
