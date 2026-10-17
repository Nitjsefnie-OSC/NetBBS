# Issue #1308 — Link Communities — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A Community (§6.5) groups a node's message boards, chat channels, file areas
and doors under a topic. Nothing about it crosses the wire today. No genesis
names a Community, and a carried board, channel or file area lands with no
Community at all, so a subscriber sees another node's topics flattened into
one list per kind. §6.5 states the intent: a Link Community is the same
Community object announced through a signed event, and carrying one carries
its present and future members with exclusions. #561 Decision 8 fixed the
vocabulary and left two questions open: whether a subscription has its own
bound, and how a subscribed Community is told apart from a same-named local
one. This entry answers both. Phase 6's other items stay separate (§15):
linked-channel governance, Link-blanket moderator grants, the lifecycle
surfaces and the audit board.

**Decision 1 — a Link Community lists its origin's own resources.** Its
members are boards, channels and file areas whose current origin is the node
that signs the Community. Authority then stays where it already is: the node
that signs the grouping also signs every member's genesis. A member whose
origin moves away (§9.4 transfer) drops out of the Community on every carrier,
because the Community's origin no longer speaks for it. Doors stay local-only.
Rejected: grouping other origins' resources. Subscribers would then carry a
board because a third node listed it, and that needs a rule for whose listing
counts, which nothing here has.

**Decision 2 — two signed events, modelled on what boards already have.**

- `community_genesis`: `origin_fingerprint`, `community_id`, `name`,
  `created_at`, and optionally `description` and the Community's own
  defaults (`default_min_read_level`, `default_min_write_level`,
  `default_min_age`, `default_name_requirement`, `default_age_requirement`).
  `community_id` is content-addressed from the origin, the name and the time,
  as a board id is, so two origins' same-named Communities stay distinct by
  construction. The defaults are recommendations, as a board genesis's are.
- `community_members`: `community_id`, `origin_fingerprint`, `members` (one
  list of resource ids per kind, at most 500 per kind), `created_at` and
  `nonce`. It replaces the whole membership. It is not chained: the newest
  `created_at` from the current origin wins, as with `board_posting` (§9.3).
  A membership change is idempotent, so a lost update is repaired by the next
  one.

Both are signed by the origin's current signing key. A member id the receiver
has no genesis for yet waits until the genesis arrives. An id whose genesis
names another origin is ignored.

**Decision 3 — Communities travel in the inventory, as a fourth kind.** The
inventory exchange (§8.8) and its `not_carried` declaration gain a
`communities` kind beside boards, channels and file areas. An offer, an
acceptance and an exclusion then work exactly as they do for a board: an
offered Community appears in the SysOp's offered list with its origin and its
member count. The new kind is advertised as a capability (`link_communities`),
and a node sends it only to peers that advertise it. An older node does not
relay community events: §7.5 relays an unknown type only when it names a board
the node carries. So the Communities of a node reach only its upgraded peers
and their upgraded peers, which is the usual cost of a new type.

**Decision 4 — subscribing is a SysOp's decision, and it auto-accepts members
within the caps.** A Community offer is never accepted automatically, whatever
the node-wide intake does for boards. Subscribing is a standing rule about
future content, so a person makes it. Accepting the offer *is* the
subscription. From then on, every member the Community lists, now or later,
is accepted as if the SysOp had accepted its offer, but it still counts
against the per-type carry caps (§9.3, 500 by default). A member past a cap
waits in the offered list with reason `cap`, as a board does. This answers
#561's first open question: a subscription has no bound of its own, and the
node's caps are the bound. As #561 already defines, excluding one member opts
it out and keeps it out. Leaving a Community (excluding the Community itself)
accepts nothing more and does not shed members already carried. A member the
origin removes from the Community stays carried, as a board that leaves a
Community stays carried (#561 Decision 4).

**Decision 5 — members land in a local copy of the Community; the carrying
node's own placement wins.** A subscription materializes a `communities` row,
the same object as a local Community, marked as Link and with its origin, as
a carried board is. This answers #561's second open question:
- **Telling it apart:** a same-name clash with a local Community is resolved as
  #671 resolves a board's (a suffix from the Community id). Screens show the
  linked marker (#1104) and the origin's label, as they do for carried boards.
- **Placement:** members go into it as they are accepted, and already-carried
  members with no Community move into it when the subscription starts.
- **Overrides:** a SysOp who moves a member to a Community of their own has
  made a local placement. Later membership updates never move that member back
  (§6.5: "carrying-node overrides win locally").
- **Defaults:** the Community's recommended defaults are stored on the copy,
  so its members inherit them locally like any Community's. A carried member
  of a Link Community stores no level of its own unless its genesis states
  one; today a carried board writes an explicit 0 and would never inherit.
  The SysOp may edit the copy's defaults, and an edit there is local.

**Decision 6 — promoting a Community announces the members already linked.** A
local Community is promoted to Link from its detail screen, as a board is.
- **What is announced:** the genesis and a membership list of its members that
  are already linked and originate here. Promotion links nothing.
- **Later changes:** linking a member later, moving a linked board of one's
  own into or out of the Community, or unlinking one sends a new
  `community_members`.
- **Why:** linking stays a decision about one resource at a time, so filing a
  private board under a Link Community never publishes it.
- **Rejected:** linking every member on promotion and every member added
  later. That makes "put this board in that Community" quietly mean "publish
  it".

**Decision 7 — closure now; transfer later.** Deleting a promoted Community at
its origin, or unlinking it, signs a terminal `community_closure`. Carriers
keep every member they carry. Their copy becomes a plain local Community,
marked as closed at its origin, and their SysOp may keep or delete it.
Transfer of a Community's origin waits for the same need that produced §9.4's
board transfer. The members' own transfers already work, and Decision 1 says
what they mean here.

**Not done, deliberately.**
- **Moderators:** Link-blanket and Community-blanket moderator grants over
  Link are a separate Phase 6 item. A carried Community's members are
  moderated as carried boards are today (§9.5).
- **Following:** following a Community stays out until Communities have a
  follow concept locally (§6.6).
- **Doors:** not linked.
- **Implementation:** it is its own issue. It needs:
  - `communities` columns (genesis, origin, Link marker);
  - a placement-override flag on boards, channels and file areas;
  - `communities` as a carry-decision and inventory kind;
  - the inheritance change in Decision 5.
