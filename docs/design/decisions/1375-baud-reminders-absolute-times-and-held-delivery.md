# Issue #1375 — Baud reminders: absolute times and delivery when the owner is back — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Baud's `remind` took only durations (`<1s-30d>`), and a due reminder was said
publicly in its channel whether or not its owner was there. "Remind me at
20:00" made the caller work out the minutes, and a reminder said to an empty
room was noise the owner missed. This decision also settles the question it
shares with `!tell` (#1376): how a bot delivers a line when someone next joins
a channel.

What existed: Baud is a stdlib program with no database or filesystem access.
It learns everything from `hello` and events, and its `hello` carried no
timezone. Reminders live in its one state value (at most 16 KiB), capped at 8
per caller and 48 per instance, 1 to 160 characters, and are said with `say`
on the one-second tick, with a backoff from a minute doubling to an hour and a
drop after 8 refusals. The only presence a bot could know came from the
per-channel join/leave grant, which has no snapshot, so a restarted bot does
not know who is already in a channel. Polling "is X here?" is impossible under
the 240-actions-a-minute limit. The host knows presence exactly.

**Shared decision — the host holds the line (also used by #1376).**
- **A new action, "say for a caller":** like `say`, but naming one caller and
  an expiry. If that caller is in the channel now, the host publishes the
  line at once. Otherwise it stores the line in a small host table and
  publishes it on the caller's next join of that channel, and tells the bot
  the outcome (delivered, or expired).
- **Why the host:** Baud keeps the `addressed` scope and needs no join
  stream; a restart leaves no gap, because the host knows who is present; and
  the same primitive carries `!tell`, bound to the request token, so the
  sender's identity is proven by the host and blocks are checked there
  (#1376).
- **Delivery:** a held line is published only in the channel it was made for,
  so text from a private channel never surfaces in another one. Being in the
  channel counts as present, away or not, and the line lands in scrollback.
  It is delivered once per join of the account, not once per session. A
  publication counts against the bot's hourly output in that channel like any
  line; a refused publication stays held until its expiry.
- **Private delivery:** once private answers exist (#1372), the action also
  takes a private flag: the line is then shown on join as a `(to you)` line
  to that caller only and debits the per-caller private limit. Baud's
  reminders and tells are delivered publicly, as the mockups chosen for this
  issue and #1376 show; whether Baud should switch either to private
  delivery is left open, for a later decision.
- **Bounds:** the table is bounded per bot and per caller by the limits of
  each feature that uses it (here and in #1376), and expired rows are pruned.
- Rejected: **the presence-event grant plus a presence-conditioned `say`.**
  No migration, but the SysOp must grant join and leave in every reminder
  channel so the bot sees everyone's joins, and `!tell` could not recheck a
  block made after the tell without trusting the bot's word for the sender.
  Rejected: **unchanged, always say at the due time:** solves neither this
  issue's second half nor #1376.

**Decision 1 — four absolute forms, besides durations.**
- `remind HH:MM <text>`, `remind tomorrow HH:MM <text>`, `remind mon HH:MM
  <text>` (any of `mon` to `sun`) and `remind YYYY-MM-DD HH:MM <text>`.
- **The clock:** the node's display clock (§6.3: one clock, no per-caller
  zone). The `hello` gains the zone's IANA name, since Baud has no database
  access; when the SysOp changes the display zone, running instances
  restart, as they do when the model-service switch changes. Due times stay
  stored as UTC instants, so a zone change never moves a saved reminder.
- **24-hour `HH:MM` and ISO dates only,** as the backup schedule reads them.
  The display format's day and month order is configurable, so `01.11`
  against `11/01` is ambiguous and is refused.
- **A time already past today means tomorrow;** an explicit date in the past
  is refused. A weekday means the next such day, today if the time is still
  ahead.
- **Gaps and repeats:** a time in a spring-forward gap is read at its
  pre-transition reading, and a repeated time at its first occurrence
  (`backup_schedule`).
- **The 30-day horizon stays** for absolute times; the caps assume it.
- **The confirmation echoes the instant** in the node clock with the zone
  abbreviation: "Saved reminder #3 for Fri 20:00 CET."
- Rejected: **time and full date only:** "Friday" would have to be typed as
  a date. Rejected: **12-hour forms** (`8pm`): a second spelling of every
  time, where the rest of NetBBS takes 24-hour input.

**Decision 2 — a held reminder waits 7 days, then is dropped silently.**
- A due reminder whose owner is not in its channel is held through the shared
  action, with a 7-day expiry. When the owner returns within it, Baud says it
  with when it was due:
  `[BOT] <Baud.bot> Nib: reminder #3 (due 18:00, held until you came back): net meeting 20:00`.
- A reminder a week stale has lost its point. "Dropped" cannot be told to
  someone who never returns, so nothing is said.
- `reminders` lists a held one as "waiting since 18:00", so its owner can
  cancel it meanwhile.
- **Held reminders still count** toward the 8 per caller and 48 per
  instance.
- Rejected: **hold until the 30-day horizon:** a month-old "net meeting at
  20:00" is noise. Rejected: **hold, then say it publicly anyway:** the
  empty-room noise the issue wants gone.

**Implementation notes.** One migration: the host table of held lines (bot,
channel, caller id, text, created, expires, and for #1376 the sender), shared
with #1376. Delivery runs from the local join path and the Linked join path
without holding the chat input lock, with the bounded `put_nowait` pattern.
Tests cover both DST transitions.
