# NetBBS v7.24.0

This release brings the three bundled games' newest versions, and with them
animation in the middle of play. Voidrunner, War Dialer and Retro Trivia now
play short full-screen scenes at the moments that matter: a jump, a kill, a
rooted exchange, a bust, a perfect round. Voidrunner also gains 66
achievements that carry across careers.

| Game | Was | Now |
|---|---|---|
| Voidrunner | v1.1.0 | [v1.2.1](https://github.com/Thiesi/Voidrunner/releases/tag/v1.2.1) |
| War Dialer | v1.1.0 | [v1.2.0](https://github.com/Thiesi/WarDialer/releases/tag/v1.2.0) |
| Retro Trivia | v1.0.0 | [v1.1.1](https://github.com/Thiesi/RetroTrivia/releases/tag/v1.1.1) |

**Nothing in NetBBS migrates:** the node database stays at schema 130. The
Link, real-time, door API and bot API versions are unchanged, as are the War
Dialer world (schema 11) and the Voidrunner career format (schema 2).
Upgrading is a wheel swap and a restart. **Rolling back is not**: see
[Upgrade and rollback](#upgrade-and-rollback).

It is a minor release because callers get new features, and because
Voidrunner careers gain a saved field that older versions refuse.

Sixteen of the scenes, from all three games, play on
[www.netbbs.org/doors.html](https://www.netbbs.org/doors.html#scenes),
replayed from the games' own output.

## Voidrunner v1.2.1: scenes and achievements (PR #1292)

**Scenes.** A voyage's turning points play as short full-screen scenes. They
reuse the launch splash's machinery, and the caller's own hull class flies in
each one:

- **Voyages:** the jump, docking (each economy's station has its own look), a
  contact, a kill, the loss of your ship, an escape, and a customs sweep.
- **Encounters:** derelict salvage, distress rescues, trader data bursts,
  landmarks, and the stranded tow.
- **Milestones:** hull refits, promotions, faction emblems, and retirement.

The docking caption also reports pay contracts finished on arrival.

**Achievements.** 66 achievements in 12 groups, kept across retirement and
counted across every career. The game's Pilot Record screen, under `[A]`, lists
progress and how many of the node's pilots hold each one, read from the
existing `scores/` records. Hidden achievements stay secret until earned. An
existing career unlocks what it has already done the first time it opens.

v1.2.1 also strips C1 control characters from names in classic drop files.
NetBBS passes `NETBBS_DOOR_INFO` rather than a drop file, so that fix changes
nothing here.

## War Dialer v1.2.0: nine scenes (PR #1292)

| Scene | When it plays |
|---|---|
| Patch In | Rooting an exchange: won, held, or an unclaimed exchange |
| Raid | A raid scores or bounces |
| Contract hack | Every job; the contract sets the login banner and the payoff |
| Operation | The Execute step |
| Busted | A bust, in place of that turn's own scene |
| Tier Up | The turn's Rank crosses a tier |
| Fed Crackdown | An unseen crackdown at login, or a season change mid-session |
| Cracktro | That same login, if the caller won a medal |
| Messages Waiting | Unseen receipts from rivals at login |

Each scene ends on the result screen's headline. Trading, recruiting,
garrisons and crew purchases keep the old carrier sweep.

Known limitation, from War Dialer's own notes: a season change seen
mid-session can play its crackdown again at the next login, because that
receipt is still unread.

## Retro Trivia v1.1.1: a finale for every round (PR #1292)

A finished round now ends with one of three shows before the results screen.
A perfect round gets **Jackpot**: every bulb flashes and confetti falls. Half
or better gets **Applause**, and anything less gets **Lights Out**, an iris
closing on the score. A 1.5-second pause first keeps the last answer's verdict
readable.

v1.1.1 also honours "no graphics" in classic drop files with plain ASCII
output. As with Voidrunner's fix, NetBBS passes `NETBBS_DOOR_INFO`, so this
changes nothing here.

## The rules every scene follows

- A scene plays **only after the result it shows has been saved**, so a dropped
  connection never loses an outcome.
- It **never waits for a key**. Any key ends it and is used up, so it can't
  choose anything on the next screen.
- It plays only to a caller who is watching: a live terminal, no keys typed
  ahead, and motion on in the game's display settings. Voidrunner's and War
  Dialer's `fast`, `mono` and `plain` presets have no scenes.
- **MANUAL — SysOp switch:** to turn Voidrunner's and War Dialer's scenes off
  for every caller, add `"DOOR_SCENES":"0"` to that door's profile environment
  JSON. It is separate from `DOOR_SPLASH`. Retro Trivia's finale is part of its
  splash, so `DOOR_SPLASH=0` turns that off.

The [door setup guide](NetBBS-door-guide.md#bundled-games-at-a-glance) and the
User Handbook describe the scenes and achievements.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. The games are bundled, so a door registered from the
gallery runs the new versions straight away.

**MANUAL — rollback to v7.23.0 breaks played Voidrunner careers.** The new
Voidrunner writes an `accolades` record into a career as soon as it earns an
achievement, and an existing career unlocks what it has already done the first
time it opens. The v1.1.0 door in v7.23.0 refuses any career with that record:
*"The saved career contains unsupported fields."* To roll back, restore the
Voidrunner save directory (`<db>.doors/voidrunner/`, or `VOIDRUNNER_SAVE_DIR`)
from a backup taken before the upgrade, along with the previous wheel. Careers
played since the upgrade lose that play. War Dialer's world and Retro Trivia
need nothing.

## Verification boundaries

- **The games' own suites** ran in their repositories before each release:
  the scenes' bounds at every supported size, colour depth and CP437, their
  size on the wire, skipping, the switches, and when each scene plays.
- **NetBBS side:** the vendored files match their releases' `SHA256SUMS` and
  tagged commits (`tests/test_vendored_doors.py`). The suite and the
  presentation gallery switch scenes off with `DOOR_SCENES=0`, like the
  splash, so a scripted session never types into one.
- **Full Windows suite:** 11,921 passed and 136 skipped on the release tree. Two tests timed out
  under the parallel run's load: a door outbound receipt test and a bot
  announcement test, neither touched here. Each passed three times out of
  three when run alone. The timing-sensitive test passed.
- **Website capture:** the sixteen scenes on the website (seven Voidrunner, six War Dialer,
  three Retro Trivia) were played by the
  games' own `play_*` functions inside this tree. Only the terminal was
  replaced, and each cast ends on the scene's real caption.
- **Not yet checked live:** scenes over real Telnet and SSH sessions and in
  SyncTERM, and War Dialer's scenes on a Windows host (its splash plays only
  where it can poll the terminal, so not on Windows).
