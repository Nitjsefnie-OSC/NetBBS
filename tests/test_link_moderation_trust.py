"""
Trust and history for Link moderation (design doc §16 issue #1357, Decision
9; issue #1359 slice 6): an action signed by a node this node holds
quarantined or blocked, or by a moderator blocked here, is not applied; a
quarantined origin's roster grants nothing; and what is applied is noted in
the linked channel's or board's history.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import create_user
from netbbs.link.channel_moderation import restriction_in_force
from netbbs.link.channels import get_channel_by_channel_id, materialize_carried_channel
from netbbs.link.events import build_channel_genesis, build_channel_restriction, build_link_moderators
from netbbs.link.moderators import (
    holds_link_permission,
    link_moderator_lines,
    moderation_refusal,
    store_received_roster,
)
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.link.transport import persist_accepted_events
from netbbs.link.trust import TrustDimension, TrustState, TrustSubject, register_subject, set_trust_override
from netbbs.moderation.log import list_recent_actions
from netbbs.moderation.roles import ChannelPermission
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane

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


def _carried(db, origin):
    materialize_carried_channel(db, build_channel_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, channel_id=CHANNEL_ID,
        name="lounge", created_at="2026-01-01T00:00:00Z",
    ))
    return get_channel_by_channel_id(db, CHANNEL_ID)


def _distrust(db, subject, state):
    register_subject(db, subject, first_accepted_at="2026-01-01T00:00:00Z", now_iso="2026-01-01T00:00:00Z")
    set_trust_override(db, subject, TrustDimension.IDENTITY_INTEGRITY, state, reason="test",
                       now_iso="2026-01-01T00:00:02Z")


def _mute(signer, moderator=None):
    return build_channel_restriction(
        signing_identity=signer.signing_key, channel_id=CHANNEL_ID, target=_who("e" * 64, "troll"), kind="mute",
        action="impose", created_at="2026-10-02T00:00:00Z", moderator=moderator,
    )


def test_an_action_from_an_untrusted_signer_is_refused(db, origin, here):
    _carried(db, origin)
    delegate = bootstrap_node_identity("delegate")
    by_origin = _mute(origin).to_dict()
    by_delegate = _mute(delegate, moderator=_who(delegate.fingerprint, "mod")).to_dict()
    assert moderation_refusal(db, by_origin) is None
    assert moderation_refusal(db, by_delegate) is None

    _distrust(db, TrustSubject.user(delegate.fingerprint, "mod"), TrustState.BLOCKED)
    assert "moderator" in moderation_refusal(db, by_delegate)
    _distrust(db, TrustSubject.node(delegate.fingerprint), TrustState.QUARANTINED)
    assert "moderator's node" in moderation_refusal(db, by_delegate)
    _distrust(db, TrustSubject.node(origin.fingerprint), TrustState.QUARANTINED)
    assert "origin" in moderation_refusal(db, by_origin)


def test_a_quarantined_origins_roster_grants_nothing_here(db, origin, here):
    channel = _carried(db, origin)
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who(here.fingerprint, "mod"), "scope": "origin", "permissions": ["moderate"]}],
        created_at="2026-10-01T00:00:00.000000Z",
    )
    store_received_roster(db, envelope=roster.to_dict(), content_id=roster.content_id,
                          own_fingerprint=here.fingerprint)
    mod = create_user(db, "mod", password="hunter2pw")
    assert holds_link_permission(db, mod, object_type="channel", object_id=channel.id,
                                 permission=ChannelPermission.MODERATE)
    assert link_moderator_lines(db, "channel", channel.id) == [f"mod@{_label(db, here)}: moderate (Link)"]
    _distrust(db, TrustSubject.node(origin.fingerprint), TrustState.QUARANTINED)
    assert not holds_link_permission(db, mod, object_type="channel", object_id=channel.id,
                                     permission=ChannelPermission.MODERATE)
    assert link_moderator_lines(db, "channel", channel.id) == []


def _label(db, identity):
    from netbbs.link.node_profiles import identity_for_fingerprint

    return identity_for_fingerprint(db, identity.fingerprint).label


def _persist(db, here, origin, raw):
    node = LinkNode(identity=here)
    from netbbs.link.events import event_content_id

    content_id = event_content_id(raw["envelope"])
    node.known_event_ids.add(content_id)
    node.events[content_id] = raw
    lane = DatabaseLane(db.path)
    try:
        asyncio.run(persist_accepted_events(
            lane, node, [content_id], sender_fingerprint=origin.fingerprint, max_carried_boards=None,
            enforce_trust_policy=True,
        ))
    finally:
        lane.close()
    return node, content_id


def test_an_applied_mute_is_in_the_channels_history(db, origin, here):
    channel = _carried(db, origin)
    _persist(db, here, origin, _mute(origin).to_dict())
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "troll") is not None
    [entry] = [e for e in list_recent_actions(db, object_type="channel", object_id=channel.id)
               if e.action == "link_mute"]
    assert entry.detail == f"troll@{'e' * 64} by {origin.fingerprint} (its origin)"


def test_a_refused_mute_is_not_applied_and_not_logged(db, origin, here):
    channel = _carried(db, origin)
    _distrust(db, TrustSubject.node(origin.fingerprint), TrustState.BLOCKED)
    node, content_id = _persist(db, here, origin, _mute(origin).to_dict())
    assert restriction_in_force(db, CHANNEL_ID, "e" * 64, "troll") is None
    assert not [e for e in list_recent_actions(db, object_type="channel", object_id=channel.id)
                if e.action == "link_mute"]
    assert content_id in node.known_event_ids  # not fetched again and again


def test_a_channels_moderators_name_channel_permissions_and_link_delegates(db, origin, here):
    from netbbs.moderation.roles import grant_permissions
    from netbbs.net.admin_resources import _moderator_lines

    channel = _carried(db, origin)
    boss = create_user(db, "boss", password="hunter2pw", user_level=255)
    local = create_user(db, "local", password="hunter2pw")
    grant_permissions(db, local, object_type="channel", object_id=channel.id,
                      permissions=ChannelPermission.MODERATE, granted_by=boss)
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who("f" * 64, "far"), "scope": {"kind": "channel", "id": CHANNEL_ID},
                  "permissions": ["topic"]}],
        created_at="2026-10-01T00:00:00.000000Z",
    )
    store_received_roster(db, envelope=roster.to_dict(), content_id=roster.content_id,
                          own_fingerprint=here.fingerprint)
    lines = _moderator_lines(db, "channel", channel.id)
    assert lines[0] == "local: moderate"
    assert lines[1].startswith("far@") and lines[1].endswith(": topic (Link)")


def test_a_long_link_roster_is_capped_in_the_moderator_lines(db, origin, here):
    from netbbs.net.admin_resources import _MODERATORS_SHOWN, _moderator_lines

    channel = _carried(db, origin)
    roster = build_link_moderators(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint,
        entries=[{"moderator": _who("f" * 64, f"m{i}"), "scope": "origin", "permissions": ["topic"]}
                 for i in range(_MODERATORS_SHOWN + 3)],
        created_at="2026-10-01T00:00:00.000000Z",
    )
    store_received_roster(db, envelope=roster.to_dict(), content_id=roster.content_id,
                          own_fingerprint=here.fingerprint)
    lines = _moderator_lines(db, "channel", channel.id)
    assert len(lines) == _MODERATORS_SHOWN + 1 and lines[-1] == "...and 3 more (Link)"
