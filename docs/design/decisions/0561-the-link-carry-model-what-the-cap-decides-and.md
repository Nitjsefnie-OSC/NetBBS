# Issue #561 — the Link carry model: what the cap decides, and what "declined" means

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Carry is opt-out: joining Link is the only decision a SysOp makes, and each
verified genesis becomes a local board, channel or file area on arrival, up to
`max_carried_boards` / `max_carried_channels` / `max_carried_file_areas`
(500 each). The issue asked whether the property that falls out of that —
*which* resources a node carries is decided by sync arrival order, and the
rest are declined forever — was chosen or accidental. It was accidental.
Normative description: §9.3.

What the code does today, which set the shape of the answer:

- **"Declined" is never recorded; it is inferred.** A genesis in `link_events`
  with no local row reads as "seen and declined" (`inventory_wanted_ids`), and
  three different histories produce that state: a quota refusal, a SysOp
  deleting a carried resource (the delete functions leave `link_events`
  alone), and a crash between `save_event` and `materialize_carried_board`,
  which are separate lane calls with no repair path. Nothing can tell them
  apart, so §9.3's promise of a visible local exclusion "represented honestly
  as 'not carried on this node'" had no implementation behind it.
- **Arrival order decides, and nothing revisits it.** Responders walk
  resources in sorted-id order; the requester dials configured seeds before the
  reliable roster. The 501st genesis of a type is refused and, being inferred
  as declined, never asked about again.
- **The declined state also starves sync.** A resource this node does not carry
  is absent from its `InventoryRequest`, so every responder carrying it sends
  its genesis and every event under it on every pass, under the one 200-event
  budget shared by all three types. Recorded as issue #669, with a
  reproduction.
- **The cap is exempt for what already landed**, so lowering it sheds nothing,
  and only boards have a readout (`Carried boards: N/500`); channels and file
  areas have none.

**Decision 1 — carry stays opt-out.** A node that joins Link carries what
arrives, and scenario 1's "small node joins a busy Link" keeps working without a
SysOp curating anything. What changes is everything past that default.

**Decision 2 — a genesis this node has accepted is in exactly one recorded
state: carried, offered or excluded.** *Carried* has a local row. *Offered* and
*excluded* are rows in a carry-decision record keyed by resource kind and id,
holding the state, when it was set and by whom (the node, or a SysOp). Nothing
is inferred from absence any more. Every transition is one transaction: the genesis save
with its carry outcome (materialization or the record), and each later move
between states — accepting, excluding, deleting, restoring — with the local row
and the record changing together. That needs non-committing variants of today's
`materialize_carried_*` and `delete_*` helpers, which commit internally. It
closes the genesis crash window the way issue #73 closed it for posts, rather
than adding a repair pass for it. On migration, every existing genesis without a
local row becomes *offered*: its history cannot be recovered, and offered is
the state that loses nothing and forces nothing.

