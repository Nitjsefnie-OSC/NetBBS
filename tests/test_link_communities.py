"""
Link Communities (design doc §6.5, decided in §16 issue #1308, built in issue
#1310): the three signed events, their acceptance rules, the inventory's
fourth kind, promotion and membership lists at the origin, and the
subscription, local copy, placement and closure at a carrier -- against real
SQLite files.
"""

from __future__ import annotations

import json

import pytest

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards.boards import create_board, get_board_by_name, update_board
from netbbs.chat.channels import create_channel
from netbbs.communities import (
    create_community,
    get_community,
    get_effective_min_read_level,
    list_communities,
    update_community,
)
from netbbs.files.areas import create_file_area
from netbbs.link.boards import link_board, materialize_carried_board, record_board_origin_change
from netbbs.link.carry import (
    EXCLUDED,
    OFFERED,
    CarryDecisionError,
    accept_genesis,
    accept_offer,
    carry_decision_state,
    exclude_offer,
    list_carry_decisions,
)
from netbbs.link.channels import link_channel
from netbbs.link.communities import (
    CarryCaps,
    LinkCommunitiesError,
    accept_community_genesis,
    close_own_community,
    community_link,
    leave_community,
    linked_community_ids,
    load_own_community_events,
    members_in_force,
    promote_community,
    refresh_own_community_members,
    store_received_closure,
    store_received_members,
    subscribe_community,
)
from netbbs.link.events import (
    COMMUNITY_GENESIS_OBJECT_TYPE,
    LINK_CAPABILITIES,
    LINK_COMMUNITIES_CAPABILITY,
    MAX_COMMUNITY_MEMBERS_PER_KIND,
    EventError,
    build_board_genesis,
    build_community_closure,
    build_community_genesis,
    build_community_members,
    build_file_area_genesis,
    community_link_id,
    event_content_id,
    verify_community_event,
)
from netbbs.link.files import link_file_area
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import InventoryRequest, LinkNode, LinkProtocolError, MissingDependency
from netbbs.link.store import (
    build_inventory_request,
    community_event_diff,
    community_wanted_ids,
    load_link_node,
    served_community_ids,
    uncarried_resource_ids,
)
from netbbs.communities import delete_community
from netbbs.storage.database import Database
from tests.link_harness import spawn_node
from tests.test_link_protocol import _hello_bytes, clock  # noqa: F401 -- clock is a fixture


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def sysop(db):
    return create_user(db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)


@pytest.fixture
def origin():
    return bootstrap_node_identity("origin")


@pytest.fixture
def here():
    return bootstrap_node_identity("here")


def _genesis(origin, name="Retro Computing", created_at="2026-03-01T00:00:00.000000Z", **defaults):
    return build_community_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, name=name,
        created_at=created_at, **defaults,
    )


def _members(origin, genesis, *, boards=(), channels=(), file_areas=(), created_at="2026-03-02T00:00:00.000000Z"):
    return build_community_members(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        community_id=genesis.payload["community_id"],
        members={"boards": list(boards), "channels": list(channels), "file_areas": list(file_areas)},
        created_at=created_at,
    )


def _board_genesis(origin, board_id, name="Board"):
    return build_board_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, board_id=board_id,
        name=name, created_at="2026-03-01T00:00:00.000000Z",
    )


def _caps(here, boards=500):
    return CarryCaps(boards=boards, channels=500, file_areas=500, own_fingerprint=here.fingerprint)


def _offer(db, origin, genesis):
    raw = genesis.to_dict()
    return accept_community_genesis(db, envelope=raw, sender_fingerprint=origin.fingerprint, content_id=genesis.content_id)


def _receive_members(db, origin, here, members, *, boards_cap=500):
    return store_received_members(
        db, envelope=members.to_dict(), sender_fingerprint=origin.fingerprint, content_id=members.content_id,
        caps=_caps(here, boards_cap),
    )


def _carry_board(db, origin, here, board_id, name="Board", cap=500):
    genesis = _board_genesis(origin, board_id, name)
    return accept_genesis(
        db, kind="boards", envelope=genesis.to_dict(), sender_fingerprint=origin.fingerprint,
        content_id=genesis.content_id, own_fingerprint=here.fingerprint, cap=cap,
    )


