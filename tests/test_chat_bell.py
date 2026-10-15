"""Audible chat bells (issue #1302, built in #1333): the per-caller level,
the per-session limiter, mentions, and the bell as its own write after the
line -- never in it, never in a replay, never while the scrollback pager is
open -- on the chat screen of `tests/test_chat_navigation.py`."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from netbbs.auth.users import create_user
from netbbs.chat.bell import (
    BELL_MENTIONS,
    BELL_OFF,
    BELL_PRIVATE,
    SENDER_TABLE_LIMIT,
    BellLimiter,
    bell_level,
    mentions_any,
    set_bell_level,
)
from netbbs.chat.bots import BotNotice
from netbbs.chat.hub import ParticipantId
from netbbs.chat.mailbox import MessageMailbox
from netbbs.chat.nick import set_nick
from netbbs.chat.scrollback import record_message
from netbbs.link.realtime_direct import IncomingDirectMessage
from netbbs.mrc.bridge import MrcNotice
from netbbs.net import chat_flow
from netbbs.net.link_direct import build_direct_message_deliverer
from netbbs.rendering import sanitize_text
from netbbs.timeutil import utc_now_iso
from tests.eventually import eventually
from tests.test_chat_flow_mrc import (  # noqa: F401 -- fixtures
    _QueueSession,
    alice,
    channel,
    db,
    hub,
    lane,
    presence,
    sysop,
)
from tests.test_chat_navigation import _in_chat, _leave, _NavSession

BEL = "\x07"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# -- the level ----------------------------------------------------------------


def test_the_level_defaults_to_private_and_keeps_what_is_set(db, alice):
    assert bell_level(db, alice) == BELL_PRIVATE
    set_bell_level(db, alice, BELL_MENTIONS)
    assert bell_level(db, alice) == BELL_MENTIONS
    set_bell_level(db, alice, BELL_OFF)
    assert bell_level(db, alice) == BELL_OFF
    with pytest.raises(ValueError):
        set_bell_level(db, alice, "loud")


# -- the limiter --------------------------------------------------------------


def test_a_session_rings_three_times_then_once_every_ten_seconds():
    clock = _Clock()
    limiter = BellLimiter(clock)
    assert [limiter.allow(f"s{n}") for n in range(4)] == [True, True, True, False]
    clock.now += 9.9
    assert not limiter.allow("s4")
    clock.now += 0.1
    assert limiter.allow("s4")
    assert not limiter.allow("s5")


def test_one_sender_rings_at_most_once_every_thirty_seconds():
    clock = _Clock()
    limiter = BellLimiter(clock)
    assert limiter.allow("bob")
    clock.now += 29.9
    assert not limiter.allow("bob")
    assert limiter.allow("carol")  # someone else still rings
    clock.now += 0.1
    assert limiter.allow("bob")


def test_the_sender_table_is_bounded():
    clock = _Clock()
    limiter = BellLimiter(clock)
    for n in range(SENDER_TABLE_LIMIT * 3):
        clock.now += 10.0  # a full token each time
        assert limiter.allow(f"sender-{n}")
    assert limiter.tracked_senders == SENDER_TABLE_LIMIT


# -- mentions -----------------------------------------------------------------


def test_a_mention_is_a_whole_word_in_any_case_with_or_without_at():
    names = ("alice", "Ink Well")
    for text in ("hi alice", "ALICE?", "@Alice: look", "ping @alice", "ink well, are you there"):
        assert mentions_any(text, names), text
    for text in ("malice", "alice_b", "alice2", "aliceinwonderland", "inkwell"):
        assert not mentions_any(text, names), text
    assert not mentions_any("alice", (None, "", "  "))


# -- remote text never carries its own BEL ------------------------------------


def test_sanitize_text_strips_bel():
    assert sanitize_text(f"ring{BEL}ring") == "ringring"


def test_remote_lines_render_without_bel(db, channel, alice):
    """MRC, Link channel and bot lines carry no BEL of their own: only the
    bell flag rings, under the caller's level and limiter."""
    mrc = record_message(db, channel, kind="message", author_label="bob@Other", body=f"hi{BEL}", external_source="mrc")
    linked = dataclasses.replace(
        record_message(db, channel, kind="message", author_label="carol@Peer", body=f"yo{BEL}"),
        link_event_json="{}",
    )
    action = record_message(db, channel, kind="action", author_label="bob@Other", body=f"waves{BEL}", external_source="mrc")
    for message in (mrc, linked, action):
        assert BEL not in chat_flow._render_channel_message(db, channel, alice, message)
    assert BEL not in chat_flow._render_mrc_notice(db, alice, MrcNotice(f"bob@Other: psst{BEL}", utc_now_iso(), kind="private"))
    assert BEL not in chat_flow._bot_notice_line(BotNotice(f"Baud.bot: hello{BEL}"))
    assert BEL not in chat_flow._render_direct_chat_message("bob", f"hi{BEL}", self_message=False)


