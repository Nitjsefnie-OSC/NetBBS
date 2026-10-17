"""
Mutes and bans in linked channels (design doc §16 issue #1357, Decision 3;
issue #1359 slice 3): a `channel_restriction` signed by the channel's origin
or a delegated moderator's home node mutes or bans one participant from any
node everywhere the channel is carried; carriers hide the participant's
messages, the participant's own node enforces it, and a carrier may add a
local restriction of its own that is never signed.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import create_user
from netbbs.chat.channels import create_channel
from netbbs.chat.hub import ChatHub, ParticipantId
from netbbs.chat.mailbox import MessageMailbox
from netbbs.chat.moderation import is_banned, is_muted
from netbbs.chat.presence import PresenceRegistry
from netbbs.chat.scrollback import get_scrollback
from netbbs.link.boards import LinkContext
from netbbs.link.channel_moderation import (
    restriction_in_force,
    set_local_restriction,
    store_received_restriction,
)
from netbbs.link.channels import get_channel_by_channel_id, link_channel, materialize_carried_channel, materialize_carried_channel_message
from netbbs.link.events import (
    build_channel_genesis,
    build_channel_message,
    build_channel_restriction,
    build_link_moderators,
)
from netbbs.link.moderators import all_roster_content_ids, store_received_roster
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode, LinkProtocolError
from netbbs.moderation import ChannelPermission, grant_permissions
from netbbs.net import chat_flow
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from tests.test_chat_flow_moderation import FakeSession, _written_text
from tests.test_link_protocol import _hello_bytes, clock  # noqa: F401 -- clock is a fixture

CHANNEL_ID = "c" * 64


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


def _who(fingerprint, user):
    return {"kind": "node_vouched_user", "home_node_fingerprint": fingerprint, "local_user_id": user}


def _genesis(origin):
    return build_channel_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, channel_id=CHANNEL_ID,
        name="lounge", created_at="2026-01-01T00:00:00Z",
    )


def _restriction(signer, *, target, kind="mute", action="impose", created_at="2026-10-02T00:00:00.000000Z",
                 moderator=None):
    return build_channel_restriction(
        signing_identity=signer.signing_key, channel_id=CHANNEL_ID, target=target, kind=kind, action=action,
        created_at=created_at, moderator=moderator,
    )


def _store(db, event, own):
    return store_received_restriction(db, envelope=event.to_dict(), content_id=event.content_id,
                                      own_fingerprint=own.fingerprint)


def _message(signer, user, body):
    return build_channel_message(
        signing_identity=signer.signing_key, home_node_fingerprint=signer.fingerprint, local_user_id=user,
        channel_id=CHANNEL_ID, body=body, created_at="2026-10-02T00:01:00Z",
    )


# -- a carrier: what is in force, and what is shown -----------------------------


def test_a_muted_participants_messages_are_kept_but_not_shown(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    troll = bootstrap_node_identity("trollhaven")
    assert _store(db, _restriction(origin, target=_who(troll.fingerprint, "troll")), here) == "stored"
    assert restriction_in_force(db, CHANNEL_ID, troll.fingerprint, "troll").kind == "mute"

    muted = _message(troll, "troll", "spam spam")
    assert materialize_carried_channel_message(db, muted, sender_fingerprint=origin.fingerprint) is None
    fine = _message(troll, "polite", "hello")
    assert materialize_carried_channel_message(db, fine, sender_fingerprint=origin.fingerprint) is not None
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    bodies = [m.body for m in get_scrollback(db, channel)]
    assert "hello" in bodies and "spam spam" not in bodies
    # Kept as a signed event, so relay and inventory are unaffected.
    assert db.connection.execute(
        "SELECT 1 FROM link_events WHERE content_id = ?", (muted.content_id,)
    ).fetchone() is not None


def test_a_delegate_never_outranks_the_origin(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    target = _who("e" * 64, "troll")
    _store(db, _restriction(origin, target=target, kind="ban"), here)
    lift = _restriction(here, target=target, kind="ban", action="lift", created_at="2026-10-05T00:00:00.000000Z",
                        moderator=_who(here.fingerprint, "mod"))
    assert _store(db, lift, here) == "stale"
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "troll").kind == "ban"
    # The same participant however their name is spelled: still outranked.
    lift_cased = _restriction(here, target=_who("e" * 64, "Troll"), kind="ban", action="lift",
                              created_at="2026-10-05T00:00:01.000000Z", moderator=_who(here.fingerprint, "mod"))
    assert _store(db, lift_cased, here) == "stale"
    # The origin's own later lift does take effect.
    _store(db, _restriction(origin, target=target, kind="ban", action="lift",
                            created_at="2026-10-06T00:00:00.000000Z"), here)
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "troll") is None


def test_a_local_restriction_adds_and_never_lifts_a_link_one(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    _store(db, _restriction(origin, target=_who("e" * 64, "troll")), here)
    set_local_restriction(db, channel_id=CHANNEL_ID, home_node_fingerprint="e" * 64, local_user_id="troll",
                          kind="mute", impose=False)
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "troll") is not None
    set_local_restriction(db, channel_id=CHANNEL_ID, home_node_fingerprint="e" * 64, local_user_id="noisy",
                          kind="ban", impose=True)
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "noisy").scope == "local"
    # Never signed, so never offered to anyone.
    assert len(all_roster_content_ids(db)) == 1


def test_the_participants_own_node_enforces_it_on_them(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    wren = create_user(db, "wren", password="hunter2pw")
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    assert is_muted(db, channel, wren) is None
    _store(db, _restriction(origin, target=_who(here.fingerprint, "Wren")), here)
    _store(db, _restriction(origin, target=_who(here.fingerprint, "wren"), kind="ban"), here)
    assert is_muted(db, channel, wren) is not None
    assert is_banned(db, channel, wren) is not None
    # Another node's user of the same name is not this node's caller.
    other = create_user(db, "finch", password="hunter2pw")
    _store(db, _restriction(origin, target=_who("e" * 64, "finch")), here)
    assert is_muted(db, channel, other) is None


# -- every receiver: checking who may restrict ------------------------------------


def _receiver(clock, origin, here):  # noqa: F811
    carol = LinkNode(identity=bootstrap_node_identity("carol"))
    for node in (origin, here):
        carol.handle_hello(_hello_bytes(LinkNode(identity=node), clock=clock))
    carol.handle_events(origin.fingerprint, [_genesis(origin).to_dict()])
    return carol


def _roster(origin, entries, created_at="2026-10-01T00:00:00.000000Z"):
    return build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, entries=entries,
        created_at=created_at,
    )


def test_the_origins_restriction_is_accepted_and_a_forgery_refused(clock, origin, here):  # noqa: F811
    carol = _receiver(clock, origin, here)
    mute = _restriction(origin, target=_who(here.fingerprint, "wren"))
    assert carol.handle_events(here.fingerprint, [mute.to_dict()]) == [mute.content_id]
    forged = _restriction(here, target=_who(here.fingerprint, "finch"))
    with pytest.raises(LinkProtocolError, match="does not verify"):
        carol.handle_events(here.fingerprint, [forged.to_dict()])


def test_a_delegated_restriction_needs_moderate_in_the_roster(clock, origin, here):  # noqa: F811
    carol = _receiver(clock, origin, here)
    mute = _restriction(here, target=_who("e" * 64, "troll"), moderator=_who(here.fingerprint, "mod"))
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(here.fingerprint, "mod"), "scope": {"kind": "channel", "id": CHANNEL_ID},
         "permissions": ["topic"]},
    ]).to_dict()])
    with pytest.raises(LinkProtocolError, match="does not let moderate"):
        carol.handle_events(here.fingerprint, [mute.to_dict()])
    carol.handle_events(origin.fingerprint, [_roster(origin, [
        {"moderator": _who(here.fingerprint, "mod"), "scope": {"kind": "channel", "id": CHANNEL_ID},
         "permissions": ["moderate"]},
    ], created_at="2026-10-01T01:00:00.000000Z").to_dict()])
    assert carol.handle_events(here.fingerprint, [mute.to_dict()]) == [mute.content_id]


# -- the chat commands --------------------------------------------------------------


def _chat(db, tmp_path, channel, user, lines, link_context):
    """Run one moderation command the way the chat loop dispatches it."""
    lane = DatabaseLane(db.path)
    try:
        session = FakeSession([])
        ctx = chat_flow.ChatCommandContext(
            session=session, lane=lane, hub=ChatHub(), presence=PresenceRegistry(), mailbox=MessageMailbox(),
            channel=channel, user=user, participant_id=ParticipantId(user.username, 1), pinned_ui_enabled=False,
            link_context=link_context,
        )
        for line in lines:
            command, _, args = line[1:].partition(" ")
            handler = {"mute": chat_flow._handle_mute, "ban": chat_flow._handle_ban,
                       "unmute": chat_flow._handle_unmute, "unban": chat_flow._handle_unban}[command]
            asyncio.run(handler(ctx, args))
        return " ".join(_written_text(session).split())
    finally:
        lane.close()


def test_the_origin_mutes_a_remote_participant_everywhere(db, tmp_path, clock, here):  # noqa: F811
    sysop = create_user(db, "sysop", password="hunter2pw", user_level=100)
    channel = create_channel(db, "lounge", creator=sysop)
    grant_permissions(db, sysop, object_type="channel", object_id=channel.id,
                      permissions=ChannelPermission.MODERATE, granted_by=sysop)
    link_channel(db, channel, node_identity=here)
    node = LinkNode(identity=here)
    troll = bootstrap_node_identity("trollhaven")
    node.handle_hello(_hello_bytes(LinkNode(identity=troll), clock=clock))
    text = _chat(db, tmp_path, channel, sysop, [f"/mute troll@{troll.fingerprint} 1h"], LinkContext(link_node=node))
    assert "is muted everywhere this channel is carried" in text
    state = restriction_in_force(db, channel.channel_id, troll.fingerprint, "troll")
    assert state is not None and state.scope == "link" and state.until is not None
    assert len(all_roster_content_ids(db)) == 1


def test_a_carriers_moderator_mutes_a_remote_participant_here_only(db, tmp_path, clock, origin, here):  # noqa: F811
    materialize_carried_channel(db, _genesis(origin))
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    sysop = create_user(db, "sysop", password="hunter2pw", user_level=100)
    grant_permissions(db, sysop, object_type="channel", object_id=channel.id,
                      permissions=ChannelPermission.MODERATE, granted_by=sysop)
    node = LinkNode(identity=here)
    node.handle_hello(_hello_bytes(LinkNode(identity=origin), clock=clock))
    text = _chat(db, tmp_path, channel, sysop, [f"/ban troll@{origin.fingerprint}"], LinkContext(link_node=node))
    assert "is banned on this node only" in text
    assert restriction_in_force(db, CHANNEL_ID, origin.fingerprint, "troll").scope == "local"


def test_a_delegate_mutes_a_remote_participant_everywhere(db, tmp_path, clock, origin, here):  # noqa: F811
    materialize_carried_channel(db, _genesis(origin))
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    roster = _roster(origin, [{"moderator": _who(here.fingerprint, "mod"), "scope": "origin",
                               "permissions": ["moderate"]}])
    store_received_roster(db, envelope=roster.to_dict(), content_id=roster.content_id,
                          own_fingerprint=here.fingerprint)
    mod = create_user(db, "mod", password="hunter2pw")
    node = LinkNode(identity=here)
    node.handle_hello(_hello_bytes(LinkNode(identity=origin), clock=clock))
    text = _chat(db, tmp_path, channel, mod, [f"/mute troll@{origin.fingerprint}"], LinkContext(link_node=node))
    assert "is muted everywhere this channel is carried" in text
    import json

    row = db.connection.execute("SELECT envelope_json FROM link_channel_restrictions").fetchone()
    assert json.loads(row["envelope_json"])["envelope"]["payload"]["moderator"] == _who(here.fingerprint, "mod")


def test_no_permission_no_restriction(db, tmp_path, clock, origin, here):  # noqa: F811
    materialize_carried_channel(db, _genesis(origin))
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    caller = create_user(db, "caller", password="hunter2pw")
    node = LinkNode(identity=here)
    text = _chat(db, tmp_path, channel, caller, [f"/mute troll@{origin.fingerprint}"], LinkContext(link_node=node))
    assert "You do not have permission to mute in this channel." in text
    assert db.connection.execute("SELECT COUNT(*) FROM link_channel_restrictions").fetchone()[0] == 0


def test_a_link_ban_silences_the_caller_already_in_the_channel(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    wren = create_user(db, "wren", password="hunter2pw")
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    assert chat_flow._check_mute(db, channel, wren) is None
    _store(db, _restriction(origin, target=_who(here.fingerprint, "wren"), kind="ban"), here)
    assert chat_flow._check_mute(db, channel, wren) == "indefinitely"
