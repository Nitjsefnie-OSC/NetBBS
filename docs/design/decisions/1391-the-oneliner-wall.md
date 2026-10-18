# Issue #1391 — the oneliner wall — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A oneliner wall is a short list of the latest one-line messages callers leave
for whoever calls next. It is one of the most characteristic BBS features and
the lightest way to leave a mark on a node: lighter than a post, and not tied
to being online at the same time as anyone. NetBBS had nothing like it.

The wall copies the Recent callers roll line for line (§3.3): one renderer
for the splash after sign-in and for the menu screen, a splash that is a
non-event when empty or too small, a screen that always draws something, and
a SysOp toggle (shown, plain, hidden) that governs the splash alone. The
logon sequence after sign-in is: SysOp onboarding, the one-time character-set
question, the Welcome line queued above the first menu, the Recent callers
splash, the one-time keys notice, then the main menu. Logon art comes before
sign-in, and a wall there would show callers' words to anyone who connects,
so the wall stays after sign-in. The welcome letter (#1352) is mail, not a
logon screen.

**Decision 1 — one screen at logon, shared with Recent callers.**
- **One frame:** the wall is the lower section of the Recent callers frame,
  under a `O N E L I N E R S` rule, so a call has one pause and both lists
  share the height budget. This is the classic "last callers and oneliners"
  screen.
- **Each section on its own:** each section follows its own SysOp toggle and
  is left out when it is empty. With both out, there is no screen.
- **The keys:** `[A]dd a line   [Enter] Continue`. Any other key continues.
  The logon never asks "Add a oneliner?": §3.5 allows a yes/no question only
  as the last keystroke before an irreversible act, and a hotkey on the panel
  is the allowed shape.
- **Sized to the terminal:** at most 10 lines of the wall at once, and a
  section that cannot fit even one row is left out. At 40x12 that is about
  two callers and two lines.
- Rejected: **a panel of its own after the roll:** a second pause on every
  call. Rejected: **nothing at logon:** the "sign of life" is lost for
  callers who never look.

**Decision 2 — the full wall is reached from `[R]ecent callers`.**
- `[R]ecent callers` draws the same combined panel as the logon. Its bar
  gains `[A]dd a line` and `[O]neliners`, which opens the full wall. The
  menu brief becomes "Who called, and what they said". No new main-menu
  letter is spent.
- **The full wall:** newest first, rows numbered 01-99, `<` `>` paging,
  `[/] Find` and `?` help (§3.5, §3.6), with `[A]dd a line  [D]elete
  [B]ack`. An empty wall says "Nobody has left a line yet." and still offers
  `[A]dd a line` and `[B]ack`. The breadcrumb is `Oneliners`; the logon panel
  stays under "Logging in".
- Rejected: **a `[Q]uips` entry of its own:** it spends a free letter on a
  less familiar word. Rejected: **`[O]neliners` on the main menu:** `O` is
  `[O]perators`, and every synonym for that collides with another letter.

**Decision 3 — 60 columns; one line per call, at most 3 a day.**
- **Length:** at most 60 display columns, which fits beside a 12-column name
  in the 78-column frame.
- **Rate:** one line per call, and at most 3 per account per day. The daily
  cap bounds the shared guest account and a reconnect loop. The numbers are
  fixed in code, as the bell's are. A refusal says which limit was reached:
  "You've added a line this call." or "You've added 3 today; the wall takes
  more tomorrow."
- Rejected: **one per account per 24 hours:** a regular who calls twice a
  day speaks once, and it caps guests hardest. Rejected: **SysOp settings
  with optional approval:** a settings screen and an approval queue kind,
  against the "lightest possible mark" the wall is for.

**Decision 4 — its own small table.**
- A `oneliners` table: id, `author_user_id` (`ON DELETE SET NULL`), the
  author label, the text and `created_at`. The newest 200 lines are kept and
  older ones pruned at insert.
- Rejected: **a reserved board with one-line posts** (the polls precedent,
  #1299). Every board surface (the Boards list, New scan, Find, the access
  map, node pages, approvals) would need a "not this board" branch, and a
  subject plus a body is the wrong shape for one line. A board's one gain, a
  Link-wide wall riding linked-board carry, is a later issue (below).

**Writing a line.**
- One single-line prompt, `Your line (60 max, Esc cancels):`, with the
  scrolling window. Esc or a blank answer cancels. Nothing is confirmed,
  since a line can be deleted. "✓ Added to the wall." is carried into the
  redraw.
- Plain text only: sanitized, one line, no colour codes, whitespace
  normalized.
- **Who may write:** a node-wide minimum level, default 0, set in the
  console beside the wall's toggle, like the mail level. A guest writes only
  if the guest account meets it; the console says "set it above the guest's
  level to keep guests read-only", as the node-map setting does. No code
  branches on guests (§4.6).
- `[A]dd a line` is hidden from a caller below the level, and shown and
  refused with its reason when a rate limit is reached, because a key that
  vanishes after use reads as broken.

**Deleting and moderation.**
- The author deletes their own lines. A SysOp, or a staff member who may
  moderate everything (§5.6), deletes any line. There is no per-wall
  moderator grant, since there is no object to grant on.
- `[D]elete` asks for a row number, then "Delete this line? (y/N)", the
  allowed question before an irreversible act. `[D]elete` is shown only while
  the viewer has a line of their own on the page, or may delete any.
- A moderator's deletion is audited with the text in the detail. The author
  gets no notice.
- The block list (§6.4) does not hide lines: the wall is public like a
  channel, and deletion is the tool.
- **The author's name:** the account name, kept as a label so a line
  outlives its account, as a post does. The Recent callers privacy choice
  does not apply: writing a line is a deliberate public act, as posting is.

**The SysOp toggle.** Oneliners after login: shown, plain or hidden, beside
Previous callers in System settings, audited. It governs the logon panel
only; `[R]ecent callers` and the full wall ignore it.

**Not now.**
- **A Link-wide wall** is a separate, later issue. It needs a signed event
  kind of its own and the carry and moderation rules of linked resources
  (§9.3, #1357). Nothing here blocks it.
- **Node pages** (#1165) do not show the wall: callers wrote for callers,
  and publishing it would need an opt-in of its own.

**Implementation notes.** One migration (the table) plus node settings for
the toggle and the level. The rate limit counts the author's rows of the
last day plus a per-session flag, with no extra table. When both sections
share the frame, its style follows the Previous callers setting.
