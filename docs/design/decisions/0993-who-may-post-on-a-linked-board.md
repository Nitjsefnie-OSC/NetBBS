# Issue #993 — who may post on a Linked board — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

ReLink Linked an announcements board, and any caller of any node carrying it
could post there: a carried post was held to nothing but the board's identity
policy. Normative description: §9.3.

**Decision 1 — origin authority, not a recommendation.** The origin chooses
who posts, and every carrying node enforces it, like closure. Rejected: a
local "accept carried posts" switch on each node, which cleans one node's
copy and leaves every other node's to its SysOp.

**Decision 2 — three modes, the origin chooses** (the maintainer's
decision): anyone, origin starts threads, origin only. "Announcements" and
"announcements with discussion" are both common.

**Decision 3 — its own event, latest wins, outside the lifecycle chain.**
Rejected: a field in `board_genesis`, which cannot change and so would only
reach boards Linked afterwards; and a chained lifecycle event, since an
origin keeps only its latest lifecycle event and a setting would push an
earlier transfer out of reach of a peer that missed it.

**Decision 4 — no capability gating.** Every node is updated before origins
set this (the maintainer's call); an older node would refuse the unknown
event type (#1022).
