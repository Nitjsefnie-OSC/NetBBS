# Issue #675 — an author's withdrawal on the Link

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

An author may withdraw their post: its text becomes "[withdrawn by author]"
(§6.1). An ordinary author edit to that text would reach every node, but a
node that carries the board with its own moderation, or that holds this
author's posts for trust review, holds an incoming author edit for approval.
There the withdrawn text would stay up until that node's moderator acted.
Normative description: §6.1.

**Decision 1 — an optional `withdrawn` field on `board_post_edit`.**
`"withdrawn": true` on the author's edit; omitted otherwise, never `false`,
per §7.2's omission rule. It is signed by the author's home node like any
author edit, so it needs no new authorization. No `netbbs_protocol` bump, for
the same reasons as issue #711's `layout`: §7.5 allows optional fields that old
peers can safely preserve, and they do. A node that predates the field
verifies and relays the edit unchanged and shows it as an ordinary edit, held
where it holds edits. That is the old behaviour, so nothing gets worse there.
Rejected:
- a new event type, which older nodes would store opaquely and never show
  (§7.5);
- reusing `board_post_tombstone`, which is origin-signed and terminal; a
  withdrawal is neither.

**Decision 2 — honored only for a withdrawal and nothing else.** A carrying
node lets the edit through without local moderation or a trust hold only when
it takes the text away and changes nothing else: the body is exactly the
placeholder and the subject is the predecessor's. Otherwise the flag would
carry new text past every node's moderators. Anything else carrying the flag
is an ordinary edit, moderated as one. A local tombstone still ends the chain,
and a post nobody here approved stays unpublished either way.

**Decision 3 — it clears the pin and the keep.** On every node, locally
(`posts.withdrawn`, `trg_posts_withdrawal_clears_flags`), so a withdrawn post
neither stays at the top of a board nor outlives its expiry.
