"""
Link moderation's roster over real transport (issue #1359): an origin's
roster reaches a carrier by push and by pull, travels on from a carrier to a
third node with the board it rides on, and never reaches a peer that does not
advertise `link_moderation`.
"""

from __future__ import annotations

import asyncio

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards.boards import create_board
from netbbs.link import sync as sync_module
from netbbs.link import transport as transport_module
from netbbs.link.boards import link_board
from netbbs.link.events import LINK_MODERATION_CAPABILITY, event_content_id
from netbbs.link.moderators import grant_link_moderator, roster_in_force
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from tests.test_link_communities_end_to_end import _NodeDb, _pass


def _origin_with_roster(tmp_path):
    identity = bootstrap_node_identity("origin")
    node = LinkNode(identity=identity)
    store = _NodeDb(tmp_path, "origin")
    sysop = create_user(store.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    board = create_board(store.db, "Retro", creator=sysop)
    link_board(store.db, board, node_identity=identity)
    raw = grant_link_moderator(
        store.db, identity, home_node_fingerprint="e" * 64, local_user_id="wren", scope_kind="origin",
        scope_id=None, actor=sysop, label="Everything",
    )
    content_id = event_content_id(raw["envelope"])
    node.known_event_ids.add(content_id)
    node.events[content_id] = raw
    return identity, node, store, board, raw


def _peer(tmp_path, name):
    identity = bootstrap_node_identity(name)
    return identity, LinkNode(identity=identity), _NodeDb(tmp_path, name)


def test_a_carrier_gets_the_roster_by_push_and_a_third_node_pulls_it_from_the_carrier(tmp_path):
    origin_identity, origin_node, origin, _board, raw = _origin_with_roster(tmp_path)
    _carrier_identity, carrier_node, carrier = _peer(tmp_path, "carrier")
    _third_identity, third_node, third = _peer(tmp_path, "third")
    try:
        # The origin dials the carrier: its push sends the roster with the board.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        assert roster_in_force(carrier.db, origin_identity.fingerprint) == raw
        # The third node pulls from the carrier, which relays what it holds.
        asyncio.run(_pass(origin_node, origin, third_node, third.lane))  # learns the origin's identity
        third.db.connection.execute("DELETE FROM link_moderator_rosters")
        third.db.connection.commit()
        third_node.known_event_ids.discard(event_content_id(raw["envelope"]))
        third_node.events.pop(event_content_id(raw["envelope"]), None)
        asyncio.run(_pass(third_node, third, carrier_node, carrier.lane))
        assert roster_in_force(third.db, origin_identity.fingerprint) == raw
    finally:
        for store in (origin, carrier, third):
            store.close()


def test_a_carrier_pulls_the_roster_from_its_origin(tmp_path):
    origin_identity, origin_node, origin, _board, raw = _origin_with_roster(tmp_path)
    _carrier_identity, carrier_node, carrier = _peer(tmp_path, "carrier")
    try:
        asyncio.run(_pass(carrier_node, carrier, origin_node, origin.lane))
        assert roster_in_force(carrier.db, origin_identity.fingerprint) == raw
    finally:
        origin.close()
        carrier.close()


def test_a_peer_without_the_capability_gets_no_roster(tmp_path, monkeypatch):
    origin_identity, origin_node, origin, board, _raw = _origin_with_roster(tmp_path)
    _carrier_identity, carrier_node, carrier = _peer(tmp_path, "carrier")
    real = sync_module.descriptor_has_capability

    def without_moderation(descriptor, capability):
        return False if capability == LINK_MODERATION_CAPABILITY else real(descriptor, capability)

    monkeypatch.setattr(sync_module, "descriptor_has_capability", without_moderation)
    monkeypatch.setattr(transport_module, "descriptor_has_capability", without_moderation)
    try:
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        asyncio.run(_pass(carrier_node, carrier, origin_node, origin.lane))
        assert carrier.db.connection.execute("SELECT COUNT(*) FROM link_moderator_rosters").fetchone()[0] == 0
        assert carrier.db.connection.execute("SELECT COUNT(*) FROM opaque_events").fetchone()[0] == 0
        assert carrier.db.connection.execute(
            "SELECT 1 FROM boards WHERE board_id = ?", (board.board_id,)
        ).fetchone() is not None
    finally:
        origin.close()
        carrier.close()