def _board_row(db, board_id):
    return db.connection.execute("SELECT * FROM boards WHERE board_id = ?", (board_id,)).fetchone()


# -- the events ------------------------------------------------------------------


def test_a_community_id_is_content_addressed_from_origin_name_and_time(origin, here):
    genesis = _genesis(origin)
    assert genesis.payload["community_id"] == community_link_id(
        origin_fingerprint=origin.fingerprint, name="Retro Computing", created_at="2026-03-01T00:00:00.000000Z"
    )
    # Two origins' same-named Communities are distinct by construction.
    assert _genesis(here).payload["community_id"] != genesis.payload["community_id"]
    assert verify_community_event(genesis, origin.signing_key.verify_key)
    assert not verify_community_event(genesis, here.signing_key.verify_key)


def test_a_membership_list_is_bounded_and_shaped(origin):
    genesis = _genesis(origin)
    members = _members(origin, genesis, boards=["b2", "b1", "b1"])
    assert members.payload["members"] == {"boards": ["b1", "b2"], "channels": [], "file_areas": []}
    with pytest.raises(EventError, match="more than 500"):
        _members(origin, genesis, boards=[f"b{i}" for i in range(MAX_COMMUNITY_MEMBERS_PER_KIND + 1)])


def test_the_capability_is_advertised():
    assert LINK_COMMUNITIES_CAPABILITY in LINK_CAPABILITIES


# -- acceptance (LinkNode.handle_events) -----------------------------------------


def _bob_knowing(tmp_path, clock, alice):  # noqa: F811
    bob = LinkNode(identity=spawn_node(tmp_path, "bob").identity)
    bob.handle_hello(_hello_bytes(LinkNode(identity=alice.identity), clock=clock))
    return bob


