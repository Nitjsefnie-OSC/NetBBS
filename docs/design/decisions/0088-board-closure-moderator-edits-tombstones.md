# Issue #88 — board closure, moderator edits, tombstones — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§9.5 states the complete design and is now fully implemented: `board_closure`
(extends §9.4's own `board_lifecycle_head` chain, terminal), `board_post_
moderator_edit`/`board_post_tombstone` (both extend the same per-post chain
`board_post_edit` already does, origin-signed rather than author-signed, the
latter terminal too). No new authorization primitive — all three verify
against the board's current origin's signing key exactly like `board_origin_
transfer_offer` already does; the two post-scoped types rely on a local
`BoardPermission.EDIT`/`DELETE` check already made on the origin node, before
`netbbs.link.boards.queue_board_post_moderator_edit_if_linked`/`queue_board_
post_tombstone_if_linked` ever builds the event — never a new gossiped grant.

`posts.tombstoned_at` is a plain nullable `ALTER TABLE ADD COLUMN`, not a
`CHECK`-widening rebuild: an earlier migration (post-`root_post_id`) already
found and documented that `posts` cannot safely go through the usual
drop/rebuild pattern, since it is a live *self-referencing* FK parent
(`parent_post_id`/`root_post_id`/`edit_of_post_id` all reference `posts.
post_id`) — the additive-column path sidesteps that risk entirely rather
than re-testing it. `netbbs.boards.posts.tombstone_post` is a new local
function, not a repurposed `delete_post`: it inserts a further
content-addressed revision (placeholder subject/body, `tombstoned_at` set)
rather than removing the row, so the edit chain and any reply's
`parent_post_id` stay intact — `delete_post` itself is unchanged, still
reserved for a still-`'pending'` post's rejection.

**Live UI wiring, stated explicitly:** `board_closure` and `board_post_
moderator_edit` reach real interactive call sites this issue — a `[C]lose`
option on the board admin screen (`netbbs.net.admin_flow`, gated the same way
`[T]ransfer origin` already is: origin only, board not already closed), and
the existing `[E]dit` flow (`netbbs.net.board_flow._edit_existing_post`)
building a moderator-edit event instead of a self-authored one whenever
`edited_by` isn't the post's own author and this node is the board's origin.
`board_post_tombstone` did not have any existing "delete an approved,
already-published post" UI action to extend (the only existing `delete_post`
call site handles pending-post rejection, which never reaches an already-
`board_post`-queued row) — a new `[T]ombstone` option was added alongside
`[E]dit` for exactly this reason, gated on `BoardPermission.DELETE`.
