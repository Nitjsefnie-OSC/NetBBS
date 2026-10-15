"""
Link Communities on the SysOp console (issue #1310): a Community's screen
links and unlinks it and shows its Link side; removing a Linked one closes it,
removing a subscribed copy leaves it; the offered list subscribes; and both
Community lists wear the linked marker.
"""

from __future__ import annotations

import asyncio

from netbbs.boards.boards import create_board
from netbbs.communities import create_community, get_community, list_communities
from netbbs.link.boards import link_board
from netbbs.link.carry import EXCLUDED, OFFERED, accept_genesis, carry_decision_state
from netbbs.link.communities import (
    CarryCaps,
    accept_community_genesis,
    community_link,
    promote_community,
    store_received_members,
    subscribe_community,
)
from netbbs.link.events import build_board_genesis, build_community_genesis, build_community_members
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.store import served_community_ids
from netbbs.net.admin_flow import admin_menu
from tests.test_admin_flow import FakeSession, _announced_text, _link_context, _visible, _written_text, db, lane, sysop  # noqa: F401 -- fixtures

BOARD_ID = "a" * 64


def _offered_community(db, origin, *, boards=(BOARD_ID,)):
    genesis = build_community_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, name="Retro",
        created_at="2026-03-01T00:00:00.000000Z", default_min_read_level=10,
    )
    accept_community_genesis(db, envelope=genesis.to_dict(), sender_fingerprint=origin.fingerprint, content_id=genesis.content_id)
    members = build_community_members(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        community_id=genesis.payload["community_id"],
        members={"boards": list(boards), "channels": [], "file_areas": []}, created_at="2026-03-02T00:00:00Z",
    )
    store_received_members(
        db, envelope=members.to_dict(), sender_fingerprint=origin.fingerprint, content_id=members.content_id,
        caps=CarryCaps(boards=500, channels=500, file_areas=500, own_fingerprint=None),
    )
    return genesis.payload["community_id"]


def _carried_board(db, origin, own):
    genesis = build_board_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, board_id=BOARD_ID,
        name="Amiga", created_at="2026-03-01T00:00:00Z",
    )
    accept_genesis(
        db, kind="boards", envelope=genesis.to_dict(), sender_fingerprint=origin.fingerprint,
        content_id=genesis.content_id, own_fingerprint=own.fingerprint, cap=500,
    )


def test_a_community_is_linked_from_its_screen_and_says_so(db, lane, sysop):
    community = create_community(db, "Pen Talk", creator=sysop)
    board = create_board(db, "Fountain pens", creator=sysop, community_id=community.id)
    link_context = _link_context()
    link_board(db, board, node_identity=link_context.node_identity)

    # admin -> Content -> Communities -> List -> 01 -> [L]ink -> yes -> back x5.
    session = FakeSession(["m", "t", "l", "0", "1", "l", "y", "b", "b", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop, link_context=link_context))

    text = " ".join(_visible(_written_text(session)).split())
    assert "'Pen Talk' is Linked" in text
    assert "Linked, origin here" in text and "1 message board" in text
    link = community_link(db, community.id, link_context.node_identity.fingerprint)
    assert link.own and link.members["boards"] == (board.board_id,)
    # The running node knows its own genesis.
    assert link.link_id in link_context.link_node.communities


def test_unlink_closes_it_and_keeps_it_local(db, lane, sysop):
    community = create_community(db, "Pen Talk", creator=sysop)
    link_context = _link_context()
    (genesis, _members) = promote_community(db, community.id, node_identity=link_context.node_identity)
    link_id = genesis["envelope"]["payload"]["community_id"]

    session = FakeSession(["m", "t", "l", "0", "1", "c", "y", "b", "b", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop, link_context=link_context))

    assert "'Pen Talk' unlinked" in _written_text(session)
    assert community_link(db, community.id, link_context.node_identity.fingerprint) is None
    assert get_community(db, community.id) is not None
    assert link_id in link_context.link_node.community_events.closures
    assert served_community_ids(db) == [link_id]


def test_removing_a_linked_community_closes_it_first(db, lane, sysop):
    community = create_community(db, "Pen Talk", creator=sysop)
    link_context = _link_context()
    (genesis, _members) = promote_community(db, community.id, node_identity=link_context.node_identity)
    link_id = genesis["envelope"]["payload"]["community_id"]

    session = FakeSession(["m", "t", "l", "0", "1", "r", "Pen Talk", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop, link_context=link_context))

    text = " ".join(_visible(_written_text(session)).split())
    assert "removing it also closes it on every node that carries it" in text
    assert list_communities(db) == []
    assert link_id in link_context.link_node.community_events.closures


