# Issue #1377 — paging the SysOp — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller in trouble should be able to call for a human from chat, the place
they are most likely to be when something goes wrong. NetBBS had the pieces
but no way to ask: the Staff list and the away notice (§5.6, #836), the live
monitor and break-in (#761), and the mail-arrival watcher's rule for live
notices (#823). There was no page feature and no earlier design for one.

The issue proposed `!sysop <reason>` as a Baud ability. A `!` line is an
ordinary chat line before any bot sees it: `record_message`, the Link queue,
the live push and the broadcast all run before `_address_bot`, and in an MRC
channel the line goes to the hub as well. A reason given that way is in the
channel's scrollback and on every Linked node before Baud can act on it. A
`!` command also needs an enabled bot and a channel default.

**Decision 1 — a host chat command, `/sysop <reason>`, that Baud points to.**
- **The command:** `/sysop <reason>` is a chat command of the host, like
  `/msg`. The reason never enters the channel, its history, Link or MRC. It
  works in every channel, MRC rooms included, and on nodes with no bot.
- **The answer:** a private titled block, `Paged the SysOp`, drawn with
  `chat_reply` like `/who`. It says who was told (or that the page waits in
  Pages), the away notice where it applies (Decision 4), and the reason, and
  ends "Only staff see your reason; nothing was said here."
- **The reason:** required. One line of plain text, sanitized, no pipe codes,
  at most 160 characters (the reminder cap). A bare `/sysop` shows its usage,
  the way a bare `/msg` does.
- **Baud:** its `help` lists `/sysop`. A `!sysop` line gets a pointer from
  Baud, privately once private answers exist (#1372), telling the caller to
  use `/sysop <reason>` because it stays private. Baud pages nobody: no bot
  action pages staff, so its pointer must not claim it passed anything on.
  The `!sysop` line itself has already gone out publicly, which is why
  `/help`, Baud's help and the handbook lead with `/sysop`.
- **Whose staff:** the caller's own node's. Staff permissions are not carried
  over Link (§5.6), and a host command is local by construction, so no Link
  frame is needed. A Link caller in a Linked channel pages their home node's
  staff. `Baud.bot@Other: sysop` gets the same pointer and pages nobody.
- **Never from a bot.** A page comes only from an accepted live human input.
  No bot action creates one, and a help-desk model (#1382) may tell a caller
  about `/sysop` but never pages by itself.
- **Staff paging their own node:** refused with "You are staff here." Paging
  yourself has no use.
- Rejected: **a Baud ability on a new `page` action.** The reason would be
  public before Baud saw it, the page would work only where Baud is the
  default, and a bot able to page staff is a harassment tool needing its own
  grant. Rejected: **both.** More API surface for no caller gain.

**Decision 2 — a Pages queue in the console.**
- **Storage:** a page is a row in a new table (id, the account or the guest
  session's label, where the caller was, channel, reason, created, and who
  handled it, when and how). Open pages are kept until handled, handled ones
  for 30 days, under a row cap.
- **Where it shows:** the console dashboard's ATTENTION panel and the Staff
  console's landing view count open pages, and the main menu tells each
  recipient "1 page waiting: SysOp → Pages." above its prompt, as the
  accounts-awaiting-approval line does.
- **Live notice:** each online recipient gets a live notice, with the chat
  bell (#1302) where chat is open, routed exactly as #823 routes mail
  arrivals: chat and the Monitor show it at once, other screens get a queued
  announcement, and nothing is written into a door or an editor.
- **Answering:** each page offers `[C]hat` (break-in, #761, while the paging
  session is still connected), `[M]ail reply` (members only: a guest has no
  mailbox) and `[D]one`. The queue is shared: one person handling a page
  clears it for everyone. Snoop is not offered from a page.
- **What is logged:** the Monitor's event tail and the node log record the
  place, never the reason (#761 Decision 2, "places, never content"): for
  example `page: Nib (Chat › Lobby)`. Handling a page is audited with the
  actor.
- Rejected: **system mail to each recipient.** A reply to system mail is
  refused, every recipient clears a separate copy, and a guest's page cannot
  be answered. Rejected: **live only.** A page with nobody online would be
  lost, which breaks "the SysOp will see this".

**Decision 3 — fixed limits; guests only while someone can answer.**
- **One open page per caller.** A new `/sysop` while one is open replaces its
  reason and adds no second page.
- **One page every 10 minutes.** The limits hold per account across all of a
  caller's sessions. Guests share one account, so for them they hold per
  session.
- **Guests:** a guest may page only while a recipient is online. A stored
  guest page has no mailbox to answer to, and the guest cannot be found
  later. The refusal says so and suggests signing up.
- **No settings.** The numbers are fixed in code.
- Refusals are a `Page not sent` block saying why and when paging works
  again.
- Rejected: **a SysOp-set policy** (who may page, a cooldown): more knobs for
  a feature whose abuse posture the fixed limits already set. Rejected:
  **members only:** it turns away the callers most likely to need help, such
  as a guest whose signup failed.

**Decision 4 — the recipients are usable SysOps and anyone with a staff
permission.**
- These are the SysOp and Staff rows of the Operators list (§5.6), the people
  with node-wide reach. Moderators are not paged: they are scoped to
  resources, and a page is usually about the node.
- Recipients are resolved when a page is delivered and the queue is shown to
  whoever qualifies now, so a demoted or disabled account stops seeing pages
  without any data change.
- **Away staff are still paged.** Being away changes no permission and a
  SysOp who is away may still look in (§5.6). When every recipient is away
  the caller's answer names the one expected back first, in the wording of
  the pending-account message (`approvers_away_line`): their return date or
  "away since", and their message.
- **The answer never claims more than it says.** "Online" means a live
  session. When every online recipient is in a door, the answer says they
  will see the page when they leave it.
- Rejected: **SysOps only:** a helper made staff so the node is covered while
  the SysOp is away would never hear a page. Rejected: **the channel's
  moderators as well:** see above.

**The block list.** A page is addressed to the node's staff, not to a
person, and §6.4's block list does not apply to it: the shared queue keeps
it whoever the caller is, as it keeps a SysOp's view of any account.

**Implementation notes.** One migration (the pages table). The answer and
refusal texts are caller-facing text that #1392 may later make editable.
