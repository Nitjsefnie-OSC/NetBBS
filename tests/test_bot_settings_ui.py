"""Bot grants selected through real pickers, with draft-only persistence."""

import asyncio

import pytest

from netbbs.boards.boards import create_board
from netbbs.bots.registry import get_bot, list_bots, save_bot
from netbbs.chat.channels import create_channel
from netbbs.communities import create_community
from netbbs.files.areas import create_file_area
from netbbs.link.channels import link_channel
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.net import bot_flow
from netbbs.net.admin_flow import _load_chrome
from netbbs.net.breadcrumb_preference import set_breadcrumb_collapsed_enabled
from netbbs.net.char_input import EditorKey, EditorKeyKind
from netbbs.net.menu_description_preference import set_menu_description_level
from netbbs.net.redraw_preference import set_redraw_in_place_enabled
from netbbs.rendering import clear_screen, strip_ansi, visible_width
from tests.test_bot_runtime import db, lane, sysop, channel, FakeSession  # noqa: F401


class Screen(FakeSession):
    def __init__(self, keys, *, lines=(), width=80, height=40):
        super().__init__()
        self.keys = iter(keys)
        self.lines = iter(lines)
        self.terminal_width = width
        self.terminal_height = height

    async def read_key(self, **kwargs):
        return next(self.keys)

    async def read_editor_key(self, **kwargs):
        key = next(self.keys)
        if key in ("ENTER", "DOWN", "UP", "ESCAPE"):
            return EditorKey(getattr(EditorKeyKind, key))
        return EditorKey(EditorKeyKind.CHAR, key)

    async def read_line(self, **kwargs):
        return next(self.lines)

    @property
    def plain(self):
        return strip_ansi("".join(self.written))


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("collapsed", [False, True])
def test_catalogue_has_breadcrumb_and_menu_descriptions(db, lane, sysop, populated, collapsed):
    if populated:
        save_bot(db, actor=sysop, name="Baud", channels=[], defaults=[])
    set_menu_description_level(db, sysop, "brief")
    set_breadcrumb_collapsed_enabled(db, sysop, collapsed)
    session = Screen(["b"])
    session.node_display_name = "Reliable Link"
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert "Chat bots" in session.plain
    assert "SysOp" not in session.plain and "Content" not in session.plain
    heading = next(line for line in session.plain.splitlines() if line.strip())
    assert heading.endswith("Chat bots")
    if collapsed:
        assert heading == "Chat bots"
    else:
        assert heading == "Reliable Link \u203a Chat bots"
    assert session.plain.index("Chat bots") < session.plain.index("Bots read addressed")
    assert "Provider, privacy and budgets" in session.plain
    assert all(visible_width(line) <= 80 for line in session.plain.splitlines())


@pytest.mark.parametrize("count", [0, 1, 16])
@pytest.mark.parametrize("width", [40, 80])
@pytest.mark.parametrize("description_level", ["off", "brief"])
def test_catalogue_actions_and_prompt_fit_the_terminal(db, lane, sysop, count, width, description_level):
    for i in range(count):
        save_bot(db, actor=sysop, name=f"Bot{i}", channels=[], defaults=[])
    set_menu_description_level(db, sysop, description_level)
    session = Screen(["b"], width=width, height=24)
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert len(session.plain.splitlines()) <= 24
    assert all(visible_width(line) <= width for line in session.plain.splitlines())


@pytest.mark.parametrize("populated", [False, True])
def test_catalogue_redraw_starts_the_breadcrumb_on_the_first_row(db, lane, sysop, populated):
    if populated:
        save_bot(db, actor=sysop, name="Baud", channels=[], defaults=[])
    set_redraw_in_place_enabled(db, sysop, True)
    set_breadcrumb_collapsed_enabled(db, sysop, False)
    # Check both entry and the return from another screen.
    session = Screen(["m", "b", "b"])
    session.node_display_name = "Reliable Link"
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    frames = "".join(session.written).split(clear_screen())[1:]
    catalogue_frames = [strip_ansi(frame) for frame in frames if "Bots read addressed" in strip_ansi(frame)]
    assert len(catalogue_frames) == 2
    for frame in catalogue_frames:
        assert frame.splitlines()[0] == "Reliable Link \u203a Chat bots"


@pytest.mark.parametrize("collapsed,width", [(True, 80), (False, 40)])
def test_collapsed_preference_reaches_bot_editor_and_resource_picker(db, lane, sysop, collapsed, width):
    create_channel(db, "Lobby", creator=sysop)
    set_breadcrumb_collapsed_enabled(db, sysop, collapsed)
    session = Screen(["c", "0", "3", "b", "b", "b"], width=width)
    session.node_display_name = "A moderately long NetBBS name"
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    lines = session.plain.splitlines()
    assert "Chat bots" in lines
    assert "Create chat bot" in lines
    assert "Allowed channels" in lines
    assert "SysOp" not in session.plain and "Content" not in session.plain
    assert session.plain.index("Allowed channels\r\n") < session.plain.index("Select rows to toggle")