def test_a_genesis_from_a_known_origin_is_accepted_and_relayed_ones_too(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    carol = spawn_node(tmp_path, "carol")
    bob = _bob_knowing(tmp_path, clock, alice)
    bob.handle_hello(_hello_bytes(LinkNode(identity=carol.identity), clock=clock))
    genesis = _genesis(alice.identity)

    # Relayed by carol: verified against alice, its origin.
    assert bob.handle_events(carol.fingerprint, [genesis.to_dict()]) == [genesis.content_id]
    assert bob.communities[genesis.payload["community_id"]].content_id == genesis.content_id
    assert bob.handle_events(alice.fingerprint, [genesis.to_dict()]) == []
    alice.close()
    carol.close()


def test_a_genesis_whose_id_does_not_match_is_refused(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    bob = _bob_knowing(tmp_path, clock, alice)
    genesis = _genesis(alice.identity)
    raw = genesis.to_dict()
    raw["envelope"]["payload"]["community_id"] = "f" * 64
    with pytest.raises(LinkProtocolError, match="does not match"):
        bob.handle_events(alice.fingerprint, [raw])
    alice.close()


def test_a_genesis_signed_by_another_node_is_refused(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    mallory = spawn_node(tmp_path, "mallory")
    bob = _bob_knowing(tmp_path, clock, alice)
    bob.handle_hello(_hello_bytes(LinkNode(identity=mallory.identity), clock=clock))
    forged = build_community_genesis(
        signing_identity=mallory.identity.signing_key, origin_fingerprint=alice.fingerprint,
        name="Retro Computing", created_at="2026-03-01T00:00:00.000000Z",
    )
    with pytest.raises(LinkProtocolError, match="does not verify"):
        bob.handle_events(mallory.fingerprint, [forged.to_dict()])
    alice.close()
    mallory.close()


def test_a_membership_list_waits_for_its_genesis_and_must_come_from_the_origin(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    mallory = spawn_node(tmp_path, "mallory")
    bob = _bob_knowing(tmp_path, clock, alice)
    bob.handle_hello(_hello_bytes(LinkNode(identity=mallory.identity), clock=clock))
    genesis = _genesis(alice.identity)
    members = _members(alice.identity, genesis, boards=["b1"])

    with pytest.raises(MissingDependency):
        bob.handle_events(alice.fingerprint, [members.to_dict()])
    bob.handle_events(alice.fingerprint, [genesis.to_dict()])
    assert bob.handle_events(alice.fingerprint, [members.to_dict()]) == [members.content_id]

    not_the_origin = build_community_members(
        signing_identity=mallory.identity.signing_key, origin_fingerprint=mallory.fingerprint,
        community_id=genesis.payload["community_id"],
        members={"boards": ["evil"], "channels": [], "file_areas": []}, created_at="2026-04-01T00:00:00Z",
    )
    with pytest.raises(LinkProtocolError, match="origin other than"):
        bob.handle_events(mallory.fingerprint, [not_the_origin.to_dict()])
    alice.close()
    mallory.close()


def test_a_malformed_membership_list_is_refused(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    bob = _bob_knowing(tmp_path, clock, alice)
    genesis = _genesis(alice.identity)
    bob.handle_events(alice.fingerprint, [genesis.to_dict()])
    from netbbs.link.events import COMMUNITY_MEMBERS_OBJECT_TYPE, CommunityMembers, build_envelope, canonical_bytes

    envelope = build_envelope(COMMUNITY_MEMBERS_OBJECT_TYPE, {
        "community_id": genesis.payload["community_id"], "origin_fingerprint": alice.fingerprint,
        "members": {"boards": [f"b{i}" for i in range(501)], "channels": [], "file_areas": []},
        "created_at": "2026-04-01T00:00:00Z", "nonce": "0" * 32,
    })
    too_many = CommunityMembers(envelope=envelope, signature=alice.identity.signing_key.sign(canonical_bytes(envelope)))
    with pytest.raises(LinkProtocolError, match="more than 500"):
        bob.handle_events(alice.fingerprint, [too_many.to_dict()])
    alice.close()


def test_a_closure_is_terminal(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    bob = _bob_knowing(tmp_path, clock, alice)
    genesis = _genesis(alice.identity)
    community_id = genesis.payload["community_id"]
    closure = build_community_closure(
        signing_identity=alice.identity.signing_key, origin_fingerprint=alice.fingerprint,
        community_id=community_id, created_at="2026-04-01T00:00:00Z",
    )
    bob.handle_events(alice.fingerprint, [genesis.to_dict()])
    assert bob.handle_events(alice.fingerprint, [closure.to_dict()]) == [closure.content_id]
    later = _members(alice.identity, genesis, boards=["b1"], created_at="2026-05-01T00:00:00Z")
    assert bob.handle_events(alice.fingerprint, [later.to_dict()]) == []  # nothing in force any more
    another = build_community_closure(
        signing_identity=alice.identity.signing_key, origin_fingerprint=alice.fingerprint,
        community_id=community_id, created_at="2026-04-02T00:00:00Z",
    )
    with pytest.raises(LinkProtocolError, match="already closed"):
        bob.handle_events(alice.fingerprint, [another.to_dict()])
    alice.close()


# -- the inventory's fourth kind ---------------------------------------------------


def test_an_inventory_request_carries_communities_only_when_asked(db, here):
    without = build_inventory_request(
        db, signing_identity=here.signing_key, requester_fingerprint=here.fingerprint,
        responder_fingerprint="responder",
    )
    assert without.communities is None and "communities" not in without.to_dict()
    with_map = build_inventory_request(
        db, signing_identity=here.signing_key, requester_fingerprint=here.fingerprint,
        responder_fingerprint="responder", include_communities=True,
    )
    # Present even when empty: that is what says this node understands them.
    assert with_map.to_dict()["communities"] == {}
    assert InventoryRequest.from_dict(with_map.to_dict()).communities == {}


def test_a_signed_communities_map_is_verified(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    bob = LinkNode(identity=spawn_node(tmp_path, "bob").identity)
    bob.handle_hello(_hello_bytes(LinkNode(identity=alice.identity), clock=clock))
    request = build_inventory_request(
        alice.db, signing_identity=alice.identity.signing_key, requester_fingerprint=alice.fingerprint,
        responder_fingerprint=bob.identity.fingerprint, include_communities=True,
    )
    bob.handle_inventory_request(alice.fingerprint, InventoryRequest.from_dict(request.to_dict()))
    tampered = request.to_dict()
    tampered["communities"] = {"x" * 64: []}
    tampered["nonce"] = "1" * 32
    with pytest.raises(LinkProtocolError, match="does not verify"):
        bob.handle_inventory_request(alice.fingerprint, InventoryRequest.from_dict(tampered))
    alice.close()


def test_the_origin_serves_its_community_and_a_newcomer_is_sent_it_whole(db, sysop, here):
    community = create_community(db, "Retro", creator=sysop)
    events = promote_community(db, community.id, node_identity=here)
    link_id = events[0]["envelope"]["payload"]["community_id"]

    assert served_community_ids(db) == [link_id]
    sent, more = community_event_diff(db, {}, limit=200)
    assert {event_content_id(e["envelope"]) for e in sent} == {event_content_id(e["envelope"]) for e in events}
    assert not more
    # Declared as held, nothing is sent; declared as not carried, nothing either.
    held = {link_id: [event_content_id(e["envelope"]) for e in events]}
    assert community_event_diff(db, held, limit=200) == ([], False)
    assert community_event_diff(db, {}, limit=200, not_carried=(link_id,)) == ([], False)
    # A peer that never saw it wants what an origin declares.
    other = Database(db.path.parent / "other.db")
    try:
        assert set(community_wanted_ids(other, held)) == set(held[link_id])
    finally:
        other.close()


def test_an_offered_community_is_declared_not_carried(db, origin, here):
    genesis = _genesis(origin)
    _offer(db, origin, genesis)
    assert uncarried_resource_ids(db)["communities"] == (genesis.payload["community_id"],)
    request = build_inventory_request(
        db, signing_identity=here.signing_key, requester_fingerprint=here.fingerprint,
        responder_fingerprint="r", declare_not_carried=True, include_communities=True,
    )
    assert request.not_carried["communities"] == (genesis.payload["community_id"],)
    # Never named to a responder that does not understand the kind.
    older = build_inventory_request(
        db, signing_identity=here.signing_key, requester_fingerprint=here.fingerprint,
        responder_fingerprint="r", declare_not_carried=True,
    )
    assert "communities" not in older.not_carried


# -- at the origin -----------------------------------------------------------------


def _own_board(db, sysop, here, name, community=None, *, link=True):
    board = create_board(db, name, creator=sysop, community_id=community.id if community else None)
    if link:
        link_board(db, board, node_identity=here)
    return board


def test_promotion_announces_only_members_already_linked_from_here(db, sysop, here, origin):
    community = create_community(db, "Retro", description="Old machines", default_min_read_level=5, creator=sysop)
    linked = _own_board(db, sysop, here, "linked", community)
    _own_board(db, sysop, here, "private", community, link=False)
    # A carried board filed here is not this node's to announce.
    carried = materialize_carried_board(db, _board_genesis(origin, "c" * 64, "carried"))
    db.connection.execute("UPDATE boards SET community_id = ? WHERE id = ?", (community.id, carried.id))
    db.connection.commit()
    area = create_file_area(db, "files", creator=sysop, community_id=community.id)
    link_file_area(db, area, node_identity=here)
    channel = create_channel(db, "chat", creator=sysop, community_id=community.id)
    link_channel(db, channel, node_identity=here)

    genesis_raw, members_raw = promote_community(db, community.id, node_identity=here)

    payload = genesis_raw["envelope"]["payload"]
    assert payload["name"] == "Retro" and payload["description"] == "Old machines"
    assert payload["default_min_read_level"] == 5 and "default_min_write_level" not in payload
    assert members_raw["envelope"]["payload"]["members"] == {
        "boards": [linked.board_id], "channels": [channel.channel_id], "file_areas": [area.area_id],
    }
    assert get_board_by_name(db, "private") is not None  # promotion links nothing
    assert db.connection.execute(
        "SELECT link_genesis_json FROM boards WHERE name = 'private'"
    ).fetchone()[0] is None
    link = community_link(db, community.id, here.fingerprint)
    assert link.own and not link.closed and link.member_count == 3
    assert community.id in linked_community_ids(db)
    with pytest.raises(LinkCommunitiesError, match="already Linked"):
        promote_community(db, community.id, node_identity=here)


def test_linking_or_moving_a_member_signs_a_new_list_and_nothing_else_does(db, sysop, here):
    community = create_community(db, "Retro", creator=sysop)
    promote_community(db, community.id, node_identity=here)
    assert refresh_own_community_members(db, here) == []

    board = _own_board(db, sysop, here, "newly", community)
    first = refresh_own_community_members(db, here)
    assert [r["envelope"]["payload"]["members"]["boards"] for r in first] == [[board.board_id]]
    assert refresh_own_community_members(db, here) == []

    update_board(
        db, board, name=board.name, description=None, min_read_level=0, min_write_level=0, category_id=None,
        pinned=False, moderated=False, max_post_age_days=None, min_age=None, name_requirement=None,
        community_id=None, allow_color=False, changed_by=sysop,
    )
    second = refresh_own_community_members(db, here)
    assert second[0]["envelope"]["payload"]["members"]["boards"] == []
    assert second[0]["envelope"]["payload"]["created_at"] > first[0]["envelope"]["payload"]["created_at"]


def test_a_member_whose_origin_moved_away_drops_out_of_the_list(db, sysop, here, origin):
    community = create_community(db, "Retro", creator=sysop)
    board = _own_board(db, sysop, here, "moving", community)
    promote_community(db, community.id, node_identity=here)
    record_board_origin_change(db, board.board_id, origin.fingerprint)
    (latest,) = refresh_own_community_members(db, here)
    assert latest["envelope"]["payload"]["members"]["boards"] == []


def test_unlinking_closes_it_and_the_closure_keeps_travelling(db, sysop, here):
    community = create_community(db, "Retro", creator=sysop)
    genesis_raw, _members_raw = promote_community(db, community.id, node_identity=here)
    link_id = genesis_raw["envelope"]["payload"]["community_id"]

    closure = close_own_community(db, community.id, node_identity=here, actor=sysop)

    assert closure["envelope"]["payload"]["community_id"] == link_id
    assert community_link(db, community.id, here.fingerprint) is None  # plain local again
    assert served_community_ids(db) == [link_id]
    own = {e.content_id for e in load_own_community_events(db, here.fingerprint)}
    assert event_content_id(closure["envelope"]) in own and event_content_id(genesis_raw["envelope"]) in own
    assert close_own_community(db, community.id, node_identity=here) is None
    # It can be promoted again, as a new Link Community.
    again = promote_community(db, community.id, node_identity=here)
    assert again[0]["envelope"]["payload"]["community_id"] != link_id


def test_a_restarted_node_remembers_its_own_and_received_communities(tmp_path, sysop, db, here, origin):
    community = create_community(db, "Retro", creator=sysop)
    own_genesis, own_members = promote_community(db, community.id, node_identity=here)
    received = _genesis(origin)
    _offer(db, origin, received)

    node = load_link_node(db, here)

    assert set(node.communities) == {
        own_genesis["envelope"]["payload"]["community_id"], received.payload["community_id"],
    }
    assert event_content_id(own_members["envelope"]) in node.known_event_ids


# -- at a carrier ----------------------------------------------------------------


def test_a_community_is_offered_never_carried_on_arrival(db, origin):
    genesis = _genesis(origin)
    assert _offer(db, origin, genesis) == "offered"
    assert carry_decision_state(db, "communities", genesis.payload["community_id"]) == OFFERED
    assert list_communities(db) == []
    with pytest.raises(CarryDecisionError):
        accept_offer(db, "communities", genesis.payload["community_id"], actor=None)


def test_the_offered_list_shows_origin_and_member_count(db, origin, here):
    genesis = _genesis(origin)
    _offer(db, origin, genesis)
    _receive_members(db, origin, here, _members(origin, genesis, boards=["b1", "b2"], channels=["c1"]))
    (decision,) = list_carry_decisions(db, OFFERED)
    assert decision.kind == "communities" and decision.name == "Retro Computing"
    assert decision.origin_fingerprint == origin.fingerprint and decision.member_count == 3


def test_subscribing_creates_the_copy_and_takes_its_members_in(db, origin, here, sysop):
    genesis = _genesis(origin, default_min_read_level=20, description="Old machines")
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    # Already carried with no Community: moves in. Offered past the cap: accepted.
    _carry_board(db, origin, here, "a" * 64, "alpha")
    assert _carry_board(db, origin, here, "b" * 64, "beta", cap=0) == "cap"
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64, "b" * 64]))

    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))

    copy = get_community(db, local_id)
    assert copy.name == "Retro Computing" and copy.default_min_read_level == 20
    assert carry_decision_state(db, "communities", link_id) is None
    alpha, beta = _board_row(db, "a" * 64), _board_row(db, "b" * 64)
    assert alpha["community_id"] == local_id and beta["community_id"] == local_id
    # No level of its own any more: it inherits the Community's.
    board = get_board_by_name(db, "alpha")
    assert board.min_read_level is None and get_effective_min_read_level(db, board) == 20
    link = community_link(db, local_id, here.fingerprint)
    assert not link.own and link.origin_fingerprint == origin.fingerprint


def test_a_member_past_the_cap_waits_as_cap(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "a" * 64, "alpha")
    assert _carry_board(db, origin, here, "b" * 64, "beta", cap=0) == "cap"
    db.connection.execute("UPDATE link_carry_decisions SET reason = 'migrated' WHERE resource_id = ?", ("b" * 64,))
    db.connection.commit()
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64, "b" * 64]))

    subscribe_community(db, link_id, actor=sysop, caps=_caps(here, boards=1))

    assert carry_decision_state(db, "boards", "b" * 64) == OFFERED
    assert db.connection.execute(
        "SELECT reason FROM link_carry_decisions WHERE resource_id = ?", ("b" * 64,)
    ).fetchone()[0] == "cap"


