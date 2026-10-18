"""SysOp-edited Baud phrases (issue #1381): validated on Save, stored per
instance, passed in the hello, and falling back to the temperament's preset."""

import asyncio
import json

import pytest

from netbbs.bots.baud import Baud, PRESETS
from netbbs.bots.botkit import MAX_CHANGES
from netbbs.bots.registry import BotError, get_bot, save_bot, validate_phrases
from netbbs.bots.runtime import BotRuntime
from netbbs.chat.hub import ParticipantId
from netbbs.chat.scrollback import record_message
from netbbs.net import bot_flow
from netbbs.net.admin_ui import _load_chrome
from tests.test_bot_runtime import db, lane, hub, sysop, alice, channel, eventually, install, next_line  # noqa: F401
from tests.test_bot_settings_ui import Screen


def message(text, name="alice"):
    return {"type": "message", "request": "r", "caller": {"id": 7, "name": name}, "channel": "general", "text": text}


def join(name="alice"):
    return {"type": "presence", "kind": "join", "channel": "general", "caller": {"id": 7, "name": name}}


@pytest.mark.parametrize("temperament", ["friendly", "terse", "retro"])
def test_baud_without_overrides_keeps_each_temperaments_presets(temperament):
    sent = []
    baud = Baud({"temperament": temperament, "greetings": True}, sent.append)
    baud.handle(join())
    assert sent[-1]["text"] == PRESETS["greeting"][temperament].format(name="alice")
    baud.handle(message("help"))
    opening = PRESETS["help"][temperament]
    assert sent[-1]["text"].startswith("alice: " + (opening + " help;" if opening else "help;"))


def test_baud_says_the_sysops_phrases_and_falls_back_for_blank_ones():
    sent = []
    phrases = {"greeting": "Willkommen, {name}!", "help": "Hallo {name}.", "busy": "",
               "limit": "Genug, {name}: {{8}} Erinnerungen."}
    baud = Baud({"temperament": "retro", "greetings": True, "phrases": phrases}, sent.append)
    baud.handle(join())
    assert sent[-1]["text"] == "Willkommen, alice!"
    baud.handle(message("help"))
    assert sent[-1]["text"].startswith("alice: Hallo alice. help;")
    baud.state["reminders"] = [{"id": i, "user": 7, "name": "alice", "channel": "general", "due": 1e12, "text": "x"}
                               for i in range(8)]
    baud.handle(message("remind 1h more"))
    assert sent[-1]["text"] == "alice: Genug, alice: {8} Erinnerungen."
    # Busy was left blank: the preset answers. Reminder commands wait for a
    # save in flight (botkit's change queue); one past its bound is busy.
    baud.store(baud.state)
    for _ in range(MAX_CHANGES):
        baud.handle(message("reminders"))
    baud.handle(message("reminders"))
    assert sent[-1]["text"] == "alice: " + PRESETS["busy"]["retro"]


def test_baud_survives_a_phrase_the_host_would_have_refused():
    sent = []
    baud = Baud({"greetings": True, "phrases": {"greeting": "Hi {user} {"}}, sent.append)
    baud.handle(join())
    assert sent[-1]["text"] == "Welcome, alice!"


def test_terse_help_opening_override_is_used_even_though_the_preset_is_empty():
    sent = []
    baud = Baud({"temperament": "terse", "phrases": {"help": "Commands:"}}, sent.append)
    baud.handle(message("help"))
    assert sent[-1]["text"].startswith("alice: Commands: help;")


@pytest.mark.parametrize("text, problem", [
    ("Hi {user}", "only placeholder is {name}"),
    ("Hi {0}", "only placeholder is {name}"),
    ("Hi {}", "only placeholder is {name}"),
    ("Hi {name!r}", "only placeholder is {name}"),
    ("Hi {name:>40}", "only placeholder is {name}"),
    ("Hi {name.upper}", "only placeholder is {name}"),
    ("Hi {name", "unmatched brace"),
    ("Hi }", "unmatched brace"),
    ("Hi\x1b[2J there", "plain text"),
    ("x" * 401, "400 characters"),
    ("{name}" * 13, "400 characters"),
])
def test_save_refuses_a_phrase_baud_could_not_say(db, sysop, channel, text, problem):
    with pytest.raises(BotError, match=problem.replace("{", r"\{").replace("}", r"\}")):
        save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[], phrases={"greeting": text})
    assert not db.connection.execute("SELECT 1 FROM chat_bots").fetchone()


