"""
Origin succession for linked channels (design doc §16 issue #1357, Decision
5; issue #1359 slice 5): `channel_origin_transfer_offer`/`_accepted`, the
shape of a board's (§9.4) -- the current origin offers, the named node
accepts, and only the acceptance moves the channel's origin. From then on
the channel's moderation answers to the new origin.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.chat.channels import create_channel
from netbbs.link.channels import (
    LinkChannelsError,
    accept_channel_origin_transfer,
    channel_origin_fingerprint,
    get_channel_by_channel_id,
    link_channel,
    materialize_carried_channel,
    offer_channel_origin_transfer,
    record_channel_origin_change,
)
from netbbs.link.events import (
    build_channel_genesis,
    build_channel_origin_transfer_accepted,
    build_channel_origin_transfer_offer,
    build_channel_restriction,
)
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode, LinkProtocolError, MissingDependency
from netbbs.link.store import load_link_node
from netbbs.storage.database import Database
from tests.test_link_protocol import _hello_bytes, clock  # noqa: F401 -- clock is a fixture

CHANNEL_ID = "c" * 64
AT = "2026-10-02T00:00:00Z"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def old():
    return bootstrap_node_identity("old-origin")


@pytest.fixture
def new():
    return bootstrap_node_identity("new-origin")


def _genesis(origin):
    return build_channel_genesis(
        signing_identity=origin.signing_key, origin_fingerprint=origin.fingerprint, channel_id=CHANNEL_ID,
        name="lounge", created_at="2026-01-01T00:00:00Z",
    )


def _offer(signer, previous, old_fp, new_fp):
    return build_channel_origin_transfer_offer(
        signing_identity=signer.signing_key, channel_id=CHANNEL_ID, previous_event_id=previous,
        old_origin_fingerprint=old_fp, new_origin_fingerprint=new_fp, created_at=AT,
    )


def _accepted(signer, offer):
    return build_channel_origin_transfer_accepted(
        signing_identity=signer.signing_key, channel_id=CHANNEL_ID, previous_event_id=offer.content_id,
        new_origin_fingerprint=signer.fingerprint, created_at=AT,
    )


def _bystander(clock, *nodes):  # noqa: F811
    carol = LinkNode(identity=bootstrap_node_identity("carol"))
    for node in nodes:
        carol.handle_hello(_hello_bytes(LinkNode(identity=node), clock=clock))
    return carol


# -- every receiver ---------------------------------------------------------------


def test_a_handoff_moves_the_origin_only_once_accepted(clock, old, new):  # noqa: F811
    carol = _bystander(clock, old, new)
    genesis = _genesis(old)
    carol.handle_events(old.fingerprint, [genesis.to_dict()])
    offer = _offer(old, genesis.content_id, old.fingerprint, new.fingerprint)
    assert carol.handle_events(old.fingerprint, [offer.to_dict()]) == [offer.content_id]
    assert carol.current_channel_origin(CHANNEL_ID) == old.fingerprint
    accepted = _accepted(new, offer)
    assert carol.handle_events(new.fingerprint, [accepted.to_dict()]) == [accepted.content_id]
    assert carol.current_channel_origin(CHANNEL_ID) == new.fingerprint
    # A resend of either half is a no-op.
    assert carol.handle_events(old.fingerprint, [offer.to_dict(), accepted.to_dict()]) == []


def test_an_offer_must_come_from_the_current_origin(clock, old, new):  # noqa: F811
    carol = _bystander(clock, old, new)
    genesis = _genesis(old)
    carol.handle_events(old.fingerprint, [genesis.to_dict()])
    with pytest.raises(LinkProtocolError, match="does not verify"):
        carol.handle_events(new.fingerprint, [_offer(new, genesis.content_id, old.fingerprint, new.fingerprint).to_dict()])
    with pytest.raises(LinkProtocolError, match="old origin"):
        carol.handle_events(new.fingerprint, [_offer(new, genesis.content_id, new.fingerprint, new.fingerprint).to_dict()])


def test_one_offer_at_a_time_and_an_acceptance_needs_one(clock, old, new):  # noqa: F811
    carol = _bystander(clock, old, new)
    genesis = _genesis(old)
    carol.handle_events(old.fingerprint, [genesis.to_dict()])
    offer = _offer(old, genesis.content_id, old.fingerprint, new.fingerprint)
    with pytest.raises(MissingDependency):
        carol.handle_events(new.fingerprint, [_accepted(new, offer).to_dict()])
    carol.handle_events(old.fingerprint, [offer.to_dict()])
    second = build_channel_origin_transfer_offer(
        signing_identity=old.signing_key, channel_id=CHANNEL_ID, previous_event_id=genesis.content_id,
        old_origin_fingerprint=old.fingerprint, new_origin_fingerprint=new.fingerprint, created_at=AT, nonce="x",
    )
    with pytest.raises(LinkProtocolError, match="at most one"):
        carol.handle_events(old.fingerprint, [second.to_dict()])
    stranger = bootstrap_node_identity("stranger")
    carol.handle_hello(_hello_bytes(LinkNode(identity=stranger), clock=clock))
    with pytest.raises(LinkProtocolError, match="doesn't match"):
        carol.handle_events(stranger.fingerprint, [_accepted(stranger, offer).to_dict()])


def test_after_a_handoff_moderation_answers_to_the_new_origin(clock, old, new):  # noqa: F811
    carol = _bystander(clock, old, new)
    genesis = _genesis(old)
    offer = _offer(old, genesis.content_id, old.fingerprint, new.fingerprint)
    carol.handle_events(old.fingerprint, [genesis.to_dict(), offer.to_dict()])
    carol.handle_events(new.fingerprint, [_accepted(new, offer).to_dict()])
    target = {"kind": "node_vouched_user", "home_node_fingerprint": "e" * 64, "local_user_id": "troll"}

    def mute(signer):
        return build_channel_restriction(
            signing_identity=signer.signing_key, channel_id=CHANNEL_ID, target=target, kind="mute",
            action="impose", created_at=AT,
        )

    with pytest.raises(LinkProtocolError, match="does not verify"):
        carol.handle_events(old.fingerprint, [mute(old).to_dict()])
    by_new = mute(new)
    assert carol.handle_events(new.fingerprint, [by_new.to_dict()]) == [by_new.content_id]


# -- the node's own database --------------------------------------------------------


def test_offering_and_accepting_on_the_two_nodes(tmp_path, old, new):
    old_db, new_db = Database(tmp_path / "old.db"), Database(tmp_path / "new.db")
    try:
        sysop = create_user(old_db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
        channel = create_channel(old_db, "lounge", creator=sysop)
        genesis = link_channel(old_db, channel, node_identity=old)
        with pytest.raises(LinkChannelsError, match="current origin"):
            offer_channel_origin_transfer(old_db, channel, node_identity=new, new_origin_fingerprint=new.fingerprint)
        offer = offer_channel_origin_transfer(old_db, channel, node_identity=old, new_origin_fingerprint=new.fingerprint)
        assert offer.payload["previous_event_id"] == genesis.content_id
        with pytest.raises(LinkChannelsError, match="outstanding"):
            offer_channel_origin_transfer(old_db, channel, node_identity=old, new_origin_fingerprint=new.fingerprint)

        materialize_carried_channel(new_db, genesis)
        carried = get_channel_by_channel_id(new_db, channel.channel_id)
        assert channel_origin_fingerprint(new_db, carried) == old.fingerprint
        with pytest.raises(LinkChannelsError, match="does not name this node"):
            accept_channel_origin_transfer(new_db, carried, node_identity=old, offer=offer)
        accept_channel_origin_transfer(new_db, carried, node_identity=new, offer=offer)
        assert channel_origin_fingerprint(new_db, carried) == new.fingerprint

        # The old origin learns of it as any carrier does.
        record_channel_origin_change(old_db, channel.channel_id, new.fingerprint)
        assert channel_origin_fingerprint(old_db, channel) == new.fingerprint
    finally:
        old_db.close()
        new_db.close()


def test_a_restart_remembers_a_handoff(tmp_path, old, new):
    db = Database(tmp_path / "new.db")
    try:
        genesis = _genesis(old)
        materialize_carried_channel(db, genesis)
        carried = get_channel_by_channel_id(db, CHANNEL_ID)
        offer = _offer(old, genesis.content_id, old.fingerprint, new.fingerprint)
        accepted = accept_channel_origin_transfer(db, carried, node_identity=new, offer=offer)
        node = load_link_node(db, new)
        assert node.current_channel_origin(CHANNEL_ID) == new.fingerprint
        assert accepted.content_id in node.known_event_ids
    finally:
        db.close()


# -- the SysOp screens ----------------------------------------------------------------


def test_the_transfer_and_accept_screens(tmp_path, old):
    from netbbs.net.admin_chat import _accept_channel_origin_transfer_screen, _transfer_channel_origin_screen
    from netbbs.storage.execution import DatabaseLane
    from tests.test_admin_flow import FakeSession, _add_fake_peer, _link_context, _written_text

    db = Database(tmp_path / "node.db")
    lane = DatabaseLane(db.path)
    try:
        sysop = create_user(db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
        channel = create_channel(db, "lounge", creator=sysop)
        link_context = _link_context()
        link_channel(db, channel, node_identity=link_context.node_identity)
        link_context.link_node.channel_events.record_genesis(_genesis_of(db, channel))
        peer = _add_fake_peer(link_context)
        session = FakeSession(["0", "1", "y"])
        asyncio.run(_transfer_channel_origin_screen(session, lane, channel, link_context))
        offer = link_context.link_node.channel_lifecycle.pending_offer(channel.channel_id)
        assert offer.payload["new_origin_fingerprint"] == peer.fingerprint

        assert offer.content_id in link_context.link_node.known_event_ids
        assert "Offer to hand #lounge off to" in _written_text(session)

        # This node is not the one offered: nothing to accept, nothing signed.
        session = FakeSession([])
        asyncio.run(_accept_channel_origin_transfer_screen(session, lane, channel, link_context))
        assert link_context.link_node.channel_lifecycle.pending_offer(channel.channel_id) == offer
        assert db.connection.execute(
            "SELECT link_lifecycle_json FROM channels WHERE id = ?", (channel.id,)
        ).fetchone()[0] is not None
    finally:
        lane.close()
        db.close()


def _genesis_of(db, channel):
    import json

    from netbbs.link.events import ChannelGenesis

    row = db.connection.execute("SELECT link_genesis_json FROM channels WHERE id = ?", (channel.id,)).fetchone()
    return ChannelGenesis.from_dict(json.loads(row["link_genesis_json"]))
