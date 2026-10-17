# Issue #86 — event/dedup retention

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§8.9 now states the complete design: the chain-idempotency gap was in
`board_origin_transfer_offer`/`_accepted`, the only two board-scoped types
whose idempotency depended solely on the fast `known_event_ids` cache
rather than a self-heal check against their own authoritative state
(`pending_offer`/`board_lifecycle_head`) — fixed with the same self-heal
shape `key_transition`/`board_post_edit` already use. Tracing what else
depends on each object type's `link_events` row surviving (restart
reconstruction, and issue #85's own inventory diff) found only
`key_transition` genuinely redundant with an already-durable separate
source (`link_peers.transitions_json`); everything board-scoped stays
unbounded in this issue, stated explicitly rather than silently assumed
safe. `netbbs.link.store.purge_expired_key_transitions` purges on write
(90-day fixed window), the same shape `LinkDiagnosticLogHandler.emit`
already established for `link_diagnostic_log`.
