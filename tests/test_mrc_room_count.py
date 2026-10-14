"""
The network's room count (issue #1283): read once a minute with the
hub's server stats query, it tells a caller when the kept room list no
longer matches the network, and it sends a fresh listing as soon as the
count changes while somebody here is on MRC.

The query is a lone `~` on a connection of its own (MRCDoc rev 1.26),
which the hub answers with `<bbses> <rooms> <users> <activity>` and
closes. Nothing caller-less beyond that is ever sent.
"""

from __future__ import annotations

import asyncio
import datetime
import random

from netbbs.chat.hub import ParticipantId
from netbbs.mrc.bridge import MrcBridge, MrcState
from netbbs.mrc.settings import (
    DIRECTORY_TIMESTAMP_FORMAT,
    DirectoryEntry,
    MrcSettings,
    OpenRoomSettings,
    hub_identity,
    load_mrc_settings,
    save_directory_snapshot,
    save_mrc_settings,
    save_open_room_settings,
)
from netbbs.net import chat_flow
from tests.mrc_fake_hub import FakeMrcHub
from tests.test_chat_flow_mrc import _wait_for
from tests.test_chat_flow_mrc_open_rooms import (  # noqa: F401 -- fixtures and helpers
    FakeSession,
    _visible_text,
    alice,
    db,
    hub,
    lane,
    presence,
)

POLL = 0.05


def _stamp(*, hours_ago: float) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return moment.strftime(DIRECTORY_TIMESTAMP_FORMAT)


async def _start(db, lane, hub, *, stats: str | None, kept_rooms=()):
    fake = FakeMrcHub()
    fake.server_stats = stats
    await fake.start()
    save_mrc_settings(db, MrcSettings(enabled=True, host="127.0.0.1", port=fake.port, tls=False, site_name="My Board"))
    save_open_room_settings(db, OpenRoomSettings(enabled=True))
    if kept_rooms:
        save_directory_snapshot(db, hub_identity(load_mrc_settings(db)), [
            DirectoryEntry(room, 1, "", _stamp(hours_ago=3.2)) for room in kept_rooms
        ])
    bridge = MrcBridge(
        hub=hub, lane=lane, version="5.8.0", rng=random.Random(1),
        min_backoff_seconds=0.05, max_backoff_seconds=0.2, stable_after_seconds=0.0, per_user_interval_seconds=0.0,
        room_count_poll_seconds=POLL,
    )
    await bridge.start()
    await _wait_for(lambda: bridge.state is MrcState.CONNECTED, what="the bridge to connect", timeout=30)
    return fake, bridge


async def _picker_text(lane, hub, alice, bridge) -> str:
    session = FakeSession(["b"])
    await chat_flow._pick_mrc_room(session, lane, hub, alice, bridge)
    return " ".join(_visible_text(session).split())


def test_a_changed_count_with_nobody_on_mrc_says_the_list_is_out_of_date(db, lane, hub, presence, alice):
    async def scenario():
        fake, bridge = await _start(db, lane, hub, stats="3 14 5 1", kept_rooms=("chess", "lobby", "quiet"))
        try:
            await _wait_for(lambda: bridge.status().network_rooms == 14, what="the room count")
            assert bridge.directory_drift()[:2] == (3, 14)
            text = await _picker_text(lane, hub, alice, bridge)
            assert "Room list as of 3 h ago; the network now has 14 rooms." in text
            assert "chess" in text  # the kept rooms are still offered

            # The network shrinks back to what the list says: no note.
            fake.server_stats = "3 3 5 1"
            await _wait_for(lambda: bridge.status().network_rooms == 3, what="the new count")
            assert bridge.directory_drift() is None
            assert "Room list as of" not in await _picker_text(lane, hub, alice, bridge)
        finally:
            await bridge.close()
            await fake.close()

    asyncio.run(scenario())


def test_the_query_is_a_lone_tilde_on_its_own_connection(db, lane, hub, presence, alice):
    async def scenario():
        fake, bridge = await _start(db, lane, hub, stats="3 2 5 1")
        try:
            await _wait_for(lambda: fake.stats_queries >= 2, what="two polls")
            # The bridge's one session, and nothing the polls sent reached it.
            assert fake.connections == 1
            assert not [packet for packet in fake.received if packet.from_user.upper() not in ("CLIENT",)
                        and packet.body.startswith("~")]
            assert fake.unknown_commands == []
        finally:
            await bridge.close()
            await fake.close()

    asyncio.run(scenario())


def test_no_answer_changes_nothing_a_caller_sees(db, lane, hub, presence, alice):
    async def scenario():
        fake, bridge = await _start(db, lane, hub, stats=None, kept_rooms=("chess", "lobby"))
        try:
            await _wait_for(lambda: fake.stats_queries >= 2, what="the polls to be refused")
            assert bridge.status().network_rooms is None
            assert bridge.directory_drift() is None
            text = await _picker_text(lane, hub, alice, bridge)
            assert "Room list as of" not in text and "chess" in text
        finally:
            await bridge.close()
            await fake.close()

    asyncio.run(scenario())


def test_a_count_change_with_a_caller_on_mrc_lists_once_within_the_bound(db, lane, hub, presence, alice):
    async def scenario():
        fake, bridge = await _start(db, lane, hub, stats="3 2 5 1")
        try:
            mapping = await bridge.open_room("lobby", "alice")
            hub.join(mapping.channel.name, ParticipantId("alice", 901))
            await bridge.local_join(mapping.channel, "alice")
            await _wait_for(lambda: fake.users, what="alice announced")

            def lists():
                return [packet for packet in fake.received if packet.to_user.upper() == "SERVER"
                        and packet.body.upper() == "LIST"]

            await _wait_for(lambda: bridge.status().network_rooms == 2, what="the first count")
            asked = fake.stats_queries
            await _wait_for(lambda: fake.stats_queries >= asked + 2, what="two more polls")
            assert lists() == []  # an unchanged count asks for nothing

            fake.server_stats = "3 5 5 1"
            await _wait_for(lambda: len(lists()) == 1, what="one LIST after the count changed")
            assert lists()[0].from_user.lower() == "alice"

            # Another change inside the shared refresh bound sends nothing more.
            fake.server_stats = "3 7 5 1"
            await _wait_for(lambda: bridge.status().network_rooms == 7, what="the second change")
            asked = fake.stats_queries
            await _wait_for(lambda: fake.stats_queries >= asked + 2, what="two more polls")
            assert len(lists()) == 1
        finally:
            await bridge.close()
            await fake.close()

    asyncio.run(scenario())
