# NetBBS v7.31.0

This release is the "Baud grows up" round. A caller can now ask a chat bot
privately, and bots can answer with titled blocks. Baud is rewritten on a
new bot developer kit and learns absolute-time reminders, `!tell`, dice,
quotes and quick polls, a help-desk mode for its optional model
conversation, and announcing door game results. A SysOp can edit Baud's
phrases and try any bot on a scratch node from the console.

Beyond Baud, callers can page the SysOp with `/sysop <reason>`, leave a line
on the new oneliner wall, and a SysOp can schedule announcements into a chat
channel.

**This release migrates:** the node database goes from schema 139 to 148.
Rolling back to v7.30.0 needs the backup taken before the upgrade (see
below).

**Versions:**
- The Link real-time protocol stays at version 4 (`REALTIME_PROTOCOL_VERSION`
  4, `NETBBS_PROTOCOL_VERSION` 1). The new Link behaviour is advertised as
  three capabilities, `chat_bot_private_v1`, `chat_bot_blocks_v1` and
  `chat_announcements_v1`, and is only sent to nodes that advertise them.
- The bot API stays at version 1 (`BOT_API_VERSION` 1). It gains actions,
  `hello` fields and `message` event fields, all additive (see "For bot
  authors" below).
- The door API goes from 4 to 5 (`DOOR_API_VERSION` 5) for door game
  events.

It is a minor release because it adds features and migrations, and nothing
that worked before stops working, apart from the door gate fix (#1401)
below.

## For SysOps: what to switch on

Almost everything new is off until you turn it on.

| Feature | Default | Where |
| --- | --- | --- |
| Baud's phrases | The temperament's presets | **Content ▸ Open chat bots**, a bot, **Phrases** |
| Baud quotes, Baud quick polls | Off | The bot editor's **Social** section |
| Dice and coin (`!roll`, `!coin`) | Always on | Nothing to set |
| Reminders at a time, `!tell`, private answers | On for any Baud | Nothing to set |
| Help-desk mode | The default **Mode**, once model conversation is set up | **Content ▸ Open chat bots ▸ Model settings** (model conversation itself stays off) |
| Tally | Installed, never created | Create a bot with Tally's program arguments |
| Try | Console only | <kbd>T</kbd> **Try selected**, or the editor's **Try** field |
| Door game events | Off per door | **Content ▸ Doors**, a door, <kbd>O</kbd> **Outbound**, <kbd>E</kbd> **Events on**  |
| Baud door announcements, `!top` | Off | The bot editor's **Door games** section  |
| Scheduled announcements | None | **Content ▸ Chat channels**, a channel, <kbd>N</kbd> **Notices** |
| Announcements to other nodes | Local only | **Other nodes (whole channel)** on an announcement |
| `/sysop` paging | On, no setting | <kbd>P</kbd> **Pages** on the console and the Staff console |
| Oneliner wall | **On** (neon), shown once someone adds a line | **Settings ▸ Wall of oneliners**; **Oneliner level** (0) under **Settings ▸ Limits & retention** |

Two things are on from the start:
- **The oneliner wall.** It stays out of sight until a caller adds a line.
  Its level is 0, so guests can add lines too. Set **Oneliner level** above
  the guest account's level to keep guests read-only.
- **`/sysop`** works in every chat channel as soon as you upgrade.

## Private bot replies (issues #1372 and #1403, PRs #1420 and #1422)

A caller can ask a bot without the channel seeing it:

- `/msg Baud.bot <text>` asks one question privately.
- `/private Baud.bot` sends every plain line to the bot privately until
  `/close` or until the caller leaves the channel. `Baud.bot@Node` works for
  both.

A private answer is drawn as `[20:14] [BOT] <Baud.bot> (to you) ...`. It
doesn't ring the bell, it stays in the caller's own scrollback (PgUp,
redraw), and it is never stored as a channel message, so it is in no
history, search or replay. A bot the caller muted or hid with `/bots`, or one
not allowed in the channel, is refused with the reason.

- **Limit:** 30 private answers per caller per hour per bot, apart from the
  channel's lines per hour.
- **Model conversation** keeps separate contexts for a caller's private and
  public exchanges, each with its own disclosure.
- **Baud** answers `help` privately whenever it can reach the caller that
  way, ending `Ask privately: /msg Baud.bot <text>.` A private `reminders`
  lists the caller's reminders in every channel. Other answers follow how
  the request was asked. A caller whose node can't take a private answer
  gets it publicly, ending `Replies are public.` as before.

**Over Link:** capability `chat_bot_private_v1`. `bot_request` gets an
optional `"private": true` and `bot_result` an optional `"private"` answer.
Neither is sent to a peer without the capability. A private question to a
bot on such a peer is refused before it is sent, and that peer's bots see
`private: false` for its callers. The caller's own node checks its mute and
`/bots` setting again before showing a private answer.

## Titled block replies (issue #1373 and #1404, PR #1424)

A bot can answer with a titled block instead of one line: a title of up to
40 characters and up to 12 rows, each an optional label of up to 24
characters and a text of up to 200. It is drawn as the `/who` and MRC
blocks are, titled `[BOT] Name.bot · <title>`. A block taller than the chat
area is paged. Mutes and `/bots` filter it like any bot line.

NetBBS builds a one-line fallback, `Title: label text; ...`, cut to 500
characters. That line is what counts against the hourly limit, what search
matches, and what older nodes show. Baud's `help` is now a `Commands` block
on a host that takes blocks.

**Over Link:** the signed `channel_message` event gains an optional `block`
field, kept only for a `.bot` author and refused above 12 KiB. Capability
`chat_bot_blocks_v1` adds the block to live frames, scrollback snapshots and
private answers, sent only to peers advertising it. A node without it shows
the fallback line.

## The bot developer kit, Tally and Try (issue #1374 and #1402, PR #1419)

- **`botkit`** (`src/netbbs/bots/botkit.py`) is one standard-library file
  that imports nothing from NetBBS, so it can be copied next to a bot. It does
  the handshake, matches results to actions by callback, dispatches events
  to `on_<type>` methods, and keeps a state cache that only changes when the
  host accepts a save. The bot API guide has a new "Writing a bot" section,
  and the Developer Handbook a "Developing a chat bot" section.
- **Baud** is rewritten on `botkit`. Its commands, phrases and saved
  reminders are unchanged.
- **Tally** is a second example bot. It counts karma from `name++` and
  `name--` in channels it may read in full, and answers `!karma [name]`. It
  keeps at most 200 names and counts at most 5 per line; self-karma is
  ignored. It is installed but never created for you: create a bot with
  `["<the node's python>", "-u", "-m", "netbbs.bots.tally"]` under **Program
  arguments**, and grant **Read whole channels** where it should count.
- **Try** runs a bot on a scratch node with its own database, state and
  channel in a temporary directory. **Content ▸ Open chat bots ▸ Try
  selected** (<kbd>T</kbd>) tries the saved instance. The **Try** field, last
  in the bot editor, tries the unsaved draft. Type lines as in chat, act out
  events with `/join`, `/leave`, `/post` and `/file`, and see each event, the
  bot's actions and their results, the chat lines and the last 8 KiB of the
  program's stderr. Model requests are rehearsed, never sent. Nothing reaches
  your node or its callers. Try is console-only.

## Baud's phrases (issue #1381, PR #1399)

The bot editor has a **Phrases** section: **Greeting**, **Help opening**,
**Busy message**, **Reminder-limit message** and, with the social commands,
**Memory-full message**. A blank field shows the temperament's text as
`(preset) ...` and uses it. A phrase is plain one-line text. `{name}` (the
caller's name) is its only placeholder, and `{{` and `}}` are literal braces.
Save refuses anything else, and the draft is kept. The bot's `hello` carries
the overrides as `phrases`.

## Reminders at a time, held lines and `!tell` (issues #1375, #1376, #1405 and #1406, PR #1421)

**Reminders.** Besides durations, `remind` takes `HH:MM`, `tomorrow HH:MM`,
`mon`-`sun HH:MM` and `YYYY-MM-DD HH:MM`, in the node's display clock.
- A time already past today means tomorrow. A weekday means the next such
  day, today if the time is still ahead.
- 24-hour times and ISO dates only: `01.11` is refused as ambiguous. A past
  date is refused, and the horizon is 30 days.
- The confirmation names the time, for example `Saved reminder #3 for Fri
  20:00 CET.`

A reminder whose owner isn't in the channel when it falls due is held, for up
to 7 days, and said when they next join that channel: `reminder #3 (due
18:00, held until you came back): ...`. `reminders` shows it as waiting, and
cancelling withdraws it. Held reminders count toward the limits of 8 per
caller and 48 per bot.

**`!tell`.** `tell <name> <text>` leaves a note for a local caller, said in
that channel when they next join it: `Nib: Quill asked me to tell you (3h
ago): ...`. `tells` lists your waiting notes in that channel, and `cancel
tell <id>` withdraws one.
- The text is at most 160 characters, and a note waits at most 14 days.
- A sender may have 3 waiting, a recipient 5, a bot 32.
- Names with `@` are refused. If the recipient is already in the channel,
  nothing is stored.
- A caller who blocks you, or takes no direct messages, gets no note. This is
  checked when the note is left and again when it is delivered. A refusal is
  told to the sender privately and never shown on the SysOp status line.

Held reminders and notes wait in the node database, not in the bot's state,
so they are in node backups with it.

## Help-desk mode (issues #1382 and #1411, PR #1423)

**Content ▸ Open chat bots ▸ Model settings** has a new **Mode**: **Help desk
(node handbook)**, the default, or **Companion (open chat)**. Model
conversation itself stays off until you set it up, as before.

In help desk, NetBBS adds to each request:
- up to three sections of the User Handbook that best match the question,
  under a 4,000-byte cap. The handbook copy ships with NetBBS; nothing is
  fetched;
- node facts, at most 1,500 bytes: the node name and tagline, its SysOps,
  whether it has MRC, browser links, doors and FTN, and its boards and file
  areas. A public answer, or any answer to a Link caller, names only what
  everyone can read; a private answer names what the asking caller can see;
- the `stats` answer, if the bot holds that grant.

The answer ends with its source, such as `(Handbook: Exchange files. May be
wrong; ? for help)`. Each mode has its own default system prompt. A prompt
still at the shipped default follows the mode, and one you wrote is kept.
Changing the mode shows callers the disclosure again. The token budgets
count the grounding: allow for about 5,900 more tokens per help-desk request
in **Node daily token budget**.

## Social commands (issues #1379 and #1409, PR #1425)

- **Dice and coin**, always on: `!roll` (`1d6`, `NdS`, `dS`, `+M` or `-M`;
  up to 20 dice of 2 to 1,000 sides) lists each die and the total, and
  `!coin` flips one.
- **Quotes** (**Baud quotes**, off by default): `!quote add <text>` (up to
  160 characters), `!quote`, `!quote <n>` and `!quote del <n>`, per channel.
  Guests can't add. The adder or a channel moderator deletes, and a
  moderator deleting someone else's quote is said in the channel. 30 per bot
  and 10 per adder.
- **Quick polls** (**Baud quick polls**, off by default): `!poll [<1m-24h>]
  <question> | <a> | <b> ...`, with 2 to 6 answers and 10 minutes unless the
  opener says otherwise. One open poll per channel. `!vote <n>` is one vote
  per caller, which they can change, acknowledged privately. Results are said
  once at close, and `!poll close` closes early for the opener or a
  moderator. At most 100 voters. Guests vote but can't open polls. Polls
  survive restarts. In a Linked channel the opening line tells callers on
  other BBSes to vote with `Baud.bot@<node>: vote <n>`.

Baud's `help` lists only the abilities that are on. A full memory is
answered with the new **Memory-full message** phrase, "Baud's memory is
full; ask the SysOp."

## Door game events and `!top` (issues #1378 and #1408, PR #1426)

A door can report game results for Baud to announce in chat.

- **For the SysOp:** a door's <kbd>O</kbd> **Outbound** screen gains
  <kbd>E</kbd> **Events on** / **Events off** and <kbd>G</kbd> **Game events
  per hour** (12 by default, 1 to 240). In the bot editor, a new **Door
  games** section holds **Door-event sources**, **Baud door announcements**
  and **Grant door standings query**, all off. Only doors every caller may
  play (minimum play level 0) can be sources. Switching a door's outbound
  hook off releases its stored events and standings.
- **For callers:** `!top <door>` shows the top ten the door last reported, as
  a titled block such as `Voidrunner · Hall of Fame`. **Profile ▸ Game
  results announced in chat** is on by default. Off, the caller is never
  named in an announcement and is left out of `!top`. Guests and disabled or
  pending accounts are never named.
- **Door API 5:** a door with events on finds `outbound.events` in
  `door_info.json`, and may drop two new requests through the existing
  transport: an `event` of one of five types (`rank`, `achievement`,
  `medal`, `capture`, `perfect`), with a title of up to 80 characters, and a
  `standings` snapshot of up to 10 rows that replaces the last one. NetBBS
  words each announcement itself. The Developer Handbook has a new "Game
  events and standings" section, and the door guide "Letting it report game
  events".
- **For bot authors:** a third source kind, `door`; the `hello` carries
  `door_sources` and `announce_doors`; events arrive as `{"type": "door",
  ...}`; and a node query `top` answers a door's standings with opted-out
  callers left out.

The bundled games don't report events yet (see "Verification boundaries").

## Onboarding offers Baud (issue #1383, PR #1398)

First-run onboarding gains a third question: switch on Baud, with help and
greetings, in a channel you choose. It defaults to no. Yes creates Baud,
enabled in that channel, with greetings there and the friendly temperament,
and nothing else: no reading, queries, Link or sources. Either answer points
you to **Content ▸ Open chat bots** for changes.

It is asked once, and only while the node has no bot and has a local,
visible, non-MRC channel whose bot policy allows bots. **A node upgraded in
place with no bots is asked once**, at the next SysOp login.

## Paging the SysOp (issues #1377 and #1407, PR #1413)

`/sysop <reason>` in any chat channel, MRC rooms included, pages every SysOp
and every account with a staff permission. The reason is shown to staff
only; it never goes to the channel, its scrollback, Link or MRC. The caller
gets a private `Paged the SysOp` block saying who was told and how, or that
nobody is online and the page waits.

- **Pages queue:** <kbd>P</kbd> **Pages** on the SysOp console's landing
  screen and on the Staff console. A page offers <kbd>C</kbd> **Chat** (the
  Monitor's break-in, shown while the caller is connected), <kbd>M</kbd>
  **Mail reply** (members only, reason quoted) and <kbd>D</kbd> **Done**.
  Whoever handles a page closes it for everyone.
- **Notices:** at once in chat or the Monitor, otherwise above your next
  screen and as a main-menu line, `1 page waiting: SysOp → Pages.`; in a door
  only once you leave it. The ATTENTION panel and the Staff console count
  open pages.
- **Fixed limits:** one open page per caller, at most one every 10 minutes
  (paging again replaces the reason), a reason of up to 160 characters. A
  guest can page only while someone who would receive it is online, and at
  most 20 guest pages are open at once. Handled pages are kept 30 days, at
  most 500. Staff can't page their own node.
- Baud's `help` mentions `/sysop <reason>`, and asking Baud to page the SysOp
  points to it instead.

## The oneliner wall (issues #1391 and #1412, PR #1414)

Callers can leave a short line for the callers after them.

- **At logon** the wall is the lower section of the Recent callers panel,
  under `O N E L I N E R S`, with one pause: <kbd>A</kbd> **Add a line** or
  <kbd>Enter</kbd> to continue. An empty wall is not shown.
- **On the main menu,** <kbd>R</kbd> **Recent callers** shows both, with
  <kbd>M</kbd> **Mail a caller**, <kbd>A</kbd> **Add a line**,
  <kbd>O</kbd> **Oneliners** and <kbd>B</kbd> **Back**. **Oneliners** is the
  full wall, newest first, with paging, find and <kbd>D</kbd> **Delete** for
  your own lines.
- **Limits:** a line is plain text of up to 60 columns. One line per call and
  three per account a day; every guest shares the guest account's three.
  The newest 200 lines are kept.
- **For the SysOp:** **Settings ▸ Wall of oneliners** cycles neon, plain and
  hidden. **Oneliner level** is under **Settings ▸ Limits & retention**. A
  SysOp, and staff who moderate everything, can delete any line; that is
  audited with the line's text.

The `[R]ecent callers` screen now always has a key bar and is left with
<kbd>B</kbd> **Back**; it used to close on any key when mail was closed.

## Scheduled announcements in chat (issues #1380 and #1410, PR #1415)

**Content ▸ Chat channels**, a channel, <kbd>N</kbd> **Notices** lists that
channel's scheduled announcements, with <kbd>C</kbd> **Create** and
<kbd>D</kbd> **Delete selected**. An announcement has a **Text** of up to 160
characters, a **Repeat** of once, daily, weekly on chosen weekdays, or
monthly on the first to fourth of a weekday, and a **Time** in the node's
clock. A `Next:` line shows when it is next said. Nothing is stored before
<kbd>S</kbd> **Save**.

- The node says it itself, muted as `Announcement: <text>`, even into an
  empty channel. It doesn't count as unread.
- At most 8 per channel, and their times of day at least an hour apart. A
  once announcement must be in the future and at most a year ahead.
- A slot the node misses is skipped, not said late.

**In a Linked channel** announcements stay on this node until you turn on
**Other nodes (whole channel)** in one of them; Save asks you to confirm.
From then on each is sent as a signed `channel_message` by the SysOp who
saved it, marked `"announcement": true`. Nodes advertising
`chat_announcements_v1` show `Announcement from <node>: <text>`; older nodes
show it as an ordinary line from that SysOp. If that account is deleted or is
no longer a SysOp, or Link is not running, it is said on this node only.

A scrollback snapshot sent to other nodes no longer includes the midnight
date line.

## Door outbound: a gated door reaches nobody (issue #1401, PR #1416)

A door whose minimum play level was above 0 could be allowed a board or
channel that every caller reads, and so announce itself, and its players, to
callers who may not see the door. Now a door's outbound hook carries
anything only while every caller may play it.

- <kbd>A</kbd> **Allow a board** and <kbd>L</kbd> **Let it chat in a
  channel** refuse while the door is gated: "Set its minimum play level to 0
  first."
- Every post and chat line is checked again when it is sent, against the
  current level.
- **Already-allowed targets stay listed but get nothing** while the door is
  gated, and the outbound screen says why. Lowering the level to 0 brings
  them back. A door tested at a high level gets its boards and channels once
  its level is lowered.

## Design document split by area (issue #1394, PR #1397)

For developers: `docs/NetBBS-design-doc.md` is now the entry point with a
section map. Each top-level section is its own file under `docs/design/`,
and each §16 decision is its own file under `docs/design/decisions/`.
Section numbers and headings are unchanged, so every `§n.n` reference and
existing link still works. The decisions for this round are recorded there
(PR #1400).

## For bot authors

All additive, at bot API version 1. The bot API guide describes each.

- **Actions:** `reply` takes `"private": true` and a `block`; `say.for`,
  `held.list` and `held.cancel` hold a line for one caller; `tell`,
  `tell.list` and `tell.cancel`; `model` and `model.reset` take `private`.
- **`hello`:** `phrases`, `timezone`, `blocks`, `linked_channels`, `node`,
  `quotes` and `polls`; with door API 5, `door_sources` and `announce_doors`.
- **Events:** `message` carries `private`, `asked_privately`,
  `caller.moderator` and `caller.guest`; a new `held` event reports
  `delivered` or `expired`; and a `door` event.

## Fixes

- **Stopping a bot could hang on a busy machine (PR #1427).** When a stopped
  bot's exit reached NetBBS late, the wait for it timed out, and that timeout
  turned the stop into a crash: the bot was started again, and the stop never
  finished. Try's `/quit`, a bot's Kill and node shutdown could all hang this
  way. The wait is now bounded and logs a warning instead, and a failed
  cleanup during a stop ends the stop rather than restarting the bot.
- **Stopping a door could hang a caller's session or shutdown (issue #1428,
  PR #1430).** After a door was killed, NetBBS waited for its exit, and for
  its output to close, with no limit. A door whose exit was reported late,
  or one that had started a program of its own that kept its output open,
  held the caller (or node shutdown) there indefinitely. The wait is now
  bounded: a stop ends within the stop grace plus about four seconds, and
  the node log says when it gave up waiting. DOSBox-X and VM doors stop the
  same way.

## Upgrade and rollback

- **Upgrade:** take a backup, install the wheel and restart. The database
  migrates from schema 139 to 148 . The new
  tables start empty, and the new switches are off (see "For SysOps"). Three
  things change on their own:
  - a node with no bots is asked once, at the next SysOp login, whether to
    switch on Baud;
  - the oneliner wall is on, and shows once a caller adds a line;
  - a door with a minimum play level above 0 stops posting to boards and
    chat channels it was allowed.
- **Rollback to v7.30.0 — MANUAL:** v7.30.0 refuses a database newer than
  schema 139 ("database schema version 148 is newer than this NetBBS build
  supports (139)"). Stop NetBBS, install the v7.30.0 wheel, then restore the
  backup taken before the upgrade with `python -m netbbs.backup restore`. In
  that order: a restore run by v7.31.0 would migrate the backup straight back
  to 148. Anything since the upgrade is lost with it: pages, oneliners,
  scheduled announcements, held reminders and notes, phrase overrides, quotes
  and polls, door events, and every other post, message and setting.
- **Help-desk model settings:** a wheel from before this release reads model
  settings strictly and would fail on the `mode` key that saving **Model
  settings** in this release writes. The restore above takes care of it: the
  pre-upgrade backup has no `mode`. This release reads settings tolerantly,
  so later rollbacks to it are not affected.
- **What peers keep does not roll back with you.** Other nodes keep the
  announcements this node signed and the bot blocks in signed channel
  messages, and go on showing them.
- **Mixed versions:** a node on v7.30.0 or earlier is never sent private bot
  requests or answers, block fields or the announcement marker. Its callers
  get public bot answers and fallback lines, and see this node's
  announcements as ordinary lines from its SysOp.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,735 passed, 136 skipped, 1 failed (26:23); the timing-sensitive test passed too. The one failure, `test_watcher_does_not_disconnect_a_still_active_session`, is in code this release does not touch. It passes on its own but can stall for a long time, and is tracked as #1432. Every other test that failed during this round's merges failed only under load and passed on its own; the two that turned out to be real (#1428, #1429) are fixed above.
- **The bundled games don't emit door events yet.** Their repositories need
  changes before they report anything :
  - Voidrunner: `rank` after a committed promotion, `achievement` for
    accolades of tier 2 and above (never catch-up unlocks on a career's first
    open), and standings from the Hall of Fame;
  - War Dialer: `rank` for rank tiers, `medal` at a season's close, `capture`
    for exchange captures, and standings for the season;
  - Retro Trivia: `perfect` for a perfect round.

  Door events are tested end to end with a fixture door and a real Baud
  process.
- Private answers, blocks, held lines, tells, polls and announcements over
  Link are tested between real nodes in the test suite, on one machine. They
  have not been exercised between live nodes on different hosts.
- The bounded door and bot stops (#1427, #1430) are proven on Windows by
  tests that hold back the exit notification. The door tests also cover the
  POSIX process-group path, but no POSIX machine has run them.
- Help-desk mode is tested against a fake provider endpoint. Its fixed
  evaluation prompts, `scripts/helpdesk_eval.py`, are meant to be run against
  a real local model as a release step; the tests never call it.
