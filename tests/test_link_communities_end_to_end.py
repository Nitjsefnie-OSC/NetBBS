"""
Link Communities over real transport (issue #1310): an origin promotes a
Community, a peer's sync pass is offered it with its members, subscribing
files them into the local copy, a member linked later follows on the next
pass, a restart keeps it all, and a closure reaches the carrier. A peer that
does not advertise `link_communities` is never sent community events.
"""

from __future__ import annotations

import asyncio

import aiohttp

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards.boards import create_board
from netbbs.communities import create_community, get_community
from netbbs.link import sync as sync_module
from netbbs.link.boards import link_board
from netbbs.link.carry import OFFERED, carry_decision_state, list_carry_decisions
from netbbs.link.communities import (
    CarryCaps,
    close_own_community,
    community_link,
    promote_community,
    subscribe_community,
)
from netbbs.link.events import LINK_COMMUNITIES_CAPABILITY, event_content_id
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.link.store import load_link_node
from netbbs.link.sync import run_link_sync
from netbbs.link.transport import LinkServer
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from tests.link_sync_wait import run_sync_briefly


class _NodeDb:
    def __init__(self, tmp_path, name: str) -> None:
        self.db = Database(tmp_path / f"{name}.db")
        self.lane = DatabaseLane(self.db.path)

    def close(self) -> None:
        self.lane.close()
        self.db.close()


def _hello_for(node: LinkNode):
    return node.build_hello(addresses=None, outgoing_only=True, created_at="2026-01-01T00:00:00+00:00")


async def _pass(dialer_node, dialer, server_node, server_lane) -> None:
    """One sync pass of `dialer_node` against `server_node`'s real server."""
    server = LinkServer(
        host="127.0.0.1", port=0, node=server_node, own_hello_provider=lambda: _hello_for(server_node),
        lane=server_lane,
    )
    await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(run_link_sync(
                dialer_node, session, [f"http://127.0.0.1:{server.port}"], lambda: _hello_for(dialer_node),
                dialer.lane, interval_seconds=60.0,
                max_carried_boards=500, max_carried_channels=500, max_carried_file_areas=500,
            ))
            await run_sync_briefly(task)
    finally:
        await server.stop()


def _register(node: LinkNode, events: list[dict]) -> None:
    """What the SysOp screen does after promoting: the running node knows
    its own events, as after `link_board`."""
    from netbbs.link.events import CommunityGenesis

    for raw in events:
        content_id = event_content_id(raw["envelope"])
        node.known_event_ids.add(content_id)
        node.events[content_id] = raw
        if raw["envelope"]["object_type"] == "community_genesis":
            node.community_events.record_genesis(CommunityGenesis.from_dict(raw))