def test_an_offer_already_put_to_the_sysop_is_not_taken_by_a_later_list(db, origin, here, sysop):
    """#561 Decision 4: only members new to a list are accepted."""
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    assert _carry_board(db, origin, here, "b" * 64, "beta", cap=0) == "cap"
    first = _members(origin, genesis, boards=["b" * 64])
    _receive_members(db, origin, here, first)
    subscribe_community(db, link_id, actor=sysop, caps=_caps(here, boards=0))
    assert carry_decision_state(db, "boards", "b" * 64) == OFFERED

    # A later list still naming it, with room now: it stays the SysOp's.
    _receive_members(db, origin, here, _members(origin, genesis, boards=["b" * 64], created_at="2026-03-05T00:00:00Z"))
    assert carry_decision_state(db, "boards", "b" * 64) == OFFERED
    # One new to the list is accepted.
    assert _carry_board(db, origin, here, "c" * 64, "gamma", cap=0) == "cap"
    _receive_members(db, origin, here, _members(
        origin, genesis, boards=["b" * 64, "c" * 64], created_at="2026-03-06T00:00:00Z"
    ))
    assert carry_decision_state(db, "boards", "c" * 64) is None
    assert _board_row(db, "c" * 64) is not None


def test_a_member_genesis_arriving_later_lands_in_the_copy(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64]))
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert _board_row(db, "a" * 64) is None  # waits for its genesis

    from netbbs.link.communities import place_listed_member

    _carry_board(db, origin, here, "a" * 64, "alpha")
    place_listed_member(db, "boards", "a" * 64)  # what the transport does after a carried genesis
    assert _board_row(db, "a" * 64)["community_id"] == local_id