**Decision 3 — past the cap a new resource is offered, not declined.** The
cap bounds what the node takes on automatically; it no longer decides what the
node can ever have. An offered resource is listed for the SysOp with its name,
description and origin node, and `[A]ccept` materializes it from the stored
genesis, replays any other events this node already holds for it (a migrated
resource, or lifecycle events accepted beside a genesis past the cap, would
otherwise be declared as known and never projected), and then the next pass
backfills the rest by ordinary inventory pull. Accepting is the SysOp's choice and is not
itself capped. A genesis whose name is already taken locally is carried under a
disambiguated local name, on automatic intake and on accept alike, and the SysOp
can rename it as any carried resource; names are not identities in Link.
Today such a genesis aborts one sync pass and is then never carried (issue
#671). While offered, a resource wants nothing further, for the reason
§8.8 gives for declined ones: asking about content this node has not taken on
does not terminate. Rejected: reordering automatic intake to prefer reliable
nodes, configured seeds or busier resources. Reliable nodes are never
protocol-privileged (issue #219, decision 1), and any order is still an order;
once nothing is lost past the cap, which resources arrive first decides only
which ones need no click.

**Decision 4 — the cap governs intake, not holdings, and a cap of 0 is a
curated node.** Lowering a cap sheds nothing; a SysOp prunes by excluding, which
is a choice about named resources rather than a second arrival-order rule run
in reverse. A cap of 0 offers everything new and carries nothing unasked, which
is scenario 2's "these ten, nothing else" without a separate mode. A slot freed
by an exclusion is not filled from the offered list: once a resource has been
put in front of the SysOp, accepting it is theirs to do, and an offer that can
silently turn into a carried resource later makes the list untrustworthy.

**Decision 5 — deleting a carried resource hides it; Restore un-hides it; Purge
deletes it (amended 2026-09-26, the maintainer's decision).** Deleting a carried
resource keeps its local row and everything in it and sets `link_hidden_at`:
callers and content administration no longer see it, it is not carried or
declared as carried, no new content is projected into it, and it is recorded as
excluded (`deleted`) -- all in one transaction with its audit entry. An
`Excluded` list under Link status shows it with `[R]estore`, which clears the
mark so the resource is back exactly as it was, and the next sync pass pulls
what arrived meanwhile; and `[P]urge`, behind a typed-name confirmation, which
deletes it for real and leaves it excluded (`purged`). After a purge, and for an
offer declined without ever being carried, `[R]estore` takes the resource on
again from its stored genesis, like an accepted offer. A hidden resource's name
stays taken, and creating or renaming onto it says which excluded resource
holds it.

The first form of this decision deleted the rows and restored by replaying
`link_events`, keeping this node's own users' envelopes and a local-moderation
overlay so the replay could be faithful. Mapping the code showed it could not
be: this node's own posts are keyed by a local content hash rather than their
event's content ID, so replayed they come back detached from their local
accounts; and a carrying node's moderation is not in the signed history at all.
Hiding keeps both exactly, costs one column, and leaves reclaiming the space to
an explicit Purge.

Exclusion applies only to a resource whose *current* origin, resolved as §9.4
resolves it, is another node — not merely one whose genesis came from
elsewhere, since a board transferred to this node makes it the authority peers
depend on for closure, moderator edits and further transfer. Removing a linked
resource this node is the current origin of is not a carry choice and is outside
this decision: for boards it goes through closure or origin transfer (§9.5, §9.4);
linked channels and file areas have no transfer or closure events yet, so their
delete path stays as it is today, a gap this decision records rather than
closes -- deleting such a resource stays a real delete. The SysOp can also
exclude an offered resource without ever carrying it. This answers scenario 5 (it stays gone, and
visibly so) and scenario 3 (pruning frees slots for automatic intake; nothing
that was offered is lost). Excluded and offered resources are never visible to
callers.

**Decision 6 — what a node does not carry, it tells its peers.** Offered and
excluded resources are declared in every `InventoryRequest` so responders stop
sending events under them, which is the fix for #669 and the #630 precedent
(events set aside under probation are declared as seen). The existing maps cannot
say it: their values are known-ID sets, and a responder sends every event whose
ID is absent, so declaring an offered resource's genesis alone still brings
every post. The fix therefore needs an explicit "not carried here" field,
signed, and sent only to a responder that has said it understands it: an older
responder rebuilds the signature payload without an unknown field and would
reject the whole request, and bumping the protocol version would stop hello
from completing, since versions must match exactly. So the responder advertises
an inventory capability — in its hello or descriptor — and the requester sends
the field only where it is advertised. Against an older responder there is no
suppression: nothing it understands can express it, and declaring received IDs
the #630 way would need a retained-ID store for events this node never keeps,
since posts, messages and descriptors under a resource without a local row are
dropped without reaching `link_events`. Mixed versions keep today's behaviour
until the responder upgrades. The declaration costs one resource ID per offered or
excluded resource; the carried maps beside it list every content ID of every
carried resource and are far larger, so keeping the whole request under the
responder's size limit is one problem for both. Issue #685 settled it for both
at once by paging the declaration (§8.8), rather than a separate bound on the
carry-decision record.

**Decision 7 — the bound stays a per-type count, and all three get a
readout.** 500 stays the default, and the declared scale (§2.3) stays small.
Link status shows `carried/cap` for boards, channels and file areas alike, and
the offered and excluded counts beside them. Rejected for now: a storage or
activity bound. Two of the three types are already bounded where their cost
concentrates — `max_remote_files_per_area` for a file area's catalogue and the
node-wide scrollback retention limit for a channel's history — and boards are
not: a carried board's posts are durable, unbounded-lifetime state (§8.9), and
no cap shape changes that, since a board grows after it is accepted. A storage
bound would need the node-wide disk accounting §13.9 lists as its own future
slice; until then a count is the number a SysOp can reason about when choosing
what to accept, and excluding a board that grew too large is the lever.

**Decision 8 — the vocabulary is the one Link Communities will use.** §6.5 says
carrying a Community carries its present and future members with per-resource
and whole-Community exclusions. In these terms a Community subscription is a
standing rule that accepts offers naming that Community; opting one member out
is excluding it; and a resource in no Community stays under the node-wide
automatic intake, so unfiled content is never uncarryable by construction.
Leaving a Community does not shed members already carried, for the reason in
decision 4. Whether a subscription has a bound of its own, and how a subscribed
Community is told apart from a same-named local one, are answered in
issue #1308's entry (Decisions 4 and 5).

**Not done, deliberately.** Offers add a row per genesis this node already
stores, so they grow nothing a peer could not already make it store; bounding
genesis intake as a whole is a separate question, and this decision does not
change it. Bulk actions on the offered list (exclude everything from one
origin) wait until a list long enough to need them exists. Implementation is its
own issue, after #669.
