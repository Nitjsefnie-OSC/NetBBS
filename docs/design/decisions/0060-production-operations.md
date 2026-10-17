# Issue #60 — production operations — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Implemented across four slices, all merged: backup/restore (§13.4,
`netbbs.backup`, verified against a real running node including a
create-wipe-restore round trip and the live-lock restore refusal); outbound
work items/retry/dead-letter (§13.7, `netbbs.link.work_items`, scoped to
Link mail delivery and acknowledgement delivery specifically — not gossip or
relay maintenance, which don't fit the same shape — wired into
`netbbs.link.mail`/`netbbs.link.sync` and surfaced as an `[O]utbox` SysOp
screen); bounded quotas (§13.9); and startup integrity checking, diagnostic
log retention, protocol/database upgrade compatibility, and graceful Link
drain on shutdown (§13.11). Staged/validated restore (§13.10) shipped
separately as issue #75.

Rehearsing these controls against a real long-running node (not just their
original implementation tests) is tracked by the Phase 3 stabilization gate
above, not by this issue.
