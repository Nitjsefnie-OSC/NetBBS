# Issue #1299 — polls on message boards — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller asks a question with several answers, other callers vote, and
everyone can see the tally. Posts are text only today. A poll needs its own
storage, because answers and votes are not post text. #842 already set the
pattern for a post with something attached: rows beside the post, keyed by its
content id, and cleaned up on every hard delete by
`forget_orphaned_post_refs_without_commit`. The board list's keys are `P A D
M Q F R`, and the reader's are `R M G H W E T O K N P B`, so `L`, `V` and
`C` are free on the screens that need them.

**Decision 1 — a poll is a post with side tables.** A poll is an ordinary
post plus `board_polls`, `board_poll_options` and `board_poll_votes`, keyed
by the root post. The post's subject is the question, and its body introduces
the poll.
- **Creating:** `[N]ew poll` on a board's list opens a poll draft editor
  (`edit_resource_draft`): the question; 2 to 10 answers; choices, either one
  or up to N; a closing date, 7 days ahead by default; and when results show
  (Decision 3). Then the body editor, then the review screen, then
  `[P]ost`. (Decided as `Po[l]l`; built by #1332 as `[N]ew poll`, since a key
  is its label's first letter, §3.5. `[V]ote` is built as a draft of ticked
  answers with `[V]ote` as its save, since a picker cannot commit several.)
- **Changing:** answers and the choice mode lock at the first vote. The
  closing date stays editable. A text edit is an ordinary revision.
- **Moderated boards:** a held poll cannot be voted on until it is approved.

**Decision 2 — who votes, and who can see a vote.**
- **Voters:** anyone who may post on the board, the gate `[R]eply` uses
  (`require_write_gate`, and on a Linked board `posting_refusal` for a
  reply). `[V]ote` on the reader opens the answers as a picker, with the
  caller's current vote ticked.
- **Changing a vote:** a vote is stored per account, so it can be changed
  until the poll closes.
- **Secret ballot:** no screen shows who voted what, not to the author, not
  to a moderator, not to the SysOp. Screens show totals and the number of
  voters only. The node's database does hold each account's vote, and the
  handbooks say so plainly: a SysOp with the database file can read it.
- **Guests:** the guest account is one account, so it holds one vote in
  total. A guest call cannot change a vote an earlier guest call cast
  (#1075, `authored_earlier_by_shared_account`).

**Decision 3 — results, and how they draw.**
- **When:** by default a caller sees the results after voting, or at once if
  they cannot vote (the poll is closed, or they may not post). The author may
  choose "only after close" for a poll instead. Before results show, the
  reader says when they will.
- **Wide (60 columns or more):** one row per answer, with its label, a bar,
  the count and the percentage of voters. A long label wraps under itself.
- **Narrow:** two rows per answer, the label, then the bar with count and
  percentage.
- **Bars:** `█` and `░`, or `#` and `.` with Unicode style off.

**Decision 4 — polls on Linked boards, with signed votes.** A poll on a
Linked board is offered and carried. Polls are never offered on FTN boards,
which have no way to carry one.
- **The poll travels with the post:** the `board_post` carries an optional
  `"poll"` field with the answers, the choice mode, the closing date and the
  results setting. An older node shows the post without its poll. Since the
  root event is immutable and votes may already exist on other nodes, a
  Linked poll's answers and choice mode are fixed when it is posted. Only
  the closing date changes, carried by the author's `board_post_edit` or a
  moderator's `board_post_moderator_edit` (§9.5).
- **Votes are events:** `board_poll_vote` names the board, the poll's root
  post, the voter and the chosen answers, and is signed by the voter's home
  node like a post. Each node keeps one vote per author identity, and the
  latest wins. A vote dated after the poll closes is not counted.
- **Counting:** votes are trust-filtered like posts (§12): a vote from a
  quarantined or blocked author or node is not counted. Each node also counts
  at most a fixed number of voters per other home node per poll (50), and
  keeps at most 5,000 voter rows per poll.
- **What the cap can do:** a hostile node can mint as many accounts as it
  likes, and its votes are signed correctly. The cap limits how far it can
  move a tally. It cannot prevent it. Each node's tally is its own view, as
  its post list is: it can differ from another node's when a node passes the
  cap or is trusted differently. The results say how many votes were not
  counted.
- **No protocol bump:** the vote event names a `board_id`, so an older node
  that carries the board relays it opaquely (#1022, §7.5).
- **Secrecy over Link:** a vote on a Linked board is not secret from the
  network. A signed vote has to name its voter, so every node that carries
  the board holds who voted what, as it holds who wrote each post. NetBBS
  screens still show no voter list on any node, but an operator of any
  carrying node can read the votes from their database. On a local board
  only this node's operator could; on a Linked board every carrying node's
  operator can. Before the first vote on a Linked poll, the poll says: "Your
  vote is shared with every BBS that carries this board." This follows
  §10.2's rule that Link privacy is described as it is, never more. Rejected: each node
  signing a tally of its own callers. It would hide voters from peers, but a
  tally cannot be trust-filtered per author, and a node could report any
  number.

**Moderation.**
- **Delete:** deleting the post deletes its poll and votes, cleaned up as
  #842's file rows are on every hard delete.
- **Close:** the author and the board's moderators get `[C]lose poll`.
- **Withdraw and takedown:** a withdrawal or a takedown closes the poll and
  hides its totals.
- **Expiry:** the poll goes when the post expires and is deleted
  (`sweep_expired_posts`).
