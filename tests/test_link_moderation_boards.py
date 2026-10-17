"""
Delegated Link moderation of linked boards (design doc §16 issue #1357,
Decision 2; issue #1359 slice 2): a user of a carrying node whom the board's
origin's roster names may edit and remove posts there, the carrier signs it
for everyone with the moderator named, and every receiver checks it against
the origin's roster it holds -- revocation taking effect on receipt.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import create_user
from netbbs.boards.boards import get_board_by_name
from netbbs.link.boards import LinkContext, materialize_carried_board, materialize_carried_post
from netbbs.link.events import (
    build_board_genesis,
    build_board_post,
    build_board_post_tombstone,
    build_community_genesis,
    build_community_members,
    build_link_moderators,
    canonical_bytes,
)
from netbbs.link.communities import accept_community_genesis, store_received_members
from netbbs.link.moderators import all_roster_content_ids, holds_link_permission, store_received_roster
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode, LinkProtocolError, MissingDependency
from netbbs.identity.keys import verify_signature
from netbbs.moderation.roles import BoardPermission, grant_permissions, has_permission
from netbbs.net import board_flow
from netbbs.storage.database import Database
from tests.test_link_protocol import _hello_bytes, clock  # noqa: F401 -- clock is a fixture
from tests.test_linked_board_moderation import _Session

BOARD_ID = "b" * 64


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def here():
    return bootstrap_node_identity("roanoke")


@pytest.fixture
def origin():
    return bootstrap_node_identity("elsewhere")


def _who(fingerprint, user="mod"):
    return {"kind": "node_vouched_user", "home_node_fingerprint": fingerprint, "local_user_id": user}


def _roster(origin, entries, created_at="2026-10-01T00:00:00.000000Z"):
    return build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, entries=entries,
        created_at=created_at,
    )


def _genesis(origin):
    return build_board_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, board_id=BOARD_ID,
        name="Remote Discussion", created_at="2026-01-01T00:00:00Z",
    )


def _post(origin):
    return build_board_post(
        signing_identity=origin.signing_key, home_node_fingerprint=origin.fingerprint, local_user_id="wanderer",
        board_id=BOARD_ID, subject="Carried subject", body="first post", created_at="2026-01-01T00:00:00Z",
    )


def _carrier(db, origin, here, entries):
    materialize_carried_board(db, _genesis(origin))
    post = materialize_carried_post(db, _post(origin), sender_fingerprint=origin.fingerprint)
    event = _roster(origin, entries)
    store_received_roster(db, envelope=event.to_dict(), content_id=event.content_id, own_fingerprint=here.fingerprint)
    return get_board_by_name(db, "Remote Discussion"), post


# -- the carrier: who may moderate here ------------------------------------------


def test_a_delegated_user_holds_the_permission_on_the_carried_board(db, origin, here):
    board, _post_row = _carrier(db, origin, here, [
        {"moderator": _who(here.fingerprint), "scope": {"kind": "board", "id": BOARD_ID}, "permissions": ["delete"]},
    ])
    mod = create_user(db, "mod", password="hunter2pw")
    other = create_user(db, "other", password="hunter2pw")
    assert has_permission(db, mod, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)
    assert not has_permission(db, mod, object_type="board", object_id=board.id, permission=BoardPermission.EDIT)
    assert not has_permission(db, other, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)


def test_an_entry_naming_another_nodes_user_grants_nothing_here(db, origin, here):
    board, _post_row = _carrier(db, origin, here, [
        {"moderator": _who("e" * 64), "scope": "origin", "permissions": ["delete", "edit"]},
    ])
    mod = create_user(db, "mod", password="hunter2pw")
    assert not holds_link_permission(db, mod, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)


def test_a_community_scope_covers_the_boards_its_list_names(db, origin, here):
    community = build_community_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, name="Retro",
        created_at="2026-02-01T00:00:00Z",
    )
    community_id = community.payload["community_id"]
    board, _post_row = _carrier(db, origin, here, [
        {"moderator": _who(here.fingerprint), "scope": {"community": community_id}, "permissions": ["delete"]},
    ])
    mod = create_user(db, "mod", password="hunter2pw")
    assert not has_permission(db, mod, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)
    accept_community_genesis(db, envelope=community.to_dict(), sender_fingerprint=origin.fingerprint,
                             content_id=community.content_id)
    members = build_community_members(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, community_id=community_id,
        members={"boards": [BOARD_ID], "channels": [], "file_areas": []}, created_at="2026-02-02T00:00:00Z",
    )
    from netbbs.link.communities import CarryCaps

    store_received_members(db, envelope=members.to_dict(), sender_fingerprint=origin.fingerprint,
                           content_id=members.content_id, caps=CarryCaps(boards=None, channels=None, file_areas=None, own_fingerprint=here.fingerprint))
    assert has_permission(db, mod, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)


# -- the carrier: what a delegated removal does ------------------------------------


def test_a_delegated_take_down_is_signed_for_everyone(db, origin, here):
    board, post = _carrier(db, origin, here, [
        {"moderator": _who(here.fingerprint), "scope": {"kind": "board", "id": BOARD_ID}, "permissions": ["delete"]},
    ])
    mod = create_user(db, "mod", password="hunter2pw")
    session = _Session(["0", "1", "t", "y", "b", "b"])
    asyncio.run(board_flow._show_board(
        session, db, board, mod, link_context=LinkContext(link_node=LinkNode(identity=here)),
    ))
    text = session.visible()
    assert 'Take down "Carried subject"? This cannot be undone.' in text
    assert "keep the original" not in text
    row = db.connection.execute(
        "SELECT link_event_json FROM posts WHERE root_post_id = ? AND tombstoned_at IS NOT NULL", (post.post_id,)
    ).fetchone()
    import json

    event = json.loads(row["link_event_json"])
    assert event["envelope"]["object_type"] == "board_post_tombstone"
    assert event["envelope"]["payload"]["moderator"] == _who(here.fingerprint)
    import base64

    assert verify_signature(here.signing_key.verify_key, canonical_bytes(event["envelope"]),
                            base64.b64decode(event["signature"]))
    assert len(all_roster_content_ids(db)) == 2  # the roster and the delegated removal


def test_a_local_moderators_take_down_still_stays_on_this_node(db, origin, here):
    board, _post_row = _carrier(db, origin, here, [])
    sysop_like = create_user(db, "localmod", password="hunter2pw")
    grant_permissions(
        db, sysop_like, object_type="board", object_id=board.id, permissions=BoardPermission.DELETE,
        granted_by=create_user(db, "boss", password="hunter2pw", user_level=255),
    )
    session = _Session(["0", "1", "t", "y", "b", "b"])
    asyncio.run(board_flow._show_board(
        session, db, board, sysop_like, link_context=LinkContext(link_node=LinkNode(identity=here)),
    ))
    assert "on this node only" in session.visible()
    assert db.connection.execute(
        "SELECT COUNT(*) FROM posts WHERE link_event_json IS NOT NULL AND tombstoned_at IS NOT NULL"
    ).fetchone()[0] == 0


# -- every receiver: checking a delegated action -------------------------------


def _receiver(tmp_path, clock, origin, here):  # noqa: F811
    carol = LinkNode(identity=bootstrap_node_identity("carol"))
    for node in (origin, here):
        carol.handle_hello(_hello_bytes(LinkNode(identity=node), clock=clock))
    post = _post(origin)
    carol.handle_events(origin.fingerprint, [_genesis(origin).to_dict(), post.to_dict()])
    return carol, post


def _tombstone(here, post, moderator):
    return build_board_post_tombstone(
        signing_identity=here.signing_key, board_id=BOARD_ID, root_post_id=post.content_id,
        previous_event_id=post.content_id, subject="[removed]", body="[removed]", reason=None,
        created_at="2026-10-02T00:00:00Z", moderator=moderator,
    )


def test_a_delegated_removal_is_accepted_under_the_roster_and_refused_once_revoked(tmp_path, clock, origin, here):  # noqa: F811
    carol, post = _receiver(tmp_path, clock, origin, here)
    grant = _roster(origin, [{"moderator": _who(here.fingerprint), "scope": "origin", "permissions": ["delete"]}])
    revoke = _roster(origin, [], created_at="2026-10-03T00:00:00.000000Z")
    tombstone = _tombstone(here, post, _who(here.fingerprint))

    with pytest.raises(LinkProtocolError, match="does not let delete"):
        carol.handle_events(here.fingerprint, [tombstone.to_dict()])
    carol.handle_events(origin.fingerprint, [grant.to_dict()])
    carol.handle_events(origin.fingerprint, [revoke.to_dict()])
    # Revoked on receipt, though the removal says it was made before.
    with pytest.raises(LinkProtocolError, match="does not let delete"):
        carol.handle_events(here.fingerprint, [tombstone.to_dict()])


def test_a_delegated_removal_under_a_grant_is_accepted(tmp_path, clock, origin, here):  # noqa: F811
    carol, post = _receiver(tmp_path, clock, origin, here)
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(here.fingerprint), "scope": {"kind": "board", "id": BOARD_ID}, "permissions": ["delete"]},
    ]).to_dict()])
    tombstone = _tombstone(here, post, _who(here.fingerprint))
    # Relayed by the origin; signed by the moderator's own node.
    assert carol.handle_events(origin.fingerprint, [tombstone.to_dict()]) == [tombstone.content_id]
    assert carol.board_events.is_tombstoned(post.content_id)


def test_a_delegated_removal_signed_by_another_node_is_refused(tmp_path, clock, origin, here):  # noqa: F811
    carol, post = _receiver(tmp_path, clock, origin, here)
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(here.fingerprint), "scope": "origin", "permissions": ["delete"]},
    ]).to_dict()])
    forged = build_board_post_tombstone(
        signing_identity=origin.signing_key, board_id=BOARD_ID, root_post_id=post.content_id,
        previous_event_id=post.content_id, subject="x", body="x", reason=None, created_at="2026-10-02T00:00:00Z",
        moderator=_who(here.fingerprint),
    )
    with pytest.raises(LinkProtocolError, match="does not\\s+verify"):
        carol.handle_events(origin.fingerprint, [forged.to_dict()])


def test_a_moderator_naming_the_origin_or_an_unknown_node_is_refused(tmp_path, clock, origin, here):  # noqa: F811
    carol, post = _receiver(tmp_path, clock, origin, here)
    stranger = bootstrap_node_identity("stranger")
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(stranger.fingerprint), "scope": "origin", "permissions": ["delete"]},
    ]).to_dict()])
    with pytest.raises(LinkProtocolError, match="of the origin itself"):
        carol.handle_events(origin.fingerprint, [_tombstone(origin, post, _who(origin.fingerprint)).to_dict()])
    with pytest.raises(MissingDependency):
        carol.handle_events(origin.fingerprint, [_tombstone(stranger, post, _who(stranger.fingerprint)).to_dict()])


def test_a_community_scope_is_checked_against_the_list_in_memory(tmp_path, clock, origin, here):  # noqa: F811
    carol, post = _receiver(tmp_path, clock, origin, here)
    community = build_community_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, name="Retro",
        created_at="2026-02-01T00:00:00Z",
    )
    community_id = community.payload["community_id"]
    carol.handle_events(origin.fingerprint, [community.to_dict(), _roster(origin, [
        {"moderator": _who(here.fingerprint), "scope": {"community": community_id}, "permissions": ["delete"]},
    ]).to_dict()])
    tombstone = _tombstone(here, post, _who(here.fingerprint))
    with pytest.raises(LinkProtocolError, match="does not let delete"):
        carol.handle_events(here.fingerprint, [tombstone.to_dict()])
    members = build_community_members(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, community_id=community_id,
        members={"boards": [BOARD_ID], "channels": [], "file_areas": []}, created_at="2026-02-02T00:00:00Z",
    )
    carol.handle_events(origin.fingerprint, [members.to_dict()])
    assert carol.handle_events(here.fingerprint, [tombstone.to_dict()]) == [tombstone.content_id]


def test_a_removal_naming_one_board_cannot_reach_a_post_on_another(tmp_path, clock, origin, here):  # noqa: F811
    # Review of #1361: a grant on board A must not reach a post on board B.
    carol, _post_on_a = _receiver(tmp_path, clock, origin, here)
    other_board = build_board_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, board_id="d" * 64,
        name="Elsewhere", created_at="2026-01-01T00:00:00Z",
    )
    post_on_b = build_board_post(
        signing_identity=origin.signing_key, home_node_fingerprint=origin.fingerprint, local_user_id="wanderer",
        board_id="d" * 64, subject="Other", body="other", created_at="2026-01-01T00:00:00Z",
    )
    carol.handle_events(origin.fingerprint, [other_board.to_dict(), post_on_b.to_dict(), _roster(origin, [
        {"moderator": _who(here.fingerprint), "scope": {"kind": "board", "id": BOARD_ID}, "permissions": ["delete"]},
    ]).to_dict()])
    with pytest.raises(LinkProtocolError, match="its root post"):
        carol.handle_events(here.fingerprint, [_tombstone(here, post_on_b, _who(here.fingerprint)).to_dict()])
    assert not carol.board_events.is_tombstoned(post_on_b.content_id)


def test_the_moderators_name_matches_ignoring_case_on_every_node(tmp_path, clock, origin, here):  # noqa: F811
    # Review of #1361: the roster says "mod", the account is "Mod".
    carol, post = _receiver(tmp_path, clock, origin, here)
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(here.fingerprint, "mod"), "scope": "origin", "permissions": ["delete"]},
    ]).to_dict()])
    tombstone = _tombstone(here, post, _who(here.fingerprint, "Mod"))
    assert carol.handle_events(here.fingerprint, [tombstone.to_dict()]) == [tombstone.content_id]
