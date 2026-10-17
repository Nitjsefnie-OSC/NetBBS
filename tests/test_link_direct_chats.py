"""
Direct chats between callers of two nodes over real real-time sessions
(issue #1368): `netbbs.link.realtime_dm.LinkDirectChats` on each node,
wired as `netbbs.__main__` wires it, with the four `direct_chat_*` frames.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.link.realtime_dm import END, LINE, LOST, DirectChatRefused, LinkDirectChats
from tests.test_link_realtime_relay import _Node


async def _pair(tmp_path, *, accept=True, line_allowed=True, grace=0.5):
    """Node `a` serves; node `b` dials it. `a`'s caller "alice" answers
    invitations as `accept` says (True, False, or None: not at all)."""
    a, b = _Node(tmp_path, "node-a"), _Node(tmp_path, "node-b")
    await a.start_server(serving=False)
    b.wire_party()
    a.trust(b)
    b.trust(a)
    b.know_relay(a)
    offered: list = []
    rooms: list = []

    async def offer(incoming):
        offered.append(incoming)
        if accept is True:
            rooms.append(await incoming.accept())
        elif accept is False:
            await incoming.refuse("declined")

    async def allowed(fingerprint, from_user, to_user):
        return line_allowed

    a.chats = LinkDirectChats(
        link_node=a.link_node, lane=a.lane, registry=a.registry, ensure_session=a.direct.ensure_session,
        offer=offer, line_allowed=allowed, reconnect_grace_seconds=grace,
    )
    a.bridge.register_frame_handler(a.chats.owns_frame, a.chats.handle_frame)
    b.chats = LinkDirectChats(
        link_node=b.link_node, lane=b.lane, registry=b.registry, ensure_session=b.direct.ensure_session,
        reconnect_grace_seconds=grace,
    )
    b.bridge.register_frame_handler(b.chats.owns_frame, b.chats.handle_frame)
    # `a` learns `b`'s identity from the dial, so it can answer.
    await b.direct.ensure_session(a.identity.fingerprint)
    for _ in range(200):  # until `a` has admitted the session too
        if a.registry.get(b.identity.fingerprint) is not None:
            break
        await asyncio.sleep(0.01)
    return a, b, offered, rooms


async def _invite(a, b):
    return await b.chats.invite(
        a.identity.fingerprint, from_user="bob", from_label="Bob", to_user="alice", peer_label="alice@node-a",
    )


async def _next(room, timeout=5.0):
    return await asyncio.wait_for(room.events.get(), timeout=timeout)


def test_an_accepted_chat_carries_lines_both_ways_and_ends_when_one_leaves(tmp_path):
    async def scenario():
        a, b, offered, rooms = await _pair(tmp_path)
        try:
            invite = await _invite(a, b)
            assert await asyncio.wait_for(invite.answer, 5) == "accepted"
            [incoming] = offered
            assert (incoming.from_user, incoming.from_label, incoming.to_user) == ("bob", "Bob", "alice")
            here, there = invite.room, rooms[0]
            assert await here.send_line("hello alice")
            assert await _next(there) == (LINE, "hello alice")
            assert await there.send_line("hi bob")
            assert await _next(here) == (LINE, "hi bob")
            await there.leave()
            assert await _next(here) == (END, "left")
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_a_decline_is_reported(tmp_path):
    async def scenario():
        a, b, _offered, _rooms = await _pair(tmp_path, accept=False)
        try:
            invite = await _invite(a, b)
            assert await asyncio.wait_for(invite.answer, 5) == "declined"
            assert invite.room is None
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_a_blocked_or_opted_out_caller_reads_as_unavailable(tmp_path):
    async def scenario():
        a, b, offered, _rooms = await _pair(tmp_path, line_allowed=False)
        try:
            invite = await _invite(a, b)
            assert await asyncio.wait_for(invite.answer, 5) == "unavailable"
            assert offered == []  # never put before the caller
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_withdrawing_tells_the_other_side_and_a_late_accept_is_ended(tmp_path):
    async def scenario():
        a, b, offered, _rooms = await _pair(tmp_path, accept=None)
        try:
            invite = await _invite(a, b)
            for _ in range(100):
                if offered:
                    break
                await asyncio.sleep(0.02)
            [incoming] = offered
            await b.chats.withdraw(invite, "timed_out")
            await asyncio.wait_for(incoming.withdrawn.wait(), 5)
            assert await incoming.accept() is None  # withdrawn: no room opens

            # A second invitation, accepted after the inviter gave up: the
            # accepting side's room is ended at once.
            await asyncio.sleep(5.1)  # past the per-caller cooldown
            second = await _invite(a, b)
            for _ in range(100):
                if len(offered) == 2:
                    break
                await asyncio.sleep(0.02)
            b.chats._pending_out.clear()  # the inviter's side gave up without a word
            second.answer.cancel()
            room = await offered[1].accept()
            assert await _next(room) == (END, "cancelled")
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_invitations_from_one_caller_are_rate_limited(tmp_path):
    async def scenario():
        a, b, offered, _rooms = await _pair(tmp_path, accept=False)
        try:
            first = await _invite(a, b)
            assert await asyncio.wait_for(first.answer, 5) == "declined"
            again = await _invite(a, b)
            assert await asyncio.wait_for(again.answer, 5) == "unavailable"
            assert len(offered) == 1
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_a_dropped_link_ends_the_chat_as_lost(tmp_path):
    async def scenario():
        a, b, _offered, rooms = await _pair(tmp_path, grace=0.3)
        try:
            invite = await _invite(a, b)
            assert await asyncio.wait_for(invite.answer, 5) == "accepted"
            await b.registry.close_all(reason="test_drop")
            assert await _next(invite.room) == (LOST, None)
            assert await _next(rooms[0]) == (LOST, None)
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())


def test_a_node_without_the_capability_is_never_sent_an_invitation(tmp_path):
    async def scenario():
        a, b, offered, _rooms = await _pair(tmp_path)
        try:
            b.chats.peer_capable = lambda fingerprint: False
            with pytest.raises(DirectChatRefused):
                await _invite(a, b)
            assert offered == []
        finally:
            await a.teardown()
            await b.teardown()

    asyncio.run(scenario())
