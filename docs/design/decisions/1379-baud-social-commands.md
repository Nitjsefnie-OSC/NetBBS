# Issue #1379 — Baud social commands — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Baud's abilities served individuals: help, reminders, `seen`. Nothing it did
gave a group in a channel a shared moment. Dice, a coin, quotes and quick
polls are what classic channel bots were loved for, and they need no language
model and no access beyond `addressed`.

What existed: Baud parses the first word of the addressed text. Every answer
is a public `reply` with the caller's name in front. Its state is one JSON
value of at most 16 KiB that reminders already use much of. A `!word` goes
only to this node's default bot, and in a Linked channel only the caller's own
node handles their shorthand (#1208 Decision 5). Bot output, door output, MRC
and replay never create requests, so the rule that bots never trigger each
other holds for any text a quote or poll republishes. The addressed line is
itself public, and every request expects an answer within 60 seconds. A bot
could not tell whether a caller is a channel moderator or the guest, nor which
of its channels are Linked. Board polls (#1299) are 2 to 10 answers, one
stored vote per account changeable until close, a secret ballot, and close by
the author or the board's moderators.

**The commands.**
- **`!roll`** is `1d6`; also `!roll NdS`, `!roll dS` and an optional `+M` or
  `-M`, in any case. N is 1 to 20, S 2 to 1000, M 0 to 1000. The reply lists
  each die and the total. Out-of-range input gets a usage reply.
  Randomness is `random.SystemRandom`, keeping Baud stdlib-only. No aliases.
- **`!coin`** answers "flips a coin: heads." and nothing more.
- Results are public and said once; that is the point.
- **Quotes** are per channel: added, shown, numbered, listed and deleted only
  in their channel, as reminders are, so text from a private channel never
  surfaces elsewhere. `!quote add <text>`, `!quote`, `!quote <n>`, `!quote del
  <n>`. 1 to 160 characters.
- **Quick polls** are single-choice, 2 to 6 answers, one open poll per
  channel: `!poll [<1m-24h>] <question> | <a> | <b> ...`, 10 minutes by
  default and 24 hours at most (longer belongs on a board poll). The result
  must fit one line, which is why the answers stop at 6, not #1299's 10: a
  poll's close is a `say`, and `say` stays one line (#1373).
- **Voting:** `!vote <n>`. A voter is the host's caller id, one vote per id,
  changeable until close (#1299 Decision 2). At most 100 voters per poll; the
  result says how many votes were not counted when the cap is hit. A Link
  voter renamed mid-poll gets a new id and could vote twice; for a poll of at
  most a day this is accepted.
- **Closing:** a due poll is closed on a tick with `say`, with the reminder
  backoff and retries, survives restarts, and is removed once published. The
  opener or a moderator may close it early.
- **No bars:** a stored line is the same for every viewer and cannot follow
  a viewer's Unicode setting. Counts and percentages only.
- **The block list does not apply** to quotes, polls or dice: §6.4 excludes
  public channels, and channel moderation is the tool there.

**Caller facts from the host.** The message event gains `caller.moderator`
(a local caller holding moderate permission on that channel, or the SysOp)
and `caller.guest` (the guest account; for a Link caller,
`access.authenticated` false). They are additive and the same for every bot
(#1208 Decision 4). A Link caller is never a moderator at the bot's host. So:
quotes are deleted by their adder or a moderator; polls are closed early by
their opener or a moderator; guests may vote (one vote for the shared
account) but neither add quotes nor open polls, which a later guest call would
otherwise own. A moderator's deletion is audited by Baud's own public line
("Quote #4 deleted by Ann."), since the host cannot audit an action inside a
bot's state.

**Decision 1 — a quote records who added it, and nothing else.** Baud shows
the adder: `#4 "<Quill> the modem is on fire again" (added by Nib)`. A name
inside the text is the adder's claim, and the adder answers for it. The adder
or a moderator deletes it; the quoted person cannot, since nothing proves who
they are.
- Rejected: **a verified quote of a real line** (`!quote add Quill` saving
  Quill's last line): true attribution, but it needs a new granted scrollback
  query and republishes a real person's words without asking. Rejected:
  **anonymous:** nobody is accountable, and a fake "<SysOp> ..." reads as
  genuine.

**Decision 2 — votes are acknowledged privately; results come at close.**
- Baud confirms a vote with a private answer (#1372): `(to you)` "Vote
  counted: 1 Voidrunner. Change it until 20:24." It costs the channel no
  line.
- The opening line says votes are public, since the `!vote` line itself is
  public chat. `!poll` shows the question and the number of voters so far,
  not the tally. The results are said once, at close.
- Rejected: **public acknowledgements:** fifteen votes would use fifteen of
  the channel's sixty lines an hour, starving the close itself. Rejected: **a
  running tally on `!poll`:** non-voters see it too and it nudges late votes.

**Decision 3 — polls and quotes work in Linked channels; remote callers
address Baud explicitly.**
- The host adds `linked_channels` to `hello`. In a Linked channel the poll's
  opening line also gives the explicit form: `Vote with !vote <n>, or from
  another BBS: Baud.bot@Roanoke: vote <n>`.
- A remote caller's `!vote` reaches their own node's default bot, which
  answers "No poll is open here" with a pointer to the poll's bot. Quotes
  work the same way.
- A remote voter whose node cannot take private answers (#1372 Decision 5)
  gets Baud's acknowledgement as a public reply instead.
- Rejected: **local channels only:** the busiest channels would get no
  polls. Rejected: **routing `!vote` to the poll's bot:** it breaks "only
  the caller's own node handles their shorthand".

**Decision 4 — quotes and polls each have a switch, off by default; dice and
coin are always on.** Dice and coin store nothing and only answer the caller,
like help. Quotes keep other callers' words and need moderating; polls
publish later and spend the hourly rate. Each gets its own default-off switch
on the instance, beside greetings and announcements. Help lists only the
enabled abilities.
- Rejected: **a switch per command:** four columns for little control gained.
  Rejected: **one "social commands" switch:** a SysOp could not have dice
  without a quote collection.

**State.** Quotes and polls share Baud's one 16 KiB state value with
reminders, with compact keys and caps of 30 quotes per instance (10 per
adder) and 4 open polls per instance. A write that would exceed the limit is
refused with "Baud's memory is full; ask the SysOp." Keyed state slots, if
ever wanted, belong to the bot API, not here.

**Implementation notes.** One migration: the two switch columns on
`chat_bots`. The memory-full line joins the temperament phrase table (#1381)
as the `full` phrase. Besides `linked_channels`, the `hello` carries `node`,
this node's display name, which the opening line puts after `Baud.bot@`. An
early close is `poll close`. A poll's question is up to 100 characters and
each answer up to 30, so the opening line with the Linked address still fits
one line. A voter is kept as a short digest of the caller id, so 100 Link
voters do not fill the state.
Dice spam can use up the channel's hourly lines and delay a close or a
reminder; `/mute` and `/bots none` are the remedy, and the handbook says so.
