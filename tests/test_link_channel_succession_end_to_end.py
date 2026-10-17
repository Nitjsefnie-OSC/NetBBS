"""
A linked channel's origin handoff over real transport (issue #1359 slice 5):
the origin offers the channel to a carrier, that carrier accepts, and the old
origin and a third carrier both persist the new origin.
"""

from __future__ import annotations

import asyncio

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.chat.channels import create_channel
from netbbs.link.channels import (
    accept_channel_origin_transfer,
    channel_origin_fingerprint,
    get_channel_by_channel_id,
    link_channel,
    offer_channel_origin_transfer,
)
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.link.store import load_link_node
from tests.test_link_communities_end_to_end import _NodeDb, _pass


def test_a_channel_handoff_reaches_the_old_origin_and_a_bystander(tmp_path):
    old_identity = bootstrap_node_identity("old")
    new_identity = bootstrap_node_identity("new")
    third_identity = bootstrap_node_identity("third")
    new_node, third_node = LinkNode(identity=new_identity), LinkNode(identity=third_identity)
    old, new, third = (_NodeDb(tmp_path, name) for name in ("old", "new", "third"))
    try:
        sysop = create_user(old.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
        channel = create_channel(old.db, "lounge", creator=sysop)
        link_channel(old.db, channel, node_identity=old_identity)
        old_node = load_link_node(old.db, old_identity)

        # Both carriers pick the channel up from its origin.
        asyncio.run(_pass(old_node, old, new_node, new.lane))
        asyncio.run(_pass(old_node, old, third_node, third.lane))
        assert get_channel_by_channel_id(third.db, channel.channel_id) is not None

        offer_channel_origin_transfer(old.db, channel, node_identity=old_identity,
                                      new_origin_fingerprint=new_identity.fingerprint)
        old_node = load_link_node(old.db, old_identity)
        asyncio.run(_pass(old_node, old, new_node, new.lane))
        offer = new_node.channel_lifecycle.pending_offer(channel.channel_id)
        assert offer is not None

        carried = get_channel_by_channel_id(new.db, channel.channel_id)
        accepted = accept_channel_origin_transfer(new.db, carried, node_identity=new_identity, offer=offer)
        new_node.channel_lifecycle.record_acceptance(
            channel.channel_id, new_origin_fingerprint=new_identity.fingerprint,
            accepted_content_id=accepted.content_id,
        )
        new_node.known_event_ids.add(accepted.content_id)
        new_node.events[accepted.content_id] = accepted.to_dict()

        # The old origin pulls the acceptance from the new one.
        asyncio.run(_pass(old_node, old, new_node, new.lane))
        assert channel_origin_fingerprint(old.db, channel) == new_identity.fingerprint
        assert old_node.current_channel_origin(channel.channel_id) == new_identity.fingerprint

        # A bystander gets both halves from the old origin.
        asyncio.run(_pass(old_node, old, third_node, third.lane))
        asyncio.run(_pass(third_node, third, old_node, old.lane))
        assert channel_origin_fingerprint(
            third.db, get_channel_by_channel_id(third.db, channel.channel_id)
        ) == new_identity.fingerprint
    finally:
        for store in (old, new, third):
            store.close()
