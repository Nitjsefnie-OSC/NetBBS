"""
Link moderation's roster (design doc §16 issue #1357, Decision 1; built in
issue #1359): the signed `link_moderators` event, its shape and acceptance
rules, the effective-time ordering with its future clamp (Decision 8), the
origin's own grants, what a roster grants, and how it travels with its
origin's boards and channels -- only to peers that understand it.
"""

from __future__ import annotations

import pytest

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards.boards import create_board
from netbbs.chat.channels import create_channel
from netbbs.link.boards import link_board
from netbbs.link.channels import link_channel
from netbbs.link.events import (
    LINK_CAPABILITIES,
    LINK_MODERATION_CAPABILITY,
    MAX_LINK_MODERATORS,
    EventError,
    build_link_moderators,
    event_content_id,
    link_moderators_problem,
)
from netbbs.link.moderators import (
    LinkModeratorsError,
    _delegated_permissions as delegated_permissions,
    effective_time,
    grant_link_moderator,
    grants_held_here,
    list_own_grants,
    load_own_roster,
    revoke_link_moderator,
    _roster_entries as roster_entries,
    roster_in_force,
    store_received_roster,
)
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode, LinkProtocolError, MissingDependency
from netbbs.link.store import (
    _all_board_events,
    _all_channel_events,
    board_event_diff,
    build_inventory_request,
    load_link_node,
)
from netbbs.moderation.log import list_recent_actions
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
def here():
    return bootstrap_node_identity("here")


def _who(fingerprint, user="wren"):
    return {"kind": "node_vouched_user", "home_node_fingerprint": fingerprint, "local_user_id": user}


def _entry(fingerprint, scope, permissions, user="wren"):
    return {"moderator": _who(fingerprint, user), "scope": scope, "permissions": permissions}


def _roster(identity, entries, created_at="2026-10-01T00:00:00.000000Z"):
    return build_link_moderators(
        signing_identity=identity.signing_key, origin_fingerprint=identity.fingerprint, entries=entries,
        created_at=created_at,
    )


# -- the event ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ([_entry("f" * 64, {"kind": "board", "id": "b"}, ["moderate"])], "permissions"),
        ([_entry("f" * 64, {"kind": "board", "id": "b"}, ["edit", "delete"])], "sorted"),
        ([_entry("f" * 64, {"kind": "door", "id": "d"}, ["edit"])], "scope"),
        ([_entry("f" * 64, "origin", [])], "non-empty"),
        ([_entry("f" * 64, "origin", ["edit"]), _entry("f" * 64, "origin", ["delete"])], "twice"),
        ([{"moderator": {"kind": "node_signed_user"}, "scope": "origin", "permissions": ["edit"]}], "node_vouched_user"),
    ],
)
def test_a_malformed_roster_is_refused(entries, problem):
    assert problem in link_moderators_problem(entries)


def test_good_rosters_take_every_scope():
    entries = [
        _entry("f" * 64, {"kind": "board", "id": "b"}, ["delete", "edit"]),
        _entry("f" * 64, {"kind": "channel", "id": "c"}, ["moderate"]),
        _entry("f" * 64, {"community": "x"}, ["delete", "edit", "moderate", "topic"]),
        _entry("e" * 64, "origin", ["topic"]),
    ]
    assert link_moderators_problem(entries) is None


def test_a_roster_is_bounded(here):
    entries = [_entry("f" * 64, "origin", ["edit"], user=f"u{i}") for i in range(MAX_LINK_MODERATORS + 1)]
    with pytest.raises(EventError, match="more than"):
        _roster(here, entries)


def test_the_capability_is_advertised():
    assert LINK_MODERATION_CAPABILITY in LINK_CAPABILITIES


# -- effective time (Decision 8) ----------------------------------------------------


def test_a_future_dated_roster_counts_from_its_arrival():
    arrived = "2026-10-17T12:00:00.000000Z"
    assert effective_time("2026-10-17T12:04:00.000000Z", arrived) == "2026-10-17T12:04:00.000000Z"
    assert effective_time("2027-10-17T12:00:00.000000Z", arrived) == arrived
    assert effective_time("not a time", arrived) == arrived


def test_the_newest_roster_wins_and_a_future_date_cannot_pin_one(db, here):
    first = _roster(here, [_entry("f" * 64, "origin", ["edit"])], created_at="2026-10-01T00:00:00.000000Z")
    future = _roster(here, [_entry("f" * 64, "origin", ["delete"])], created_at="2030-01-01T00:00:00.000000Z")
    honest = _roster(here, [], created_at="2026-10-17T12:10:00.000000Z")
    stored = lambda event, at: store_received_roster(  # noqa: E731
        db, envelope=event.to_dict(), content_id=event.content_id, arrived_at=at,
    )
    assert stored(first, "2026-10-01T00:00:01.000000Z") == "stored"
    assert stored(first, "2026-10-01T00:00:02.000000Z") == "stale"
    assert stored(future, "2026-10-17T12:00:00.000000Z") == "stored"  # counts from 12:00
    assert stored(honest, "2026-10-17T12:10:01.000000Z") == "stored"  # so this one still wins
    assert roster_entries(db, here.fingerprint) == []
    older = _roster(here, [_entry("f" * 64, "origin", ["topic"])], created_at="2026-10-02T00:00:00.000000Z")
    assert stored(older, "2026-10-17T13:00:00.000000Z") == "stale"