def test_a_sysop_placement_wins_over_every_later_list(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    mine = create_community(db, "Mine", creator=sysop)
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "a" * 64, "alpha")
    _carry_board(db, origin, here, "b" * 64, "beta")
    # Filed in a local Community before subscribing: placed by the SysOp.
    db.connection.execute("UPDATE boards SET community_id = ? WHERE board_id = ?", (mine.id, "b" * 64))
    db.connection.commit()
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64, "b" * 64]))
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert _board_row(db, "b" * 64)["community_id"] == mine.id

    # The SysOp moves alpha out of the copy: an override.
    alpha = get_board_by_name(db, "alpha")
    update_board(
        db, alpha, name=alpha.name, description=None, min_read_level=0, min_write_level=0, category_id=None,
        pinned=False, moderated=False, max_post_age_days=None, min_age=None, name_requirement=None,
        community_id=None, allow_color=False, changed_by=sysop,
    )
    assert _board_row(db, "a" * 64)["community_placed_locally"] == 1
    _receive_members(db, origin, here, _members(
        origin, genesis, boards=["a" * 64, "b" * 64], created_at="2026-03-07T00:00:00Z",
    ))
    assert _board_row(db, "a" * 64)["community_id"] is None
    assert local_id != mine.id


def test_a_local_resource_filed_in_the_copy_stays_there(db, origin, here, sysop):
    """Review of #1336: a board created straight into a subscribed copy was
    taken out again by the next membership list."""
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    created = create_board(db, "ours", creator=sysop, community_id=local_id)
    create_channel(db, "ours-chat", creator=sysop, community_id=local_id)
    create_file_area(db, "ours-files", creator=sysop, community_id=local_id)
    # Even without the flag (a row from before it), a local resource is not a list's.
    db.connection.execute("UPDATE file_areas SET community_placed_locally = 0")
    db.connection.commit()

    _receive_members(db, origin, here, _members(origin, genesis, created_at="2026-03-08T00:00:00Z"))

    assert get_board_by_name(db, "ours").community_id == local_id == created.community_id
    for table in ("channels", "file_areas"):
        assert db.connection.execute(f"SELECT community_id FROM {table}").fetchone()[0] == local_id


