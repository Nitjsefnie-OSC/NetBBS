"""
Mutes across Link over real transport (issue #1359 slice 3): the origin of
a linked channel delegates moderation to a user of a carrying node, that
user mutes a participant from a third node, and the mute -- signed by the
carrier with the moderator named -- reaches the origin and, from it, the
participant's own node, which then silences them.
"""

from __future__ import annotations

import asyncio

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.chat.channels import create_channel
from netbbs.chat.moderation import is_muted
from netbbs.link.channel_moderation import restriction_in_force, sign_channel_restriction
from netbbs.link.channels import get_channel_by_channel_id, link_channel
from netbbs.link.moderators import grant_link_moderator
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.link.store import load_link_node
from tests.test_link_communities_end_to_end import _NodeDb, _pass


def test_a_delegated_mute_reaches_the_origin_and_the_participants_own_node(tmp_path):
    origin_identity = bootstrap_node_identity("origin")
    carrier_identity = bootstrap_node_identity("carrier")
    third_identity = bootstrap_node_identity("third")
    carrier_node, third_node = (LinkNode(identity=i) for i in (carrier_identity, third_identity))
    origin, carrier, third = (_NodeDb(tmp_path, name) for name in ("origin", "carrier", "third"))
    try:
        sysop = create_user(origin.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
        channel = create_channel(origin.db, "lounge", creator=sysop)
        link_channel(origin.db, channel, node_identity=origin_identity)
        grant_link_moderator(
            origin.db, origin_identity, home_node_fingerprint=carrier_identity.fingerprint, local_user_id="wren",
            scope_kind="channel", scope_id=channel.channel_id, permissions=["moderate"], actor=sysop,
            label="lounge",
        )
        origin_node = load_link_node(origin.db, origin_identity)
        wren = create_user(carrier.db, "wren", password="hunter2pw")
        troll = create_user(third.db, "troll", password="hunter2pw")

        # The origin pushes the channel and the roster to the carrier.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        carried = get_channel_by_channel_id(carrier.db, channel.channel_id)
        assert carried is not None
        raw = sign_channel_restriction(
            carrier.db, carried, node_identity=carrier_identity, actor=wren,
            home_node_fingerprint=third_identity.fingerprint, local_user_id="troll", kind="mute", impose=True,
        )
        assert raw is not None and raw["envelope"]["payload"]["moderator"]["local_user_id"] == "wren"
        assert restriction_in_force(carrier.db, channel.channel_id, third_identity.fingerprint, "troll")

        # The origin pulls from the carrier: the mute is in force there.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        assert restriction_in_force(origin.db, channel.channel_id, third_identity.fingerprint, "troll")

        # The participant's own node carries the channel and gets the mute.
        asyncio.run(_pass(origin_node, origin, third_node, third.lane))
        asyncio.run(_pass(third_node, third, origin_node, origin.lane))
        own_copy = get_channel_by_channel_id(third.db, channel.channel_id)
        assert own_copy is not None
        assert is_muted(third.db, own_copy, troll) is not None
    finally:
        for store in (origin, carrier, third):
            store.close()
