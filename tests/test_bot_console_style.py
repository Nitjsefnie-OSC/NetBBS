"""The chat bot screens follow the console's presentation rules (issue #1241)."""

import asyncio
import time
from dataclasses import replace

import pytest

from netbbs.bots.model import ModelSettings, validate_settings
from netbbs.bots.registry import BotError, save_bot
from netbbs.chat.bots import BotNotice
from netbbs.chat.channels import create_channel
from netbbs.link.channels import link_channel
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.net import bot_flow, bot_model_flow
from netbbs.net.admin_ui import _load_chrome
from netbbs.net.chat_flow import _bot_notice_line
from netbbs.rendering import LINKED_COLOR, MUTED_COLOR, WARNING_COLOR, colored, fg
from tests.test_bot_runtime import db, lane, sysop, channel  # noqa: F401
from tests.test_bot_settings_ui import Screen


def _row(plain, label):
    return next(line for line in plain.splitlines() if label in line and "." in line[:6])


def test_catalogue_rows_are_a_table_with_state_in_its_colour(db, lane, sysop, channel):
    save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[])
    save_bot(db, actor=sysop, name="Waiting", channels=[channel.name], defaults=[], enabled=True)
    session = Screen(["b"])
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert "STATE" in session.plain and "CHANNELS" in session.plain
    # One fact per column; the state is not repeated in a sentence.
    assert _row(session.plain, "Baud.bot").split()[1:] == ["Baud.bot", "off", "1"]
    # Enabled, but no runtime has started it: a warning, not a muted "off".
    written = "".join(session.written)
    assert fg(WARNING_COLOR) in written.split("Waiting.bot", 1)[1].split("\n", 1)[0]
    assert "last runtime status" not in session.plain


@pytest.mark.parametrize("keys, lines, outcome", [
    (["k", "b"], ["01"], "Baud.bot stopped. It stays off until you enable it again."),
    (["s", "b"], [], "Every bot stopped."),
    (["d", "y", "b"], ["01"], "Baud.bot and its state deleted."),
    (["d", "n", "b"], ["01"], "Cancelled. Nothing was deleted."),
])
def test_catalogue_actions_announce_their_outcome(db, lane, sysop, keys, lines, outcome):
    save_bot(db, actor=sysop, name="Baud", channels=[], defaults=[])
    session = Screen(keys, lines=lines)
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert outcome in session.plain


def test_delete_refusal_names_the_key_to_press(db, lane, sysop, channel):
    save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[], enabled=True)
    session = Screen(["d", "b"], lines=["01"])
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert "Stop this bot with [K]ill selected" in session.plain


def test_saving_the_editor_announces_it(db, lane, sysop):
    session = Screen(["c", "s", "b", "b"])
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    assert "Baud.bot saved." in session.plain


def test_editor_is_sectioned_and_fits_an_80x24_terminal(db, lane, sysop):
    save_bot(db, actor=sysop, name="Baud", channels=[], defaults=[])
    session = Screen(["0", "1", "b", "b"], height=24)
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    editor = session.plain.split("Edit chat bot", 1)[1]
    assert "BOT" in editor and "01 Name:" in editor and "02 Enabled:" in editor
    # A field keeps its number; sections only page the form.
    assert "19 Program arguments" not in editor.split("Choice:")[0]
    assert "Enabled:                    no" in editor


def test_linked_channels_are_marked_in_the_bot_channel_picker(db, lane, sysop, channel):
    linked = create_channel(db, "linked-room", creator=sysop)
    link_channel(db, linked, node_identity=bootstrap_node_identity(db))

    async def run():
        chrome = await _load_chrome(lane, sysop)
        session = Screen(["0", "3", "b", "b", "b", "y"])
        await bot_flow.bot_editor(session, lane, sysop, chrome)
        return "".join(session.written)
    written = asyncio.run(run())
    assert fg(LINKED_COLOR) + "linked-room" in written
    assert fg(LINKED_COLOR) + channel.name not in written


def test_usage_is_a_titled_screen_in_sysop_wording(db, lane, sysop):
    now = time.time()
    db.connection.execute("INSERT INTO bot_model_usage(at, caller, channel_id, tokens, cost_micros, state) "
                          "VALUES (?, ?, 1, 1500, 250000, 'reported')", (now, str(sysop.id)))
    db.connection.execute("INSERT INTO bot_model_limits VALUES ('your daily model quota', 3, ?)", (now,))
    db.connection.commit()
    # Callers and limits page on to a second page at 80x24.
    session = Screen([">", "b"], height=24)
    asyncio.run(bot_model_flow.model_usage(session, lane, sysop))
    plain = session.plain
    assert plain.lstrip().splitlines()[0].endswith("Model usage")
    assert "TODAY (UTC)" in plain and "CALLERS THIS MONTH" in plain and "LIMITS REACHED" in plain
    assert "caller daily quota" in plain and "your daily" not in plain
    assert "1,500" in plain and "0.25" in plain and "0.000000" not in plain


def test_model_settings_errors_name_the_field_the_sysop_sees():
    with pytest.raises(BotError, match=r"^Node daily token budget must be 1-1,000,000,000\.$"):
        validate_settings(replace(ModelSettings(), day_tokens=0))
    # Money is stored in millionths and shown in the SysOp's currency units.
    with pytest.raises(BotError, match=r"^Price per million input tokens must be 0-1,000\.$"):
        validate_settings(replace(ModelSettings(), input_price=2000000000))
    with pytest.raises(BotError, match="^System prompt must"):
        validate_settings(replace(ModelSettings(), persona=""))


def test_bot_notices_read_as_notices_not_as_the_bot_speaking():
    private = _bot_notice_line(BotNotice("Baud.bot: The bot did not reply within 60 seconds.", private=True))
    assert private == colored("(Baud.bot: The bot did not reply within 60 seconds.)", fg_color=MUTED_COLOR)
    public = _bot_notice_line(BotNotice("Baud.bot reads this channel."))
    assert public.startswith(colored("*** ", fg_color=MUTED_COLOR) + colored("[BOT] ", fg_color=MUTED_COLOR, bold=True))
    assert BotNotice("x") == "x" and not BotNotice("x").private


def test_who_states_presence_events_only_for_bots_that_receive_them(db, sysop, channel):
    from netbbs.net.chat_flow import _bot_presence_readers
    save_bot(db, actor=sysop, name="Quiet", channels=[channel.name], defaults=[])
    save_bot(db, actor=sysop, name="Greeter", channels=[channel.name], defaults=[], event_channels=[channel.name])
    assert _bot_presence_readers(db, channel) == {"Greeter.bot"}


def test_a_failing_bot_shows_why_in_its_catalogue_row(db, lane, sysop, channel):
    from netbbs.bots.registry import set_runtime_status
    from netbbs.rendering import ERROR_COLOR
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[], enabled=True)
    set_runtime_status(db, bot.id, "restarting", "Bot closed its output pipe.")
    session = Screen(["b"], width=80, height=24)
    asyncio.run(bot_flow.bot_menu(session, lane, sysop))
    # A table drops row descriptions, so the reason must be a cell of the row.
    assert "LAST ERROR" in session.plain
    row = _row(session.plain, "Baud.bot")
    assert "restarting" in row and "Bot closed its" in row
    assert fg(ERROR_COLOR) + "Bot closed its" in "".join(session.written)