def test_a_linked_community_is_not_removed_where_no_closure_can_be_signed(db, lane, sysop):
    community = create_community(db, "Pen Talk", creator=sysop)
    promote_community(db, community.id, node_identity=bootstrap_node_identity("elsewhere"))

    session = FakeSession(["m", "t", "l", "0", "1", "r", "b", "b", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop))

    assert "remove it from the running node's console" in " ".join(_visible(_written_text(session)).split())
    assert len(list_communities(db)) == 1


def test_accepting_an_offered_community_subscribes(db, lane, sysop):
    from netbbs.net.admin_link import _carry_decisions_screen

    origin = bootstrap_node_identity("ui-origin")
    link_context = _link_context()
    _carried_board(db, origin, link_context.node_identity)
    link_id = _offered_community(db, origin)

    session = FakeSession(["1", "\r", "a"])
    asyncio.run(_carry_decisions_screen(session, lane, sysop, link_context=link_context, state=OFFERED))

    text = " ".join(_visible(_written_text(session)).split())
    assert "Community from" in text and "1 member" in text and "Listed members" in text
    assert "Subscribed to 'Retro'" in _announced_text(session)
    (copy,) = list_communities(db)
    assert copy.default_min_read_level == 10
    assert db.connection.execute(
        "SELECT community_id FROM boards WHERE board_id = ?", (BOARD_ID,)
    ).fetchone()["community_id"] == copy.id
    assert carry_decision_state(db, "communities", link_id) is None


def test_removing_a_subscribed_copy_leaves_the_community(db, lane, sysop):
    origin = bootstrap_node_identity("ui-origin")
    link_context = _link_context()
    _carried_board(db, origin, link_context.node_identity)
    link_id = _offered_community(db, origin)
    subscribe_community(
        db, link_id, actor=sysop,
        caps=CarryCaps(boards=500, channels=500, file_areas=500, own_fingerprint=link_context.node_identity.fingerprint),
    )

    session = FakeSession(["m", "t", "l", "0", "1", "r", "Retro", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop, link_context=link_context))

    text = " ".join(_visible(_written_text(session)).split())
    assert "Subscribed" in text and "removing it leaves it" in text
    assert "you left that Community" in text
    assert list_communities(db) == []
    assert carry_decision_state(db, "communities", link_id) == EXCLUDED
    assert db.connection.execute("SELECT 1 FROM boards WHERE board_id = ?", (BOARD_ID,)).fetchone() is not None


def test_both_community_lists_mark_a_linked_one(db, lane, sysop):
    from netbbs.rendering import LINKED_MARKER

    linked = create_community(db, "Pen Talk", creator=sysop)
    create_community(db, "Local Only", creator=sysop)
    promote_community(db, linked.id, node_identity=bootstrap_node_identity("marker"))

    session = FakeSession(["m", "t", "l", "b", "b", "b", "b"])
    session.output_charset = "ascii"
    asyncio.run(admin_menu(session, lane, sysop))

    text = _visible(_written_text(session))
    assert f"Pen Talk{LINKED_MARKER}" in text
    assert f"Local Only{LINKED_MARKER}" not in text


def test_subscribing_refreshes_the_mrc_bridge(db, lane, sysop):
    """Review of #1340: a subscription can carry chat channels in, as
    accepting one directly does, so the running bridge re-reads its rooms."""
    from netbbs.net.admin_link import _carry_decisions_screen

    class _Bridge:
        refreshed = 0

        async def refresh_channel_mappings(self):
            _Bridge.refreshed += 1

    origin = bootstrap_node_identity("ui-origin")
    _offered_community(db, origin, boards=())
    session = FakeSession(["1", "\r", "a"])
    asyncio.run(_carry_decisions_screen(
        session, lane, sysop, link_context=_link_context(), state=OFFERED, mrc_bridge=_Bridge(),
    ))
    assert _Bridge.refreshed == 1


def test_a_linked_community_another_identity_signed_is_not_removed(db, lane, sysop):
    """Review of #1340: a database Linked by another identity cannot be
    closed by this node, so removing must not delete it unannounced."""
    community = create_community(db, "Pen Talk", creator=sysop)
    promote_community(db, community.id, node_identity=bootstrap_node_identity("someone-else"))

    session = FakeSession(["m", "t", "l", "0", "1", "r", "Pen Talk", "b", "b", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop, link_context=_link_context()))

    assert "cannot be closed here" in " ".join(_visible(_written_text(session)).split())
    assert len(list_communities(db)) == 1
