"""Chat command replies as titled blocks: the renderer, `/list`'s counts
from everywhere this node can see, and the hub-reply grouping window."""

from __future__ import annotations

from types import SimpleNamespace

from netbbs.net import chat_flow
from netbbs.net.chat_reply import MAX_BLOCK_WIDTH, block_width, paragraph, table_rows, title_rule
from netbbs.rendering.ansi import strip_ansi
from netbbs.rendering.width import display_width


def test_the_title_rule_spans_the_block_with_the_count_at_its_end():
    rule = strip_ansi(title_rule("Channels", width=40, unicode_style=True, title_color=51, count=3))
    assert rule.startswith("── Channels ─")
    assert rule.endswith(" 3 ──")
    assert display_width(rule) == 40
    ascii_rule = strip_ansi(title_rule("Channels", width=40, unicode_style=False, title_color=51))
    assert ascii_rule.startswith("-- Channels -") and display_width(ascii_rule) == 40
    assert set(ascii_rule) <= set("- Chanels")


def test_a_title_too_long_for_the_terminal_is_cut_not_overflowed():
    rule = strip_ansi(title_rule("x" * 60, width=30, unicode_style=True, title_color=51, right="21:24"))
    assert display_width(rule) <= 30 and rule.endswith(" 21:24 ──")


def test_blocks_stop_at_78_columns_however_wide_the_terminal():
    assert block_width(200) == MAX_BLOCK_WIDTH and block_width(40) == 40


def test_rows_align_their_columns_and_wrap_the_last_under_itself():
    rows = [
        (("#lobby", 220), ("2 here", None), ("General chatter for everyone, " * 4, 248)),
        (("#ops", 220), ("0 here", None), ("", None)),
    ]
    lines = [strip_ansi(line) for line in table_rows(rows, width=50)]
    assert lines[0].startswith(" #lobby  2 here  General")
    start = lines[0].index("General")
    continuation = lines[1]
    assert continuation[:start].strip() == "" and continuation[start] != " "
    assert all(display_width(line) <= 50 for line in lines)
    assert lines[-1] == " #ops    0 here"


def test_a_name_too_wide_for_its_column_stands_alone_and_is_never_cut():
    name = "averyveryverylongnick@averyveryverylongsite"
    rows = [((name, None), ("on MRC", None)), (("bob", None), ("here", None))]
    lines = [strip_ansi(line) for line in table_rows(rows, width=40)]
    assert lines[0] == " " + name
    assert lines[1].strip() == "on MRC" and lines[1].startswith(" " * 3)
    assert lines[2].startswith(" bob")
    assert all(display_width(line) <= 40 for line in lines[1:])



def test_an_overflowing_middle_cell_leaves_no_blank_line_at_40_columns():
    # Review of #1257: a presence cell wider than its capped column flushed
    # the row, and the short-room and empty-description paths then flushed
    # the leftover padding as blank lines.
    rows = [
        (("#chess", 220), ("0 here · 1 on MRC (stale)", None), ("", None)),
        (("#retro", 220), ("0 here · 4 on MRC (3 h ago)", None), ("Old machines", 248)),
    ]
    lines = [strip_ansi(line) for line in table_rows(rows, width=40)]
    assert all(line.strip() for line in lines), lines
    assert any("Old machines" in line for line in lines)
    assert all(display_width(line) <= 40 for line in lines)

def test_prose_wraps_inside_the_block():
    lines = [strip_ansi(line) for line in paragraph("alice, bob, " * 10, width=30)]
    assert len(lines) > 1 and all(line.startswith(" ") and display_width(line) <= 30 for line in lines)


class _Hub:
    def __init__(self, counts):
        self._counts = counts

    def participant_count(self, name):
        return self._counts.get(name, 0)


class _Mrc:
    def __init__(self, bridged, presence, directory=None):
        self._bridged, self._presence, self._directory = bridged, presence, directory or {}

    def is_bridged(self, channel):
        return channel.name in self._bridged

    def room_presence(self, channel):
        return self._presence.get(channel.name, (None, False))

    def mapping_for(self, channel):
        return SimpleNamespace(room=channel.name) if channel.name in self._bridged else None

    def directory_details(self, room):
        return self._directory.get(room)


class _Live:
    def __init__(self, roster):
        self._roster = roster  # channel_id -> [(node, user_id, label)]

    def remote_channel_roster(self, channel_id):
        return list(self._roster.get(channel_id, []))


def _channel(name):
    return SimpleNamespace(name=name, channel_id=f"id-{name}")


