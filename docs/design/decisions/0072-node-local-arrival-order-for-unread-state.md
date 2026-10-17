# Issue #72 — node-local arrival order for unread state — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§6.6's "Node-local arrival order for carried content" subsection now
states the complete design: `user_read_cursors.last_seen_arrival_id`,
sourced from `posts`/`files`' own rowid rather than authored
`created_at`, with existing cursors backfilled on upgrade. The one
accepted, documented scope boundary: jump-to-first-unread still uses
the `created_at`-based cursor and may not navigate precisely to an
out-of-order arrival, even though unread counting now correctly flags
it.
