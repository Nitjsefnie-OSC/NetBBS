"""
The topic of a linked channel (design doc §16 issue #1357, Decision 4; issue
#1359 slice 4): set by its origin or a delegated moderator with `topic`, the
newest wins everywhere, and a carrier's own `/topic` is refused for anyone
else -- a local topic would contradict the shared one.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.chat.channels import create_channel
from netbbs.chat.hub import ChatHub, ParticipantId
from netbbs.chat.mailbox import MessageMailbox
from netbbs.chat.presence import PresenceRegistry
from netbbs.link.boards import LinkContext
from netbbs.link.channel_moderation import store_received_topic
from netbbs.link.channels import get_channel_by_channel_id, link_channel, materialize_carried_channel
from netbbs.link.events import build_channel_genesis, build_channel_topic, build_link_moderators
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


def _topic(signer, topic, created_at, moderator=None):
    return build_channel_topic(signing_identity=signer.signing_key, channel_id=CHANNEL_ID, topic=topic,
                               created_at=created_at, moderator=moderator)


def _store(db, event):
    return store_received_topic(db, envelope=event.to_dict(), content_id=event.content_id)


def test_the_newest_topic_is_shown_on_the_carrier(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    assert _store(db, _topic(origin, "Retro night", "2026-10-02T00:00:00.000000Z")) == "stored"
    assert get_channel_by_channel_id(db, CHANNEL_ID).topic == "Retro night"
    assert _store(db, _topic(origin, "Older", "2026-10-01T00:00:00.000000Z")) == "stale"
    assert get_channel_by_channel_id(db, CHANNEL_ID).topic == "Retro night"
    _store(db, _topic(origin, None, "2026-10-03T00:00:00.000000Z"))
    assert get_channel_by_channel_id(db, CHANNEL_ID).topic is None
    assert len(all_roster_content_ids(db)) == 1  # withheld from older peers


def test_a_delegated_topic_needs_topic_in_the_roster(clock, origin):  # noqa: F811
    delegate = bootstrap_node_identity("delegate")
    carol = LinkNode(identity=bootstrap_node_identity("carol"))
    for node in (origin, delegate):
        carol.handle_hello(_hello_bytes(LinkNode(identity=node), clock=clock))
    carol.handle_events(origin.fingerprint, [_genesis(origin).to_dict()])
    topic = _topic(delegate, "Delegated", "2026-10-02T00:00:00.000000Z", moderator=_who(delegate.fingerprint, "mod"))
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who(delegate.fingerprint, "mod"), "scope": "origin", "permissions": ["moderate"]}],
        created_at="2026-10-01T00:00:00.000000Z",
    )
    carol.handle_events(origin.fingerprint, [roster.to_dict()])
    with pytest.raises(LinkProtocolError, match="does not let topic"):
        carol.handle_events(delegate.fingerprint, [topic.to_dict()])
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who(delegate.fingerprint, "mod"), "scope": "origin", "permissions": ["topic"]}],
        created_at="2026-10-01T01:00:00.000000Z",
    )
    carol.handle_events(origin.fingerprint, [roster.to_dict()])
    assert carol.handle_events(delegate.fingerprint, [topic.to_dict()]) == [topic.content_id]
    forged = _topic(delegate, "Forged", "2026-10-02T00:00:00.000000Z")
    with pytest.raises(LinkProtocolError, match="does not verify"):
        carol.handle_events(delegate.fingerprint, [forged.to_dict()])


# -- /topic -----------------------------------------------------------------------


def _topic_command(db, channel, user, args, node):
    lane = DatabaseLane(db.path)
    try:
        session = FakeSession([])
        ctx = chat_flow.ChatCommandContext(
            session=session, lane=lane, hub=ChatHub(), presence=PresenceRegistry(), mailbox=MessageMailbox(),
            channel=channel, user=user, participant_id=ParticipantId(user.username, 1), pinned_ui_enabled=False,
            link_context=LinkContext(link_node=node),
        )
        asyncio.run(chat_flow._handle_topic(ctx, args))
        return " ".join(_written_text(session).split())
    finally:
        lane.close()


def test_the_origins_topic_is_signed_for_everyone(db, here):
    sysop = create_user(db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    channel = create_channel(db, "lounge", creator=sysop)
    link_channel(db, channel, node_identity=here)
    node = LinkNode(identity=here)
    text = _topic_command(db, channel, sysop, "Retro night", node)
    assert "Topic changed by sysop: Retro night" in text
    row = db.connection.execute("SELECT envelope_json, content_id FROM link_channel_topics").fetchone()
    payload = json.loads(row["envelope_json"])["envelope"]["payload"]
    assert payload["topic"] == "Retro night" and "moderator" not in payload
    assert row["content_id"] in node.known_event_ids


def test_a_carriers_moderator_cannot_set_the_topic_without_a_grant(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    sysop = create_user(db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    grant_permissions(db, sysop, object_type="channel", object_id=channel.id,
                      permissions=ChannelPermission.EDIT, granted_by=sysop)
    text = _topic_command(db, channel, sysop, "Local idea", LinkNode(identity=here))
    assert "This channel's topic is set on" in text and "which it comes from" in text
    assert get_channel_by_channel_id(db, CHANNEL_ID).topic is None


def test_a_delegate_sets_the_carried_topic_for_everyone(db, origin, here):
    materialize_carried_channel(db, _genesis(origin))
    channel = get_channel_by_channel_id(db, CHANNEL_ID)
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who(here.fingerprint, "mod"), "scope": {"kind": "channel", "id": CHANNEL_ID},
                  "permissions": ["topic"]}],
        created_at="2026-10-01T00:00:00.000000Z",
    )
    store_received_roster(db, envelope=roster.to_dict(), content_id=roster.content_id,
                          own_fingerprint=here.fingerprint)
    mod = create_user(db, "mod", password="hunter2pw")
    text = _topic_command(db, channel, mod, "Delegated topic", LinkNode(identity=here))
    assert "Topic changed by mod: Delegated topic" in text
    assert get_channel_by_channel_id(db, CHANNEL_ID).topic == "Delegated topic"
    payload = json.loads(db.connection.execute(
        "SELECT envelope_json FROM link_channel_topics"
    ).fetchone()["envelope_json"])["envelope"]["payload"]
    assert payload["moderator"] == _who(here.fingerprint, "mod")


def test_a_linked_channels_topic_is_bounded(db, here):
    sysop = create_user(db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    channel = create_channel(db, "lounge", creator=sysop)
    link_channel(db, channel, node_identity=here)
    text = _topic_command(db, channel, sysop, "x" * 301, LinkNode(identity=here))
    assert "at most 300 bytes" in text