def test_a_link_direct_message_carries_a_ring_flag_and_no_bel(db, lane, hub, presence, channel, alice):
    async def scenario():
        participant = ParticipantId(username="alice", session_key=1)
        queue = hub.join(channel.name, participant)
        presence.enter("alice")
        deliver = build_direct_message_deliverer(
            lane=lane, hub=hub, mailbox=MessageMailbox(), session_registry=None, presence=presence,
        )
        delivered = await deliver(IncomingDirectMessage(
            from_node_fingerprint="f" * 64, from_user_id="bob", from_display_label="Bob", to_user_id="alice",
            body=f"psst{BEL}", created_at=utc_now_iso(),
        ))
        return delivered, queue.get_nowait()

    delivered, notice = asyncio.run(scenario())
    assert delivered
    assert isinstance(notice, chat_flow._TimestampedNotice)
    assert notice.ring == f"link:bob@{'f' * 64}"
    assert BEL not in notice.text


# -- who a channel line rings for ---------------------------------------------


def test_a_mention_rings_only_at_the_mentions_level(db, channel, alice):
    bob = create_user(db, "bob", password="pw", user_level=10)
    line = record_message(db, channel, kind="message", author_label="bob", body="hey @Alice")
    assert chat_flow._mention_bell_sender(db, channel, alice, line) is None  # private, the default
    set_bell_level(db, alice, BELL_MENTIONS)
    assert chat_flow._mention_bell_sender(db, channel, alice, line) == "local:bob"
    # The alias counts too.
    set_nick(db, alice, "Quill")
    aliased = record_message(db, channel, kind="action", author_label="bob", body="nods at quill")
    assert chat_flow._mention_bell_sender(db, channel, alice, aliased) == "local:bob"
    # Not a whole word; the caller's own line; a bot's; a join.
    assert chat_flow._mention_bell_sender(
        db, channel, alice, record_message(db, channel, kind="message", author_label="bob", body="malice"),
    ) is None
    own = record_message(db, channel, kind="message", author_label="alice", body="alice here")
    assert chat_flow._mention_bell_sender(db, channel, alice, own) is None
    assert chat_flow._mention_bell_sender(db, channel, alice, dataclasses.replace(line, author_kind="bot")) is None
    assert chat_flow._mention_bell_sender(
        db, channel, alice, record_message(db, channel, kind="join", author_label="alice"),
    ) is None
    # An MRC line naming the caller through color codes still names them.
    mrc = record_message(db, channel, kind="message", author_label="dan@Far", body="|03alice|07 hi", external_source="mrc")
    assert chat_flow._mention_bell_sender(db, channel, alice, mrc) == "mrc:dan@Far"
    assert bob is not None


# -- the chat screen ----------------------------------------------------------


def _bells(session: _NavSession) -> int:
    return session.written.count(BEL)


def _private(text: str, sender: str = "local:bob") -> chat_flow._TimestampedNotice:
    return chat_flow._TimestampedNotice(f"*** Private message from bob: {text}", utc_now_iso(), ring=sender)


def test_a_private_message_rings_after_its_line_and_never_inside_it(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=24)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            await hub.send_to(channel.name, participant, _private("hello"))
            assert await eventually(lambda: _bells(session) == 1)
            index = session.written.index(BEL)
            assert "hello" in "".join(session.written[:index])
            assert all(BEL not in chunk for chunk in session.written if chunk != BEL)
            assert all(BEL not in line for line in session.line_transcript)
            # A notice without the flag -- daybreak's -- never rings.
            await hub.send_to(channel.name, participant, chat_flow._TimestampedNotice("*** A new day", utc_now_iso()))
            assert await eventually(lambda: "A new day" in session.text())
            await asyncio.sleep(0.1)
            assert _bells(session) == 1
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_off_silences_the_bell_and_away_does_not(db, lane, hub, presence, channel, alice):
    async def scenario(level: str, away: bool) -> int:
        set_bell_level(db, alice, level)
        session = _NavSession(height=24)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            if away:
                session.inputs.put_nowait("/away lunch")
                assert await eventually(lambda: "marked away" in session.text())
            await hub.send_to(channel.name, participant, _private("ping"))
            await hub.send_to(channel.name, participant, MrcNotice(
                "dan@Far: psst", utc_now_iso(), kind="private", ring="mrc:dan@Far",
            ))
            assert await eventually(lambda: "psst" in session.text())
            await asyncio.sleep(0.1)
            return _bells(session)
        finally:
            await _leave(session, task)

    assert asyncio.run(scenario(BELL_OFF, False)) == 0
    assert asyncio.run(scenario(BELL_PRIVATE, True)) == 2