def test_a_member_the_origin_removes_moves_out_and_stays_carried(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "a" * 64, "alpha")
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64]))
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert _board_row(db, "a" * 64)["community_id"] == local_id

    _receive_members(db, origin, here, _members(origin, genesis, created_at="2026-03-08T00:00:00Z"))

    row = _board_row(db, "a" * 64)
    assert row is not None and row["community_id"] is None and row["link_hidden_at"] is None


def test_an_older_list_never_replaces_a_newer_one(db, origin, here):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    newer = _members(origin, genesis, boards=["n"], created_at="2026-03-09T00:00:00Z")
    older = _members(origin, genesis, boards=["o"], created_at="2026-03-08T00:00:00Z")
    assert _receive_members(db, origin, here, newer) == "stored"
    assert _receive_members(db, origin, here, older) == "stale"
    assert members_in_force(db, link_id)["boards"] == ("n",)
    # Superseded lists are not kept.
    assert db.connection.execute(
        "SELECT COUNT(*) FROM link_events WHERE community_id = ? AND object_type = 'community_members'", (link_id,)
    ).fetchone()[0] == 1


def test_a_member_whose_genesis_names_another_origin_is_ignored(db, origin, here, sysop):
    third = bootstrap_node_identity("third")
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    foreign = _board_genesis(third, "f" * 64, "foreign")
    accept_genesis(
        db, kind="boards", envelope=foreign.to_dict(), sender_fingerprint=third.fingerprint,
        content_id=foreign.content_id, own_fingerprint=here.fingerprint, cap=0,
    )
    _carry_board(db, origin, here, "a" * 64, "alpha")
    db.connection.execute("UPDATE link_carry_decisions SET reason = 'migrated'")
    db.connection.commit()
    _receive_members(db, origin, here, _members(origin, genesis, boards=["f" * 64]))
    subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert carry_decision_state(db, "boards", "f" * 64) == OFFERED