def test_bot_screens_keep_only_node_and_current_location_in_auto_mode(db, lane, sysop):
    create_channel(db, "Lobby", creator=sysop)
    set_breadcrumb_collapsed_enabled(db, sysop, False)
    session = Screen(["c", "0", "3", "b", "b", "m", "b", "b"])
    session.node_display_name = "Reliable Link"
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    lines = session.plain.splitlines()
    for title in ("Chat bots", "Create chat bot", "Allowed channels", "Model conversation"):
        assert f"Reliable Link \u203a {title}" in lines
    assert "SysOp" not in session.plain and "Content" not in session.plain


@pytest.mark.parametrize("save", [False, True])
def test_channel_picker_selects_multiple_and_only_save_persists(db, lane, sysop, channel, save):
    other = create_channel(db, "Zeta", creator=sysop)
    # Field 03, two numbered rows, Back to the draft, then Save or discard.
    session = Screen(["0", "3", "0", "1", "0", "2", "b", *(["s"] if save else ["b", "y"])])

    async def run():
        return await bot_flow.bot_editor(session, lane, sysop, await _load_chrome(lane, sysop))

    result = asyncio.run(run())
    assert "[x]" in session.plain
    if save:
        assert set(result.channels) == {channel.name, other.name}
    else:
        assert result is None and not list_bots(db)
        assert "Discard unsaved changes?" in session.plain


def test_choices_exclude_hidden_mrc_and_inherited_private_sources(db, sysop, channel):
    hidden = create_channel(db, "Hidden", creator=sysop, hidden=True)
    mrc = create_channel(db, "MRC", creator=sysop)
    db.connection.execute("UPDATE channels SET mrc_room='lobby', mrc_paused=1 WHERE id=?", (mrc.id,))
    db.connection.commit()
    linked = create_channel(db, "Linked", creator=sysop)
    identity = bootstrap_node_identity(db)
    link_channel(db, linked, node_identity=identity)
    allowed = bot_flow._resource_names(db, "channels", [])
    assert set(allowed) == {channel.name, linked.name}
    assert hidden.name not in allowed and mrc.name not in allowed
    assert bot_flow._resource_names(db, "linked", allowed) == [linked.name]
    assert bot_flow._resource_names(db, "reading", [channel.name]) == [channel.name]
    assert bot_flow._resource_names(db, "events", []) == []

    community = create_community(db, "Restricted", creator=sysop, default_min_read_level=10)
    board = create_board(db, "Public", creator=sysop)
    area = create_file_area(db, "Public", creator=sysop)
    create_board(db, "Private", creator=sysop, min_read_level=10)
    create_board(db, "Inherited", creator=sysop, community_id=community.id, min_read_level=None)
    create_file_area(db, "Inherited", creator=sysop, community_id=community.id, min_read_level=None)
    assert bot_flow._resource_names(db, "boards", []) == [board.name]
    assert bot_flow._resource_names(db, "files", []) == [area.name]


def test_removing_channel_clears_dependent_draft_grants(db, lane, sysop, channel):
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[channel.name],
                   reading_channels=[channel.name], event_channels=[channel.name],
                   announcement_channel=channel.name)
    session = Screen(["0", "3", "0", "1", "b", "s"])

    async def run():
        return await bot_flow.bot_editor(session, lane, sysop, await _load_chrome(lane, sysop), bot=bot)

    saved = asyncio.run(run())
    assert not saved.channels and not saved.defaults and not saved.reading_channels and not saved.event_channels
    assert saved.announcement_channel is None


def test_announcement_destination_can_be_cleared_or_left_unchanged(db, lane, sysop, channel):
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[],
                   announcement_channel=channel.name)

    async def run():
        chrome = await _load_chrome(lane, sysop)
        # Field 11 is the single destination picker; Back leaves it intact.
        kept = await bot_flow.bot_editor(Screen(["1", "1", "b", "s"]), lane, sysop, chrome, bot=bot)
        assert kept.announcement_channel == channel.name
        cleared = await bot_flow.bot_editor(Screen(["1", "1", "0", "1", "s"]), lane, sysop, chrome, bot=kept)
        assert cleared.announcement_channel is None

    asyncio.run(run())


def test_source_pickers_and_destination_save_resource_names_intact(db, lane, sysop, channel):
    board = create_board(db, "News, events", creator=sysop)
    area = create_file_area(db, "Public files", creator=sysop)
    session = Screen([
        "0", "3", "0", "1", "b",  # allowed channel
        "0", "9", "0", "1", "b",  # board sources
        "1", "0", "0", "1", "b",  # file sources
        "1", "1", "0", "2",       # destination (after the none entry)
        "s",
    ])

    async def run():
        return await bot_flow.bot_editor(session, lane, sysop, await _load_chrome(lane, sysop))

    bot = asyncio.run(run())
    assert bot.board_sources == (board.name,)
    assert bot.file_sources == (area.name,)
    assert bot.announcement_channel == channel.name


def test_stale_grant_can_be_removed_without_regranting_it(db, lane, sysop, channel):
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[])
    db.connection.execute("UPDATE channels SET hidden=1 WHERE id=?", (channel.id,))
    db.connection.commit()
    session = Screen(["0", "3", "0", "1", "0", "1", "b", "s"])

    async def run():
        await bot_flow.bot_editor(session, lane, sysop, await _load_chrome(lane, sysop), bot=bot)

    asyncio.run(run())
    assert not get_bot(db, bot.id).channels
    assert "Unavailable" in session.plain