# -- what a roster grants --------------------------------------------------------


def test_delegated_permissions_follow_the_scope():
    fp = "f" * 64
    entries = [
        _entry(fp, {"kind": "board", "id": "b1"}, ["delete"]),
        _entry(fp, {"community": "c1"}, ["edit", "moderate"]),
        _entry(fp, "origin", ["topic"], user="other"),
    ]
    ask = lambda **kw: delegated_permissions(entries, home_node_fingerprint=fp, local_user_id="wren", **kw)  # noqa: E731
    assert ask(resource_kind="board", resource_id="b1") == {"delete"}
    assert ask(resource_kind="board", resource_id="b2") == set()
    assert ask(resource_kind="board", resource_id="b2", community_ids={"c1"}) == {"edit"}
    assert ask(resource_kind="channel", resource_id="ch", community_ids={"c1"}) == {"moderate"}
    assert delegated_permissions(
        entries, home_node_fingerprint=fp, local_user_id="other", resource_kind="channel", resource_id="any",
    ) == {"topic"}
    assert delegated_permissions(
        entries, home_node_fingerprint=fp, local_user_id="other", resource_kind="board", resource_id="any",
    ) == set()


# -- the origin's own grants ---------------------------------------------------


def test_granting_signs_a_roster_and_is_audited(db, sysop, here):
    other = "e" * 64
    raw = grant_link_moderator(
        db, here, home_node_fingerprint=other, local_user_id="wren", scope_kind="origin", scope_id=None,
        actor=sysop, label="Everything",
    )
    assert raw == roster_in_force(db, here.fingerprint)
    [entry] = roster_entries(db, here.fingerprint)
    assert entry["moderator"]["local_user_id"] == "wren"
    assert entry["permissions"] == ["delete", "edit", "moderate", "topic"]
    assert load_own_roster(db, here.fingerprint)[0].to_dict() == raw
    [grant] = list_own_grants(db)
    second = revoke_link_moderator(db, here, grant.id, actor=sysop, label="Everything")
    assert roster_entries(db, here.fingerprint) == []
    assert second["envelope"]["payload"]["created_at"] > raw["envelope"]["payload"]["created_at"]
    actions = [entry.action for entry in list_recent_actions(db)]
    assert "grant_link_moderator" in actions and "revoke_link_moderator" in actions


def test_a_grant_to_this_nodes_own_user_is_refused(db, sysop, here):
    with pytest.raises(LinkModeratorsError, match="ordinary moderator grant"):
        grant_link_moderator(
            db, here, home_node_fingerprint=here.fingerprint, local_user_id="sysop", scope_kind="origin",
            scope_id=None, actor=sysop, label="Everything",
        )


def test_granting_twice_updates_the_one_grant(db, sysop, here):
    for permissions in (["delete", "edit"], ["delete"]):
        grant_link_moderator(
            db, here, home_node_fingerprint="e" * 64, local_user_id="wren", scope_kind="board", scope_id="b1",
            permissions=permissions, actor=sysop, label="Board",
        )
    [grant] = list_own_grants(db)
    assert grant.permissions == ("delete",)


def test_grants_held_here_lists_this_nodes_users_on_other_rosters(db, here):
    origin = bootstrap_node_identity("origin")
    event = _roster(origin, [_entry(here.fingerprint, "origin", ["edit"]), _entry("d" * 64, "origin", ["edit"])])
    store_received_roster(db, envelope=event.to_dict(), content_id=event.content_id)
    [(held_origin, entry)] = grants_held_here(db, here.fingerprint)
    assert held_origin == origin.fingerprint and entry["moderator"]["local_user_id"] == "wren"


# -- acceptance --------------------------------------------------------------------


def _bob_knowing(tmp_path, clock, *nodes):  # noqa: F811
    bob = LinkNode(identity=spawn_node(tmp_path, "bob").identity)
    for node in nodes:
        bob.handle_hello(_hello_bytes(LinkNode(identity=node.identity), clock=clock))
    return bob