def test_an_excluded_member_stays_out(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "b" * 64, "beta", cap=0)
    exclude_offer(db, "boards", "b" * 64, actor=sysop)
    _receive_members(db, origin, here, _members(origin, genesis, boards=["b" * 64]))
    subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert carry_decision_state(db, "boards", "b" * 64) == EXCLUDED


def test_a_name_clash_with_a_local_community_gets_a_suffix(db, origin, here, sysop):
    create_community(db, "Retro Computing", creator=sysop)
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert get_community(db, local_id).name == f"Retro Computing-{link_id[:8]}"


def test_an_edit_to_the_copys_defaults_is_local(db, origin, here, sysop):
    genesis = _genesis(origin, default_min_read_level=20)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    copy = get_community(db, local_id)
    update_community(
        db, copy, name=copy.name, description=None, hidden=False, default_min_read_level=0,
        default_min_write_level=None, default_min_age=None, default_name_requirement=None, changed_by=sysop,
    )
    _receive_members(db, origin, here, _members(origin, genesis))
    assert get_community(db, local_id).default_min_read_level == 0


def test_leaving_accepts_nothing_more_and_sheds_nothing(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "a" * 64, "alpha")
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64]))
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))

    leave_community(db, local_id, actor=sysop, delete=lambda: delete_community(db, get_community(db, local_id), deleted_by=sysop))

    assert carry_decision_state(db, "communities", link_id) == EXCLUDED
    assert _board_row(db, "a" * 64) is not None
    _carry_board(db, origin, here, "b" * 64, "beta", cap=0)
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64, "b" * 64], created_at="2026-04-01T00:00:00Z"))
    assert carry_decision_state(db, "boards", "b" * 64) == OFFERED
    # Restore is subscribing again.
    again = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    assert _board_row(db, "a" * 64)["community_id"] == again


