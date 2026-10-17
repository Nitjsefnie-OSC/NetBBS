# Issue #1298 — cross-posting a message to several boards — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller wants to post one message to several boards without writing it again
on each. Today a post belongs to exactly one board, and nothing in the post
model allows more. `posts.board_id` is a single column. `create_post` derives
the post's content id from a payload that includes `board_id`. The read
cursor, expiry, pinning, moderation, the file rows of #842 and the Link
`board_post` event are all per board. The composer has one destination: the
review screen (`board_flow._review_and_commit` over
`composition.review_composition`) passes `recipient=None`, so its `[T]o` key
is free on a board post. Mail met the same question for several recipients in
#827 and answered it with one copy per recipient, linked by a group id.

**Decision 1 — one copy per board, tied by a group id.** A cross-post writes
one ordinary post on each chosen board, with a nullable
`posts.crosspost_group` naming the group, as `mail_messages.mail_group_id`
ties a letter's copies (#827). Each copy is a post like any other, with its
own content id, because the board is part of it:
- **Replies:** each copy has its own replies. A reply is not cross-posted.
- **Moderation:** each board moderates its own copy. A copy held on a
  moderated board waits there, while the copies on the other boards go up.
- **Removal and pinning:** deletion, takedown, pin and keep act on one copy.
- **Author's changes:** an edit or withdrawal changes one copy. There is no
  "edit everywhere".
- **All or nothing at Post.** Every copy is checked before any is written,
  and all are written in one transaction. The checks are the ones a single
  post meets on each board: `require_write_gate`, `posting_refusal` (#993),
  a closed or hidden board, and the length limit, with a Linked board's
  file lines counted (`body_with_link_text`). A board that refuses is named
  on the review screen and nothing is posted.

Rejected: one post listed by several boards. Every per-board rule above would
have to learn about a post that lives in more than one place, and the post's
content id, which includes the board, would no longer identify it. This is
mail's #827 reasoning, unchanged.

**Decision 2 — any mix of local, Linked and FTN boards.** Each copy leaves
the node the way a post on its board does.
- **Linked:** each Linked copy is its own `board_post`, with an optional
  `"crosspost": "<group id>"` field. As with #711's `layout`, the key is
  omitted for an ordinary post, and §7.5 lets a node that predates it keep
  and relay the signed bytes unchanged, so there is no protocol bump. An
  older node shows the copy as an ordinary post. A newer node that carries
  two copies of one group ties them together locally, under the group id
  scoped by the signer, `<id>@<home node fingerprint>`, so no other node's
  group can share it (mail's rule).
- **FTN:** each FTN copy is a separate echomail message with its own
  `^AMSGID`. Its first body line is `* Crossposted in <ECHO>, <ECHO>`,
  naming only the other echoes of the same FTN network that receive this
  post. Local and Link board names never go to an FTN network.

**Decision 3 — `[T]o boards` on the review screen.** The post review screen
of a new post (prose or art, never a reply or an edit) gets `[T]o boards`, an
`extra_actions` entry as `[A]ttach file` is (#842).
- **The picker:** `pick_item`'s `on_toggle` multi-select, as the bot settings
  use it. The current board is ticked. Boards the caller cannot post to are
  listed muted with the reason and cannot be ticked, as the mail picker
  shows who cannot take a letter (#920). An art post can go only to boards
  that allow color; the others show that reason.
- **The review screen:** a row under the subject lists the chosen boards.
- **Nothing is saved before `[P]ost`.** The choice lives on the review
  screen. A draft keeps its text, as a draft does now.

**Decision 4 — a cap the SysOp sets.** A cross-post reaches at most a
node-wide number of boards, the current one included: 5 by default. The SysOp
sets it from the console, beside the other node-wide limits (§13.10a's rule
that operating policy is tunable without a shell). The picker refuses one
more tick with a line naming the cap.

**What follows from one group.**
- **Read state:** opening one copy marks the copies the caller can see on
  this node read too (`activity.record_post_opened` on each sibling). The
  new-scan totals and "replies to you" count a group once.
- **Search:** `search_posts` lists a group once, by a copy the caller can
  open.
- **Byline:** a copy's byline says "also in X, Y", naming only the boards of
  sibling copies that the reader can open.
