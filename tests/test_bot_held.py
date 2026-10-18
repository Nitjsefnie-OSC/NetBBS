"""Host-held bot lines and tells (issues #1375, #1376) at real SQLite, hub and runtime boundaries."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from types import SimpleNamespace

import pytest

from netbbs.auth.renames import rename_user
from netbbs.auth.users import create_user
from netbbs.bots import held
from netbbs.bots.registry import BotError, save_bot
from netbbs.bots.runtime import BotRuntime, _Worker
from netbbs.chat.channels import create_channel
from netbbs.chat.hub import ParticipantId
from netbbs.chat.scrollback import get_scrollback
from netbbs.mail import block_link_sender, block_local_sender, unblock_local_sender
from netbbs.messaging_preferences import set_accepts_direct_messages
from netbbs.timeutil import utc_iso
from tests.test_chat_bots import sysop, alice, channel  # noqa: F401
from tests.test_chat_flow_moderation import FakeSession, db, lane, hub, presence, mailbox, history  # noqa: F401

WEEK = 7 * 24 * 3600


def install(db, sysop, channel, **kwargs):
    return save_bot(db, actor=sysop, name="Baud", channels=[channel.name], defaults=[channel.name],
                    enabled=True, **kwargs)


@pytest.fixture
def nib(db):
    return create_user(db, "nib", password="hunter2", user_level=10)


def bot_lines(db, channel):
    return [m.body for m in get_scrollback(db, channel) if m.author_kind == "bot"]


def held_rows(db):
    return db.connection.execute("SELECT * FROM bot_held_lines ORDER BY id").fetchall()


def expire_all(db):
    past = utc_iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
    db.connection.execute("UPDATE bot_held_lines SET expires_at = ?", (past,))
    db.connection.commit()


class Harness:
    """A runtime with one ready worker and no process: the worker's events
    queue is what the bot would read."""

    def __init__(self, lane, hub, bot, link=None):
        self.runtime = BotRuntime(lane, hub, poll_seconds=.02)
        self.runtime.link = link
        self.worker = _Worker(self.runtime, bot)
        self.worker.ready = True
        self.runtime.workers[bot.id] = self.worker
        self.runtime.delivery_task = None

    async def __aenter__(self):
        self.runtime.delivery_task = asyncio.create_task(self.runtime._deliveries())
        return self

    async def __aexit__(self, *exc):
        self.runtime.delivery_task.cancel()
        await asyncio.gather(self.runtime.delivery_task, return_exceptions=True)

    async def act(self, **value):
        return await self.worker._action(value)

    def request(self, channel, caller):
        token = f"req-{len(self.worker.pending)}"
        self.worker.pending[token] = (channel.name, None, time.monotonic() + 60, None, dict(caller), "")
        return token

    async def event(self, kind="held", timeout=5):
        async with asyncio.timeout(timeout):
            while True:
                event = await self.worker.events.get()
                if event["type"] == kind:
                    return event

    async def settle(self):
        async with asyncio.timeout(5):
            while not self.runtime.joins.empty():
                await asyncio.sleep(.01)
        await asyncio.sleep(.1)


def test_say_for_a_present_caller_publishes_at_once(db, lane, hub, sysop, alice, channel):
    bot = install(db, sysop, channel)

    async def run():
        hub.join(channel.name, ParticipantId(alice.username, 1))
        async with Harness(lane, hub, bot) as h:
            result = await h.act(action="say.for", channel=channel.name, caller=alice.id,
                                 text="alice: reminder #1: tea", held_text="alice: held", expires=WEEK)
            assert result["delivered"] and result["message_id"]
        assert bot_lines(db, channel) == ["alice: reminder #1: tea"]
        assert held_rows(db) == []

    asyncio.run(run())


def test_held_line_waits_for_the_next_join_and_two_sessions_get_one(db, lane, hub, sysop, alice, channel):
    other = create_channel(db, "other", creator=sysop)
    bot = save_bot(db, actor=sysop, name="Baud", channels=[channel.name, other.name], defaults=[], enabled=True)

    async def run():
        async with Harness(lane, hub, bot) as h:
            result = await h.act(action="say.for", channel=channel.name, caller=alice.id,
                                 text="now", held_text="alice: reminder #1 (held): tea", expires=WEEK)
            assert result["held"] and isinstance(result["held_id"], int)
            assert (await h.act(action="held.list"))[0]["caller"] == alice.id
            # A join elsewhere is not this channel: text never crosses channels.
            h.runtime.joined(other, alice.id)
            await h.settle()
            assert bot_lines(db, channel) == [] and len(held_rows(db)) == 1
            # Two sessions of one account join at once: one delivery.
            hub.join(channel.name, ParticipantId(alice.username, 1))
            hub.join(channel.name, ParticipantId(alice.username, 2))
            h.runtime.joined(channel, alice.id)
            h.runtime.joined(channel, alice.id)
            event = await h.event()
            assert event == {"type": "held", "held_id": result["held_id"], "outcome": "delivered",
                             "message_id": event["message_id"]}
            await h.settle()
        assert bot_lines(db, channel) == ["alice: reminder #1 (held): tea"]
        assert held_rows(db) == []

    asyncio.run(run())


def test_held_lines_survive_a_runtime_restart(db, lane, hub, sysop, alice, channel):
    bot = install(db, sysop, channel)

    async def run():
        async with Harness(lane, hub, bot) as h:
            await h.act(action="say.for", channel=channel.name, caller=alice.id, text="x",
                        held_text="alice: after restart", expires=WEEK)
        # A new runtime (a node restart) knows nothing but the table.
        async with Harness(lane, hub, bot) as h:
            hub.join(channel.name, ParticipantId(alice.username, 1))
            h.runtime.joined(channel, alice.id)
            assert (await h.event())["outcome"] == "delivered"
        assert bot_lines(db, channel) == ["alice: after restart"]

    asyncio.run(run())


def test_refused_publication_stays_held_and_expiry_drops_silently(db, lane, hub, sysop, alice, channel):
    bot = install(db, sysop, channel, lines_per_hour=1)

    async def run():
        async with Harness(lane, hub, bot) as h:
            first = await h.act(action="say.for", channel=channel.name, caller=alice.id, text="x",
                                held_text="alice: one", expires=WEEK)
            await h.act(action="say", channel=channel.name, text="uses the hour")
            hub.join(channel.name, ParticipantId(alice.username, 1))
            h.runtime.joined(channel, alice.id)
            await h.settle()
            # Over the hourly limit: refused, so still held.
            assert [r["id"] for r in held_rows(db)] == [first["held_id"]]
            expire_all(db)
            dropped = await lane.run(held.prune_expired)
            assert dropped == [(bot.id, first["held_id"], "line")]
        assert bot_lines(db, channel) == ["uses the hour"]
        assert held_rows(db) == []

    asyncio.run(run())


def test_expired_line_on_join_is_dropped_and_the_bot_told(db, lane, hub, sysop, alice, channel):
    bot = install(db, sysop, channel)

    async def run():
        async with Harness(lane, hub, bot) as h:
            first = await h.act(action="say.for", channel=channel.name, caller=alice.id, text="x",
                                held_text="alice: stale", expires=WEEK)
            # A row found on join after its expiry, before pruning reached
            # it, is dropped at delivery the same way.
            expire_all(db)
            await h.runtime._deliver(channel, first["held_id"], bot.id, "line")
            assert (await h.event())["outcome"] == "expired"
        assert bot_lines(db, channel) == [] and held_rows(db) == []

    asyncio.run(run())


def test_line_bounds_and_cancel(db, sysop, alice, channel):
    bot = install(db, sysop, channel)
    with pytest.raises(BotError, match="60 seconds to 7 days"):
        held.hold_line(db, bot.id, channel.name, alice.id, "x", WEEK + 1)
    with pytest.raises(BotError, match="caller id"):
        held.hold_line(db, bot.id, channel.name, "alice", "x", WEEK)
    with pytest.raises(BotError, match="plain text"):
        held.hold_line(db, bot.id, channel.name, alice.id, "a\x1b[2Jb", WEEK)
    ids = [held.hold_line(db, bot.id, channel.name, alice.id, f"n{i}", WEEK)["held_id"]
           for i in range(held.LINES_PER_CALLER)]
    with pytest.raises(BotError, match="for that caller"):
        held.hold_line(db, bot.id, channel.name, alice.id, "one more", WEEK)
    # A Link caller is a separate caller.
    held.hold_line(db, bot.id, channel.name, "link:abc123:bob", "for bob", WEEK)
    assert held.cancel_line(db, bot.id, ids[0]) == {"cancelled": True}
    assert held.cancel_line(db, bot.id, ids[0]) == {"cancelled": False}
    assert len(held.list_lines(db, bot.id)) == held.LINES_PER_CALLER


def test_linked_caller_line_waits_for_their_linked_join(db, lane, hub, sysop, channel):
    bot = install(db, sysop, channel)
    roster = []
    link = SimpleNamespace(node=SimpleNamespace(identity=None),
                           bridge=SimpleNamespace(remote_channel_roster=lambda cid: list(roster)))
    caller = "link:abc123:bob"

    async def broadcast_local_message_live(channel, message):
        pass

    link.bridge.broadcast_local_message_live = broadcast_local_message_live

    async def run():
        async with Harness(lane, hub, bot, link=link) as h:
            result = await h.act(action="say.for", channel=channel.name, caller=caller, text="x",
                                 held_text="bob@Elsewhere: held", expires=WEEK)
            assert result["held"]
            roster.append(("abc123", "bob", "bob@Elsewhere"))
            assert await h.runtime.present(channel, caller)
            h.runtime.joined(channel, caller)
            assert (await h.event())["outcome"] == "delivered"
        assert bot_lines(db, channel) == ["bob@Elsewhere: held"]

    asyncio.run(run())


def test_real_chat_join_delivers_the_held_line(db, lane, hub, presence, mailbox, history, sysop, alice, channel):
    from netbbs.net import chat_flow
    bot = install(db, sysop, channel)

    class InteractiveSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.input = asyncio.Queue()

        async def read_line(self, **kwargs):
            return await self.input.get()

    async def run():
        async with Harness(lane, hub, bot) as h:
            hub.bot_runtime = h.runtime
            await h.act(action="say.for", channel=channel.name, caller=alice.id, text="x",
                        held_text="alice: reminder #1 (due 18:00, held until you came back): tea", expires=WEEK)
            session = InteractiveSession()
            task = asyncio.create_task(chat_flow._chat_loop(session, lane, hub, presence, mailbox, history,
                                                            channel, alice))
            try:
                # The line is wrapped at the terminal width.
                async with asyncio.timeout(10):
                    while "held until you came" not in "".join(session.written):
                        await asyncio.sleep(.02)
                session.input.put_nowait("/quit")
                await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                hub.bot_runtime = None
        assert held_rows(db) == []

    asyncio.run(run())


# -- tells -------------------------------------------------------------------


def test_tell_is_bound_to_the_request_and_delivered_on_join(db, lane, hub, sysop, alice, nib, channel):
    bot = install(db, sysop, channel)

    async def run():
        async with Harness(lane, hub, bot) as h:
            token = h.request(channel, {"id": alice.id, "name": alice.username})
            # The bot cannot name a sender: only the token's caller counts.
            made = await h.act(action="tell", request=token, to="nib", text="check the Retro board",
                               sender={"id": sysop.id})
            assert made["held"] and made["name"] == "nib"
            row = held_rows(db)[0]
            assert (row["sender_user_id"], row["caller_user_id"], row["kind"]) == (alice.id, nib.id, "tell")
            listed = await h.act(action="tell.list", request=token)
            assert [(t["to"], t["text"]) for t in listed] == [("nib", "check the Retro board")]
            # Renamed between the tell and its delivery: keyed by account.
            rename_user(db, nib, "nibbles", renamed_by=sysop)
            hub.join(channel.name, ParticipantId("nibbles", 1))
            h.runtime.joined(channel, nib.id)
            await h.settle()
        assert bot_lines(db, channel) == ["nibbles: alice asked me to tell you (1m ago): check the Retro board"]
        assert held_rows(db) == []

    asyncio.run(run())


def test_tell_refusals_at_creation(db, lane, hub, sysop, alice, nib, channel):
    bot = install(db, sysop, channel)
    secret = create_channel(db, "secret", creator=sysop, members_only=True)
    save_bot(db, actor=sysop, name="Baud", channels=[channel.name, secret.name], defaults=[channel.name],
             enabled=True, bot_id=bot.id)
    alice_caller = {"id": alice.id, "name": alice.username}

    def tell(to, text="hello", where=channel, present=frozenset(), caller=alice_caller):
        return held.hold_tell(db, bot.id, where.name, caller, to, text, present)

    with pytest.raises(BotError, match="local accounts"):
        tell("bob@Elsewhere")
    with pytest.raises(BotError, match="no account named ghost"):
        tell("ghost")
    with pytest.raises(BotError, match="1-160"):
        tell("nib", "x" * 161)
    with pytest.raises(BotError, match="cannot enter this channel"):
        tell("nib", where=secret)
    assert tell("nib", present=frozenset({"nib"})) == {"held": False, "present": True, "name": "nib"}
    assert held_rows(db) == []
    block_local_sender(db, nib, alice)
    with pytest.raises(BotError, match="nib does not accept messages from you"):
        tell("nib")
    unblock_local_sender(db, nib, alice.id)
    set_accepts_direct_messages(db, nib, False)
    with pytest.raises(BotError, match="nib does not accept messages from you"):
        tell("nib")
    assert held_rows(db) == []


def test_tell_caps(db, sysop, alice, nib, channel):
    bot = install(db, sysop, channel)
    senders = [create_user(db, f"s{i}", password="hunter2", user_level=10) for i in range(6)]
    callers = [{"id": u.id, "name": u.username} for u in senders]
    for _ in range(held.TELLS_PER_SENDER):
        held.hold_tell(db, bot.id, channel.name, callers[0], "alice", "hi")
    with pytest.raises(BotError, match="You have 3 tells waiting"):
        held.hold_tell(db, bot.id, channel.name, callers[0], "alice", "hi")
    for caller in callers[1:6]:
        held.hold_tell(db, bot.id, channel.name, caller, "nib", "hi")
    with pytest.raises(BotError, match="nib has 5 tells waiting"):
        held.hold_tell(db, bot.id, channel.name, {"id": alice.id, "name": "alice"}, "nib", "hi")
    # 8 so far; fill the instance to 32 with fresh pairs.
    while len(held_rows(db)) < held.TELLS_PER_BOT:
        u = create_user(db, f"r{len(held_rows(db))}", password="hunter2", user_level=10)
        s = create_user(db, f"q{len(held_rows(db))}", password="hunter2", user_level=10)
        held.hold_tell(db, bot.id, channel.name, {"id": s.id, "name": s.username}, u.username, "hi")
    with pytest.raises(BotError, match="holding 32 tells"):
        held.hold_tell(db, bot.id, channel.name, {"id": alice.id, "name": "alice"}, "s1", "hi")
    # Expired tells free their places, silently.
    expire_all(db)
    held.hold_tell(db, bot.id, channel.name, callers[0], "alice", "fresh")
    assert len(held_rows(db)) == 1


def test_tell_dropped_silently_when_blocked_after_it_was_made(db, lane, hub, sysop, alice, nib, channel):
    bot = install(db, sysop, channel)

    async def run():
        async with Harness(lane, hub, bot) as h:
            token = h.request(channel, {"id": alice.id, "name": alice.username})
            await h.act(action="tell", request=token, to="nib", text="psst")
            block_local_sender(db, nib, alice)
            hub.join(channel.name, ParticipantId("nib", 1))
            h.runtime.joined(channel, nib.id)
            await h.settle()
        assert bot_lines(db, channel) == [] and held_rows(db) == []

    asyncio.run(run())


def test_link_sender_tell_rechecked_at_delivery(db, lane, hub, sysop, nib, channel, monkeypatch):
    bot = install(db, sysop, channel)
    sender = {"id": "link:abc123:bob", "name": "bob@Elsewhere"}
    import netbbs.link.enforcement as enforcement
    allowed = SimpleNamespace(allowed=True)
    monkeypatch.setattr(enforcement, "decide_node_action", lambda *a: allowed)
    monkeypatch.setattr(enforcement, "decide_user_authorship", lambda *a: allowed)

    made = held.hold_tell(db, bot.id, channel.name, sender, "nib", "first")
    assert held_rows(db)[0]["sender_link"] == "link:abc123:bob"
    held.hold_tell(db, bot.id, channel.name, sender, "nib", "second")

    async def run():
        async with Harness(lane, hub, bot) as h:
            hub.join(channel.name, ParticipantId("nib", 1))
            await h.runtime._deliver(channel, made["tell_id"], bot.id, "tell")
            # The Link sender's node may no longer author here: dropped.
            monkeypatch.setattr(enforcement, "decide_user_authorship", lambda *a: SimpleNamespace(allowed=False))
            await h.runtime._deliver(channel, made["tell_id"] + 1, bot.id, "tell")
        assert bot_lines(db, channel) == ["nib: bob@Elsewhere asked me to tell you (1m ago): first"]
        assert held_rows(db) == []

    asyncio.run(run())
    # A blocked Link address is refused when the tell is made.
    block_link_sender(db, nib, "bob@abc123")
    with pytest.raises(BotError, match="does not accept messages from you"):
        held.hold_tell(db, bot.id, channel.name, sender, "nib", "third")


def test_cancel_own_tell_only(db, sysop, alice, nib, channel):
    bot = install(db, sysop, channel)
    made = held.hold_tell(db, bot.id, channel.name, {"id": alice.id, "name": "alice"}, "nib", "hi")
    assert held.cancel_tell(db, bot.id, channel.name, {"id": nib.id, "name": "nib"}, made["tell_id"]) == {
        "cancelled": False}
    assert held.list_tells(db, bot.id, channel.name, {"id": nib.id, "name": "nib"}) == []
    assert held.cancel_tell(db, bot.id, channel.name, {"id": alice.id, "name": "alice"}, made["tell_id"]) == {
        "cancelled": True}


HELLO_ECHO = (
    "import json, sys\n"
    "hello = json.loads(sys.stdin.readline())\n"
    "print(json.dumps({'type': 'ready', 'version': 1}), flush=True)\n"
    "print(json.dumps({'id': 's', 'action': 'state.set', 'value': {'tz': hello['timezone']}}), flush=True)\n"
    "for line in sys.stdin:\n"
    "    pass\n"
)


def test_hello_names_the_display_zone_and_a_zone_change_restarts(db, lane, hub, sysop, channel):
    import sys
    from netbbs.bots.state import read_state
    from netbbs.timeutil import set_display_timezone
    set_display_timezone(db, "Europe/Berlin")
    bot = install(db, sysop, channel, command=[sys.executable, "-u", "-c", HELLO_ECHO])

    async def run():
        runtime = BotRuntime(lane, hub, poll_seconds=.03, restart_seconds=.03)
        runtime.start()
        try:
            async with asyncio.timeout(30):
                while read_state(db, bot.storage_name).get("tz") != "Europe/Berlin":
                    await asyncio.sleep(.05)
                set_display_timezone(db, "America/New_York")
                while read_state(db, bot.storage_name).get("tz") != "America/New_York":
                    await asyncio.sleep(.05)
        finally:
            await runtime.close()

    asyncio.run(run())