def test_a_closure_makes_the_copy_a_plain_local_community(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    _carry_board(db, origin, here, "a" * 64, "alpha")
    _receive_members(db, origin, here, _members(origin, genesis, boards=["a" * 64]))
    local_id = subscribe_community(db, link_id, actor=sysop, caps=_caps(here))
    closure = build_community_closure(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, community_id=link_id,
        created_at="2026-04-01T00:00:00Z",
    )

    store_received_closure(db, envelope=closure.to_dict(), sender_fingerprint=origin.fingerprint, content_id=closure.content_id)

    link = community_link(db, local_id, here.fingerprint)
    assert link.closed and local_id not in linked_community_ids(db)
    assert _board_row(db, "a" * 64)["community_id"] == local_id  # members kept, where they are
    # The SysOp may delete it, with nothing to record.
    leave_community(db, local_id, actor=sysop, delete=lambda: delete_community(db, get_community(db, local_id), deleted_by=sysop))
    assert carry_decision_state(db, "communities", link_id) is None


def test_a_closure_withdraws_an_offer(db, origin, here, sysop):
    genesis = _genesis(origin)
    link_id = genesis.payload["community_id"]
    _offer(db, origin, genesis)
    closure = build_community_closure(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, community_id=link_id,
        created_at="2026-04-01T00:00:00Z",
    )
    store_received_closure(db, envelope=closure.to_dict(), sender_fingerprint=origin.fingerprint, content_id=closure.content_id)
    assert carry_decision_state(db, "communities", link_id) is None
    assert link_id in uncarried_resource_ids(db)["communities"]


def test_a_communities_decision_survives_the_migration_rebuild(tmp_path, monkeypatch):
    """The carry-decision table is rebuilt to take the new kind; rows from
    before are copied, and the old kinds still work."""
    from netbbs.storage import database as database_module
    from netbbs.storage.migrations import MIGRATIONS

    index = next(i for i, m in enumerate(MIGRATIONS) if "link_carry_decisions_1310" in m.sql)
    monkeypatch.setattr(database_module, "MIGRATIONS", MIGRATIONS[:index])
    database = Database(tmp_path / "pre.db")
    monkeypatch.undo()
    database.connection.execute(
        "INSERT INTO link_carry_decisions (kind, resource_id, state, reason, decided_at) "
        "VALUES ('boards', 'x', 'offered', 'cap', '2026-01-01T00:00:00Z')"
    )
    database.connection.commit()
    database.close()
    migrated = Database(tmp_path / "pre.db")
    try:
        assert carry_decision_state(migrated, "boards", "x") == OFFERED
        migrated.connection.execute(
            "INSERT INTO link_carry_decisions (kind, resource_id, state, reason, decided_at) "
            "VALUES ('communities', 'y', 'offered', 'community', '2026-01-01T00:00:00Z')"
        )
        assert migrated.connection.execute(
            "SELECT community_placed_locally FROM boards LIMIT 0"
        ).fetchall() == []
    finally:
        migrated.close()


def test_a_genesis_row_keeps_its_json_shape(db, origin):
    genesis = _genesis(origin)
    _offer(db, origin, genesis)
    stored = db.connection.execute(
        "SELECT object_type, community_id, envelope_json FROM link_events WHERE community_id IS NOT NULL"
    ).fetchone()
    assert stored["object_type"] == COMMUNITY_GENESIS_OBJECT_TYPE
    assert json.loads(stored["envelope_json"]) == genesis.to_dict()