def test_an_mrc_private_line_rings_by_its_flag_and_a_drop_notice_does_not(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=24)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            await hub.send_to(channel.name, participant, MrcNotice(
                "(private MRC messages for you arrived faster than can be shown -- some were dropped)",
                utc_now_iso(), kind="private",
            ))
            assert await eventually(lambda: "some were dropped" in session.text())
            await asyncio.sleep(0.1)
            assert _bells(session) == 0
            await hub.send_to(channel.name, participant, MrcNotice(
                "dan@Far: psst", utc_now_iso(), kind="private", ring="mrc:dan@Far",
            ))
            assert await eventually(lambda: _bells(session) == 1)
            assert all(BEL not in chunk for chunk in session.written if chunk != BEL)
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_the_bell_is_rate_limited_per_session_and_per_sender(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=24)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            await hub.send_to(channel.name, participant, _private("one"))
            await hub.send_to(channel.name, participant, _private("two"))  # same sender
            for n in range(4):
                await hub.send_to(channel.name, participant, _private(f"from {n}", sender=f"local:u{n}"))
            assert await eventually(lambda: "from 3" in session.text())
            await asyncio.sleep(0.1)
            return _bells(session)
        finally:
            await _leave(session, task)

    # bob once, then two more senders until the bucket of three is empty.
    assert asyncio.run(scenario()) == 3


def test_no_bell_while_the_scrollback_pager_is_open(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=12)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            for n in range(30):
                await hub.send_to(channel.name, participant, MrcNotice(f"line {n:02}", utc_now_iso()))
            assert await eventually(lambda: "line 29" in session.text())
            await session.navigate("PAGE_UP")
            mark = len(session.written)
            await hub.send_to(channel.name, participant, _private("while reading"))
            assert await eventually(lambda: "1 new" in session.since(mark))
            await asyncio.sleep(0.1)
            assert _bells(session) == 0
            await session.navigate("ESCAPE")
            assert "while reading" in session.since(mark)
            assert _bells(session) == 0  # replayed lines never ring
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_a_line_naming_the_caller_rings_at_mentions_and_their_own_never(db, lane, hub, presence, channel, alice):
    bob = create_user(db, "bob", password="pw", user_level=10)
    set_bell_level(db, alice, BELL_MENTIONS)
    set_bell_level(db, bob, BELL_MENTIONS)

    async def scenario():
        alice_session = _NavSession(height=24)
        alice_task, _ = await _in_chat(lane, hub, presence, channel, alice, alice_session)
        bob_session = _NavSession(height=24)
        bob_task, _ = await _in_chat(lane, hub, presence, channel, bob, bob_session)
        try:
            bob_session.inputs.put_nowait("anyone seen malice?")
            assert await eventually(lambda: "malice" in alice_session.text())
            await asyncio.sleep(0.1)
            assert _bells(alice_session) == 0
            bob_session.inputs.put_nowait("@Alice are you there")
            assert await eventually(lambda: _bells(alice_session) == 1)
            # Bob's own line naming himself never rings him.
            bob_session.inputs.put_nowait("bob is back")
            assert await eventually(lambda: "bob is back" in alice_session.text())
            await asyncio.sleep(0.1)
            assert _bells(bob_session) == 0
        finally:
            await _leave(bob_session, bob_task)
            await _leave(alice_session, alice_task)

    asyncio.run(scenario())


def test_bell_command_shows_and_sets_the_level(db, lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=24)
        task, _ = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            session.inputs.put_nowait("/bell")
            assert await eventually(lambda: "Chat bell: private (private messages)" in session.text())
            session.inputs.put_nowait("/bell MENTIONS")
            assert await eventually(lambda: "Chat bell is now mentions" in session.text())
            session.inputs.put_nowait("/bell loud")
            assert await eventually(lambda: "Usage: /bell [off|private|mentions]" in session.text())
        finally:
            await _leave(session, task)

    asyncio.run(scenario())
    assert bell_level(db, alice) == BELL_MENTIONS
    assert "bell" in chat_flow._COMMAND_INFO and chat_flow._COMMANDS["bell"] is chat_flow._handle_bell