def test_a_roster_from_its_origin_or_relayed_is_accepted(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    carol = spawn_node(tmp_path, "carol")
    bob = _bob_knowing(tmp_path, clock, alice, carol)
    event = _roster(alice.identity, [_entry(carol.fingerprint, "origin", ["edit"])])
    assert bob.handle_events(carol.fingerprint, [event.to_dict()]) == [event.content_id]  # relayed by carol
    assert bob.handle_events(alice.fingerprint, [event.to_dict()]) == []  # already known
    alice.close()
    carol.close()


def test_a_roster_of_an_unknown_origin_waits_and_a_forged_one_is_refused(tmp_path, clock):  # noqa: F811
    alice = spawn_node(tmp_path, "alice")
    stranger = spawn_node(tmp_path, "stranger")
    mallory = spawn_node(tmp_path, "mallory")
    bob = _bob_knowing(tmp_path, clock, alice, mallory)
    with pytest.raises(MissingDependency):
        bob.handle_events(alice.fingerprint, [_roster(stranger.identity, []).to_dict()])
    forged = build_link_moderators(
        signing_identity=mallory.identity.signing_key, origin_fingerprint=alice.fingerprint,
        entries=[_entry(mallory.fingerprint, "origin", ["delete"])], created_at="2026-10-01T00:00:00Z",
    )
    with pytest.raises(LinkProtocolError, match="does not verify"):
        bob.handle_events(mallory.fingerprint, [forged.to_dict()])
    for node in (alice, stranger, mallory):
        node.close()


# -- travelling with the origin's resources ---------------------------------


def test_a_linked_board_and_channel_carry_their_origins_roster(db, sysop, here):
    board = create_board(db, "Retro", creator=sysop)
    link_board(db, board, node_identity=here)
    channel = create_channel(db, "lobby", creator=sysop)
    link_channel(db, channel, node_identity=here)
    raw = grant_link_moderator(
        db, here, home_node_fingerprint="e" * 64, local_user_id="wren", scope_kind="origin", scope_id=None,
        actor=sysop, label="Everything",
    )
    content_id = event_content_id(raw["envelope"])
    assert _all_board_events(db, board.board_id)[content_id] == raw
    assert content_id in _all_channel_events(db, channel.channel_id)

    served, _ = board_event_diff(db, {}, limit=100)
    assert raw in served
    withheld, _ = board_event_diff(db, {}, limit=100, rosters=False)
    assert raw not in withheld

    request = lambda **kw: build_inventory_request(  # noqa: E731
        db, signing_identity=here.signing_key, requester_fingerprint=here.fingerprint,
        responder_fingerprint="responder", **kw,
    )
    assert content_id in request(include_moderators=True).boards[board.board_id]
    assert content_id not in request().boards[board.board_id]


def test_a_restarted_node_remembers_the_rosters_it_holds(db, sysop, here):
    raw = grant_link_moderator(
        db, here, home_node_fingerprint="e" * 64, local_user_id="wren", scope_kind="origin", scope_id=None,
        actor=sysop, label="Everything",
    )
    node = load_link_node(db, here)
    assert any(event == raw for event in node.events.values())


# -- the console screen ------------------------------------------------------------


def test_the_screen_adds_and_removes_a_link_moderator(db, sysop, here, monkeypatch):
    import asyncio

    from netbbs.link.boards import LinkContext
    from netbbs.net import admin_link_moderators as screen
    from netbbs.net import mail_flow
    from netbbs.storage.execution import DatabaseLane
    from tests.test_admin_flow import FakeSession, _written_text

    other = "e" * 64
    monkeypatch.setattr(mail_flow, "_resolve_link_address", lambda db, text: mail_flow._LinkRecipient(user="wren", fingerprint=other))

    async def first(session, items, **kwargs):
        return items[0]

    monkeypatch.setattr(screen, "pick_item", first)
    lane = DatabaseLane(db.path)
    node = LinkNode(identity=here)
    try:
        session = FakeSession(["a", "wren@Elsewhere", "r", "y", "b"])
        asyncio.run(screen.link_moderators_screen(session, lane, sysop, link_context=LinkContext(link_node=node)))
    finally:
        lane.close()
    from netbbs.rendering import strip_ansi

    text = " ".join(strip_ansi(_written_text(session)).split())
    assert "may now remove posts, edit posts, mute and ban, set the topic on Everything this node originates" in text
    assert "no longer moderates Everything this node originates" in text
    assert list_own_grants(db) == []
    held = roster_in_force(db, here.fingerprint)
    assert held is not None and event_content_id(held["envelope"]) in node.known_event_ids


def test_the_screen_lists_what_this_nodes_users_hold_elsewhere(db, sysop, here):
    import asyncio

    from netbbs.link.boards import LinkContext
    from netbbs.net.admin_link_moderators import link_moderators_screen
    from netbbs.rendering import strip_ansi
    from netbbs.storage.execution import DatabaseLane
    from tests.test_admin_flow import FakeSession, _written_text

    origin = bootstrap_node_identity("origin")
    event = _roster(origin, [_entry(here.fingerprint, "origin", ["delete", "edit"], user="sysop")])
    store_received_roster(db, envelope=event.to_dict(), content_id=event.content_id)
    lane = DatabaseLane(db.path)
    try:
        session = FakeSession(["b"])
        asyncio.run(link_moderators_screen(session, lane, sysop, link_context=LinkContext(link_node=LinkNode(identity=here))))
    finally:
        lane.close()
    text = " ".join(strip_ansi(_written_text(session)).split())
    assert "held by this node's users" in text.lower() and "remove posts, edit posts" in text
