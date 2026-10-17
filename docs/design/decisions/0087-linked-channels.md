# Issue #87 — linked channels — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§9.6 states the complete design and is now fully implemented:
`channel_genesis`/`channel_message` event types mirroring `board_genesis`/
`board_post` minus the fields that don't apply (no edit chain — channel
messages have no local edit concept at all; no `default_min_write_level`/
`_moderated`/`_max_post_age_days` equivalents — `Channel` has none of those
settings to recommend). Origin succession is reused by reference (§9.4's
model applies unchanged, if ever needed) rather than a new
`channel_origin_transfer_offer`/`_accepted` pair built in this issue —
genesis, promotion, materialization, and message propagation are the
actual scope, matching what actually shipped. `netbbs.link.channels`
mirrors `netbbs.link.boards` closely; `ChannelEventState` (genesis only, no
edit-chain half) mirrors `BoardEventState`; `handle_events` gained
`channel_genesis`/`channel_message` branches with issue #85's multi-hop
verification model from day one (never had the older restriction to begin
with). Issue #85's inventory mechanism (`InventoryRequest.channels`,
`channel_event_diff`) and issue #86's restart reconstruction both extend
to channels the same way they already cover boards.

Carry/materialization follows §9.3's exact shape, with one real, stated
consequence of reusing the existing bounded scrollback rather than
inventing unbounded storage for channel content: a materialized linked
message is subject to the same trim a local one already is, and a
self-originated message trimmed before any sync pass pushes it is simply
never propagated — bounded and honestly scoped, not a silent surprise,
since the identical bound already governs every channel's own local
history today. `channel_messages.link_content_id` is a new column solving
a real gap found while implementing: unlike `posts.post_id`,
`channel_messages.id` is a plain autoincrement with no existing
content-addressed column to key idempotent materialization off of.

**Not done in this issue, closed by issue #91:** wiring `queue_channel_
message_if_linked` into `netbbs.net.chat_flow`'s live interactive send
path was deferred here (`chat_flow.py`'s message-send code had no existing
`link_context` threading at all, unlike `login_flow.py`'s board-post path)
and completed separately — see issue #91's own §16 entry.