def test_the_longest_help_opening_never_cuts_the_command_list():
    # Review of #1399: the opening shares its line with the fixed help text.
    sent = []
    longest = "x" * 168 + "{name}"  # 200 with a 32-character name
    assert validate_phrases({"help": longest}) == {"help": longest}
    with pytest.raises(BotError, match="200 characters"):
        validate_phrases({"help": longest + "x"})
    baud = Baud({"queries": ["stats", "seen"], "model_conversation": True, "phrases": {"help": longest}}, sent.append)
    baud.handle(message("help", name="n" * 32))
    assert len(sent[-1]["text"]) <= 500
    assert sent[-1]["text"].endswith("reset clears your temporary context here. Replies are public.")


def test_baud_survives_a_phrase_that_raises_type_error():
    sent = []
    baud = Baud({"greetings": True, "phrases": {"greeting": "Hi {name[a]}"}}, sent.append)
    baud.handle(join())
    assert sent[-1]["text"] == "Welcome, alice!"


def test_validation_keeps_overrides_only_and_allows_literal_braces():
    assert validate_phrases({"greeting": "  ", "help": " {{help}} for {name} ", "busy": "", "limit": "x" * 400}) == {
        "help": "{{help}} for {name}", "limit": "x" * 400}
    with pytest.raises(BotError, match="placeholder"):
        validate_phrases({"greeting": "Hi {name[a]}"})
    with pytest.raises(BotError, match="Unknown bot phrase"):
        validate_phrases({"farewell": "Bye"})


def test_phrases_are_stored_per_instance_and_cleared_by_blanking(db, sysop, channel):
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[],
                   phrases={"greeting": "Moin, {name}!"})
    other = save_bot(db, actor=sysop, name="Other", channels=[channel.name], defaults=[])
    assert get_bot(db, bot.id).phrase_overrides == {"greeting": "Moin, {name}!"}
    assert get_bot(db, other.id).phrase_overrides == {}
    cleared = save_bot(db, actor=sysop, bot_id=bot.id, name="Baud", channels=[channel.name], defaults=[],
                       phrases={"greeting": ""})
    assert cleared.phrase_overrides == {}
    assert json.loads(db.connection.execute("SELECT phrases_json FROM chat_bots WHERE id=?", (bot.id,)).fetchone()[0]) == {}


def test_editor_shows_presets_saves_an_override_and_keeps_the_draft_on_a_bad_one(db, lane, sysop, channel):
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[], temperament="retro")
    # Field 20 is the greeting; a bad placeholder is refused on Save with the
    # draft kept, then fixed and saved.
    session = Screen(["2", "0", "s", "2", "0", "s"],
                     lines=["Hi {user}", "Hi {name}"], height=60)

    async def fake_read_field_line(session_, *, initial="", echo=True):
        return next(session.lines)

    async def run():
        import netbbs.net.resource_editor as editor
        original = editor.read_field_line
        editor.read_field_line = fake_read_field_line
        try:
            return await bot_flow.bot_editor(session, lane, sysop, await _load_chrome(lane, sysop), bot=bot)
        finally:
            editor.read_field_line = original

    saved = asyncio.run(run())
    assert "(preset) Carrier detected: welcome, {name}!" in session.plain
    assert "the only placeholder is {name}" in session.plain
    assert saved.phrase_overrides == {"greeting": "Hi {name}"}


def test_real_baud_receives_phrases_in_its_hello(db, lane, hub, sysop, alice, channel):
    bot = install(db, sysop, channel, event_channels=[channel.name], greetings=True,
                  phrases={"greeting": "Carrier up, {name}.", "help": "Befehle:"})

    async def run():
        pid = ParticipantId(alice.username, 1)
        queue = hub.join(channel.name, pid)
        runtime = BotRuntime(lane, hub, poll_seconds=.03)
        runtime.start()
        try:
            await eventually(lambda: bot.id in runtime.running_ids)
            joined = record_message(db, channel, kind="join", author_label="alice")
            await runtime.observe(channel, joined, caller={"id": alice.id, "name": "alice"})
            assert (await next_line(queue, "Carrier up")).body == "Carrier up, alice."
            sent = record_message(db, channel, kind="message", author_label=alice.username, body="!help")
            assert await runtime.addressed(channel, alice, sent, pid) is None
            # Issue #1373: the help is a titled block, the opening its first row.
            answer = await next_line(queue, "Befehle:")
            assert answer.body.startswith("Commands: Befehle:; help this list;")
            assert answer.block.rows[:2] == (("", "Befehle:"), ("help", "this list"))
        finally:
            await runtime.close()
            hub.leave(channel.name, pid)

    asyncio.run(run())
