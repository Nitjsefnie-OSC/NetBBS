# Issue #1374 — a bot developer kit — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

#1208 Decision 1 chose a house bot built on an open bot API so that bots other
than Baud could exist. The openness was real but hard to use. The only example
was Baud, whose 172 lines mix protocol plumbing (a sequence for action ids,
one `waiting` slot for a result, a backlog of 32 events) with bot logic.
Correlating results by hand is the hardest part of the protocol, and there
was no way to see what a bot does without enabling it in a real channel.
Stderr is drained and discarded, so an author never saw their own traceback.

Constraints: the host matches `BOT_API_VERSION` exactly (a bot answers
`{"type":"ready","version":1}` or fails the handshake). Baud is started by
filename (`sys.executable -u <path>/baud.py`) with no `PYTHONPATH`, so
`netbbs` may not be importable from it. `examples/` and `scripts/` do not ship
in the wheel. The door precedent for an on-ramp is an installed, readable
stdlib example (`python -m netbbs.doors.bundled.retro_trivia`) and the door
editor's **Test as SysOp**, which tests the draft.

**The helper.**
- **One stdlib-only file inside the package, meant to be copied too:**
  `src/netbbs/bots/botkit.py` (working name). It ships in the wheel, so every
  SysOp has it. It imports nothing from `netbbs`, so an author can copy it
  next to a bot run by any interpreter. Baud imports it as a sibling module,
  which works when Baud is run by filename.
- **Versioning:** the helper speaks exactly one `BOT_API_VERSION` and has no
  version of its own. It refuses any other `hello` version with a clear
  stderr line and answers `ready` only on a match. A test pins the helper's
  version to the package's. Additive actions (#1372's private answer,
  #1373's blocks) need no bump: the helper gains methods.
- **What it owns:** the handshake; action ids and result correlation, with
  several results outstanding; dispatch by event type, ignoring unknown
  types (ready for #1378's door events); a state cache with ordered writes;
  `reply` with the caller's name in front and capped at 500 characters, as
  Baud does; and flushing. Capabilities are read from the hello's grant
  lists, not from `scope`, which the host always sends as `addressed`.

**Decision 1 — a handler waits for a result through a callback.**
`msg.query("stats", then=lambda r: msg.reply(r.text))`. It is plain
synchronous code, about 120 lines to copy, and maps one-to-one onto Baud's
current flow. Chains of two waits nest; that is the cost.
- Rejected: **generator handlers** (`r = yield msg.query(...)`): `yield` as
  "wait" is unfamiliar to the non-developers the kit is for. Rejected:
  **asyncio:** reading stdin asynchronously across platforms with the
  standard library alone needs a reader thread or platform branches, and is
  more machinery to copy.

**Decision 2 — Baud is rewritten on the helper, before the other Baud
changes.** Every feature Baud uses (state writes, queries, ticks,
announcements, the model) is then exercised by a shipped bot and its real-pipe
test, which keeps the helper honest. Baud loses its own sequence, waiting
slot and backlog. It fits #1208 Decision 4: the house bot has no privilege
others lack. The rewrite lands first so that the Baud parts of #1372, #1373,
#1375, #1376, #1377, #1378, #1379 and #1382 are not each written twice. Its in-process tests
and the reminder backoff (8 refusals, doubling from a minute to an hour) are
carried over exactly.
- Rejected: **Baud stays raw:** two styles in the tree, and the Baud issues
  keep building on hand-written correlation.

**Decision 3 — the second example is Tally, karma from whole-channel
reading.**
- It shows what Baud does not: observations, which carry no token, so the
  bot must `say` rather than `reply`; its own state; and the reading grant
  and its disclosure. Grants: addressed plus **Read whole channels**. The
  editor's warning and the `/who` disclosure already cover that grant.
  ```
  [20:14] <alice> thanks bob++ for the file fix
  [20:14] [BOT] <Tally.bot> bob now has 4 karma.
  [20:15] <carol> !karma bob
  [20:15] [BOT] <Tally.bot> carol: bob has 4 karma (top: bob 4, alice 2).
  ```
- **Installed and off.** It runs as `python -m netbbs.bots.tally` and is
  never enabled or preset: #1208 says bots are off until the SysOp switches
  one on. The SysOp enters it under Program arguments with the node's
  interpreter, as for any external bot.
- Rejected: **a quiz bot:** it overlaps the Retro Trivia door. Rejected:
  **an echo bot:** it shows only the addressed pattern Baud already shows.
  Dice and coin belong to Baud (#1379).

**Decision 4 — the harness is in the console only: Try in the bot editor and
`[T]ry selected` on the list.**
- **Where:** the bot editor gets a **Try** field that tries the draft, with
  the draft's own grants and program, as the door editor's **Test as SysOp**
  does; the Chat bots list gets `[T]ry selected` (T is free there), which
  tries the saved instance. There is no command-line front end.
- **The engine:** the real `BotRuntime` against a scratch database, scratch
  state and a scratch working directory. All host checks then apply as they
  will live, and a trial never touches the node's database, its `.bots`
  directory, rate history or chat. Live callers never see a trial.
- **The screen:** a titled `Try <Name>.bot · scratch #<channel>` screen. The
  SysOp types lines as a caller; each line is addressed exactly as in a
  channel (`!help`, `Name.bot: help`, plain chat becoming an observation when
  reading is granted), and `/join`, `/leave`, `/post` and `/file` inject the
  granted events. Real ticks run. The screen shows each host event and bot
  action with its result, the chat lines as callers would see them (blocks
  drawn with `chat_reply`), and a bounded stderr excerpt (8 KiB, like a
  door's last diagnostic). Live, stderr stays discarded.
- **Model actions are rehearsed,** never sent to the provider: the result
  says what would have happened, as door outbound rehearsals do. A trial
  spends no tokens and skips no disclosure rule.
- **No confirmation before a trial.** A trial changes no node data, so there
  is nothing to consent to (§3.5).
- Rejected: **command line and console on one engine:** a second front end
  to maintain. Rejected: **command line only:** invisible to SysOps who never
  leave the console.

**Docs.** A "Writing a bot" section in `docs/NetBBS-bot-api.md`, and a bot
row in the Developer Handbook's integration table with a short "Developing a
chat bot" section pointing at the helper, Tally and Try.

**Implementation notes.** No migration: the scratch database runs the normal
migrations. The only runtime change is an opt-in trace hook on the worker's
reads and writes and a rehearsal branch for `model` and `model.reset`, both
off live; a test proves live mode keeps nothing. Because the harness is
console-only, an author iterates through the console; a later command-line
front end on the same engine would be a new decision.
