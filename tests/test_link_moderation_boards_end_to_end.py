"""
Delegated board moderation over real transport (issue #1359 slice 2): the
origin delegates removal to a user of a carrying node, the carrier learns
it, that user removes a post there, and the removal -- signed by the carrier
with the moderator named -- reaches the origin and, from it, a third node.
"""

from __future__ import annotations

import asyncio

from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards.boards import create_board, get_board_by_name
from netbbs.boards.posts import create_post, tombstone_post, visible_post
from netbbs.link.boards import link_board, queue_board_post_tombstone_if_linked
from netbbs.link.events import event_content_id
from netbbs.link.moderators import grant_link_moderator
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.link.store import load_link_node
from tests.test_link_communities_end_to_end import _NodeDb, _pass


def _post_row(db, subject):
    return db.connection.execute(
        "SELECT * FROM posts WHERE subject = ? ORDER BY created_at", (subject,)
    ).fetchall()


def _removed(db) -> bool:
    return db.connection.execute("SELECT COUNT(*) FROM posts WHERE tombstoned_at IS NOT NULL").fetchone()[0] == 1


def test_a_delegated_removal_reaches_the_origin_and_a_third_node(tmp_path):
    origin_identity = bootstrap_node_identity("origin")
    carrier_identity = bootstrap_node_identity("carrier")
    third_identity = bootstrap_node_identity("third")
    origin_node, carrier_node, third_node = (LinkNode(identity=i) for i in (origin_identity, carrier_identity, third_identity))
    origin, carrier, third = (_NodeDb(tmp_path, name) for name in ("origin", "carrier", "third"))
    try:
        sysop = create_user(origin.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
        board = create_board(origin.db, "Retro", creator=sysop)
        link_board(origin.db, board, node_identity=origin_identity)
        from netbbs.link.boards import queue_board_post_if_linked

        post = create_post(origin.db, board, sysop, "Spam", "buy now")
        queue_board_post_if_linked(origin.db, post, board, node_identity=origin_identity)
        raw = grant_link_moderator(
            origin.db, origin_identity, home_node_fingerprint=carrier_identity.fingerprint, local_user_id="wren",
            scope_kind="board", scope_id=board.board_id, permissions=["delete"], actor=sysop, label="Retro",
        )
        # The running origin, as a restart builds it: its boards and roster.
        origin_node = load_link_node(origin.db, origin_identity)
        assert event_content_id(raw["envelope"]) in origin_node.known_event_ids
        wren = create_user(carrier.db, "wren", password="hunter2pw")

        # The origin pushes the board, the post and the roster to the carrier.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        carried_board = get_board_by_name(carrier.db, "Retro")
        [carried] = _post_row(carrier.db, "Spam")
        removed = tombstone_post(carrier.db, visible_post(carrier.db, carried["post_id"]), carried_board, tombstoned_by=wren)
        tombstone = queue_board_post_tombstone_if_linked(
            carrier.db, removed, carried_board, node_identity=carrier_identity, tombstoned_by=wren,
        )
        assert tombstone is not None and tombstone.payload["moderator"]["local_user_id"] == "wren"

        # The origin pulls from the carrier: the removal is accepted there.
        asyncio.run(_pass(origin_node, origin, carrier_node, carrier.lane))
        assert _removed(origin.db)

        # A third node carrying from the origin gets it too.
        asyncio.run(_pass(origin_node, origin, third_node, third.lane))
        asyncio.run(_pass(third_node, third, origin_node, origin.lane))
        assert _removed(third.db)
    finally:
        for store in (origin, carrier, third):
            store.close()