def _ctx(*, counts=None, mrc=None, live=None):
    return SimpleNamespace(hub=_Hub(counts or {}), mrc_bridge=mrc, realtime_bridge=live, link_context=None)


def test_list_counts_a_bridged_room_from_its_roster_and_marks_it_stale():
    mrc = _Mrc({"lobby", "chess"}, {"lobby": (3, True), "chess": (1, False)})
    ctx = _ctx(counts={"lobby": 2}, mrc=mrc)
    assert chat_flow._list_presence(ctx, _channel("lobby"), " · ") == "2 here · 3 on MRC"
    assert chat_flow._list_presence(ctx, _channel("chess"), " · ") == "0 here · 1 on MRC (stale)"


def test_list_falls_back_to_the_room_listing_when_nobody_here_is_in_the_room():
    mrc = _Mrc({"retro"}, {}, directory={"retro": (4, "old games", False, 3 * 3600)})
    ctx = _ctx(mrc=mrc)
    assert chat_flow._list_presence(ctx, _channel("retro"), ", ") == "0 here, 4 on MRC (3 h ago)"
    unknown = _ctx(mrc=_Mrc({"retro"}, {}))
    assert chat_flow._list_presence(unknown, _channel("retro"), ", ") == "0 here, ? on MRC"


def test_list_counts_the_callers_on_every_linked_node():
    one = {"id-general": [("ab12cd", "u1", "ann"), ("ab12cd", "u2", "ben")]}
    ctx = _ctx(counts={"general": 1}, live=_Live(one))
    # No peer table here: the node is named by its fingerprint.
    assert chat_flow._list_presence(ctx, _channel("general"), " · ") == "1 here · 2 on ab12cd"
    # Issue #1281: the origin hears from each subscriber, a subscriber from the
    # origin about the others -- several nodes are counted, not listed.
    several = {"id-general": [("ab12cd", "u1", "ann"), ("ef34gh", "u3", "cy"), ("ef34gh", "u4", "di")]}
    ctx = _ctx(counts={"general": 0}, live=_Live(several))
    assert chat_flow._list_presence(ctx, _channel("general"), " · ") == "0 here · 3 on 2 linked BBSes"
    # No roster (not live, or the last caller here left): nothing to count.
    assert chat_flow._list_presence(_ctx(live=_Live({})), _channel("general"), " · ") == "0 here"


def test_a_hub_answer_is_collected_until_the_hub_goes_quiet():
    now = [100.0]
    block = chat_flow._HubReplyBlock(lambda: now[0])
    assert not block.accepts() and block.quiet_in() is None  # nothing asked
    block.expect("/mrc rooms")
    assert block.accepts() and block.quiet_in() is None
    block.add("row 1", "2026-10-12T21:24:00Z")
    now[0] += 0.4
    block.add("row 2", "2026-10-12T21:24:01Z")
    assert block.quiet_in() == chat_flow.HUB_REPLY_QUIET_SECONDS
    assert block.take() == ("/mrc rooms", "2026-10-12T21:24:00Z", ["row 1", "row 2"])
    assert not block.accepts()  # drawn: a later line stands alone


def test_a_question_with_no_answer_in_time_takes_no_later_line():
    now = [0.0]
    block = chat_flow._HubReplyBlock(lambda: now[0])
    block.expect("/mrc motd")
    now[0] += chat_flow.HUB_REPLY_WINDOW_SECONDS + 1
    assert not block.accepts()


def test_a_second_question_keeps_the_first_answer_for_drawing():
    block = chat_flow._HubReplyBlock(lambda: 0.0)
    block.expect("/mrc rooms")
    block.add("lobby", "t1")
    block.expect("/mrc motd")
    assert block.quiet_in() == 0.0
    assert block.take() == ("/mrc rooms", "t1", ["lobby"])
    block.add("welcome", "t2")
    assert block.take() == ("/mrc motd", "t2", ["welcome"])


def test_a_page_counts_the_rows_a_long_hub_line_wraps_onto():
    rows = ["x" * 100, "short", "short", "short"]
    page, rest = chat_flow._take_hub_reply_page(rows, 3, 80)
    assert page == ["x" * 100, "short"] and rest == ["short", "short"]
    assert chat_flow._hub_reply_page_count(rows, 3, 80) == 2
    # A row taller than a whole page still goes out, alone.
    assert chat_flow._take_hub_reply_page(["y" * 500, "z"], 3, 80)[0] == ["y" * 500]