def test_a_promoted_community_is_offered_subscribed_and_kept_in_step(tmp_path):
    origin_identity = bootstrap_node_identity("origin")
    carrier_identity = bootstrap_node_identity("carrier")
    origin_node = LinkNode(identity=origin_identity)
    carrier_node = LinkNode(identity=carrier_identity)
    origin = _NodeDb(tmp_path, "origin")
    carrier = _NodeDb(tmp_path, "carrier")
    sysop = create_user(origin.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    carrier_sysop = create_user(carrier.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    community = create_community(origin.db, "Retro", creator=sysop)
    first = create_board(origin.db, "first", creator=sysop, community_id=community.id)
    link_board(origin.db, first, node_identity=origin_identity)
    events = promote_community(origin.db, community.id, node_identity=origin_identity)
    _register(origin_node, events)
    link_id = events[0]["envelope"]["payload"]["community_id"]

    try:
        # The origin dials the carrier: its push delivers the Community.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))

        assert carry_decision_state(carrier.db, "communities", link_id) == OFFERED
        offered = [d for d in list_carry_decisions(carrier.db, OFFERED) if d.kind == "communities"]
        assert [(d.name, d.member_count) for d in offered] == [("Retro", 1)]
        # The board itself came in by the node-wide intake, with no Community.
        carried = carrier.db.connection.execute(
            "SELECT community_id FROM boards WHERE board_id = ?", (first.board_id,)
        ).fetchone()
        assert carried is not None and carried["community_id"] is None

        local_id = subscribe_community(
            carrier.db, link_id, actor=carrier_sysop,
            caps=CarryCaps(boards=500, channels=500, file_areas=500, own_fingerprint=carrier_identity.fingerprint),
        )
        assert carrier.db.connection.execute(
            "SELECT community_id FROM boards WHERE board_id = ?", (first.board_id,)
        ).fetchone()["community_id"] == local_id

        # A board linked into the Community later follows on the next pass:
        # the origin signs a new list, and the carrier files the board.
        second = create_board(origin.db, "second", creator=sysop, community_id=community.id)
        link_board(origin.db, second, node_identity=origin_identity)
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        assert carrier.db.connection.execute(
            "SELECT community_id FROM boards WHERE board_id = ?", (second.board_id,)
        ).fetchone()["community_id"] == local_id

        # A restarted carrier still knows the Community, and takes its closure.
        restarted = load_link_node(carrier.db, carrier_identity)
        assert link_id in restarted.communities
        closure = close_own_community(origin.db, community.id, node_identity=origin_identity)
        _register(origin_node, [closure])
        asyncio.run(_pass(origin_node, origin, restarted, carrier.lane))
        link = community_link(carrier.db, local_id, carrier_identity.fingerprint)
        assert link.closed
        assert get_community(carrier.db, local_id) is not None
    finally:
        origin.close()
        carrier.close()


def test_a_peer_without_the_capability_is_sent_no_community_events(tmp_path, monkeypatch):
    origin_identity = bootstrap_node_identity("origin")
    carrier_identity = bootstrap_node_identity("carrier")
    origin_node = LinkNode(identity=origin_identity)
    carrier_node = LinkNode(identity=carrier_identity)
    origin = _NodeDb(tmp_path, "origin")
    carrier = _NodeDb(tmp_path, "carrier")
    sysop = create_user(origin.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    community = create_community(origin.db, "Retro", creator=sysop)
    board = create_board(origin.db, "first", creator=sysop, community_id=community.id)
    link_board(origin.db, board, node_identity=origin_identity)
    _register(origin_node, promote_community(origin.db, community.id, node_identity=origin_identity))
    real = sync_module.descriptor_has_capability
    monkeypatch.setattr(
        sync_module, "descriptor_has_capability",
        lambda descriptor, capability: False if capability == LINK_COMMUNITIES_CAPABILITY else real(descriptor, capability),
    )
    try:
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        assert carrier.db.connection.execute(
            "SELECT COUNT(*) FROM link_events WHERE community_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert carrier.db.connection.execute("SELECT COUNT(*) FROM opaque_events").fetchone()[0] == 0
        assert carrier.db.connection.execute(
            "SELECT 1 FROM boards WHERE board_id = ?", (board.board_id,)
        ).fetchone() is not None
    finally:
        origin.close()
        carrier.close()


def test_a_carrier_pulls_a_community_from_its_origin(tmp_path):
    """The other direction: the carrier dials the origin, and its inventory
    pull -- not a push -- brings the Community."""
    origin_identity = bootstrap_node_identity("origin")
    carrier_identity = bootstrap_node_identity("carrier")
    origin_node = LinkNode(identity=origin_identity)
    carrier_node = LinkNode(identity=carrier_identity)
    origin = _NodeDb(tmp_path, "origin")
    carrier = _NodeDb(tmp_path, "carrier")
    sysop = create_user(origin.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    community = create_community(origin.db, "Retro", creator=sysop)
    events = promote_community(origin.db, community.id, node_identity=origin_identity)
    _register(origin_node, events)
    link_id = events[0]["envelope"]["payload"]["community_id"]
    try:
        # The origin must know the carrier before it answers its inventory.
        asyncio.run(_pass(carrier_node, carrier, origin_node, origin.lane))
        asyncio.run(_pass(carrier_node, carrier, origin_node, origin.lane))
        assert carry_decision_state(carrier.db, "communities", link_id) == OFFERED
    finally:
        origin.close()
        carrier.close()
