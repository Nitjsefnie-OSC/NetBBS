# NetBBS v7.25.0

This release brings the bundled games' newest versions. Voidrunner's Viewport
now shows full-screen pictures of the caller's ship, station and discoveries,
and its screens get a colour pass that makes the text-heavy ones easier to
read. All three games hold the end of a scene longer, so its result can be
read before the next screen.

| Game | Was | Now |
|---|---|---|
| Voidrunner | v1.2.1 | [v1.3.0](https://github.com/Thiesi/Voidrunner/releases/tag/v1.3.0) (with [v1.2.2](https://github.com/Thiesi/Voidrunner/releases/tag/v1.2.2)) |
| War Dialer | v1.2.0 | [v1.2.1](https://github.com/Thiesi/WarDialer/releases/tag/v1.2.1) |
| Retro Trivia | v1.1.1 | [v1.1.2](https://github.com/Thiesi/RetroTrivia/releases/tag/v1.1.2) |

**Nothing migrates:** the node database stays at schema 130. The Link,
real-time, door API and bot API versions are unchanged, and so are the
Voidrunner career format (schema 2, achievements version 1) and the War Dialer
world (schema 11). Upgrading is a wheel swap and a restart, and so is rolling
back to v7.24.0.

It is a minor release because callers get new screens.

## Voidrunner v1.3.0 (PR #1295)

**Viewport pictures.** The Viewport (`V` on the Command Deck) is drawn in the
same half-block pixels as the in-flight scenes:

- **[1] Ship:** your hull, lit along its plating, with its drive burning and the
  world you are docked at behind it. Damage shows as scorch marks, then sparks,
  then smoke and a cockpit alarm.
- **[2] Station:** your station, with its ring turning and traffic on its
  approach.
- **[3] Discovery:** each of the four landmarks drawn as itself, or a scanner
  sweeping empty space before you have found one.

A picture opens its blast shutters and idles until a key, for about 21 seconds
at most. That key is the next command, so nothing is swallowed. The `fast`
preset and `DOOR_SCENES=0` get a still picture. `mono`, `plain` and terminals
too small for a picture keep the text portraits.

**Easier reading.** Running text has its own cool grey instead of the brown
label colour, which helps the Pilot Guide, the Viewport and contract terms
most. In `Label: value` rows the two parts are coloured apart. On the Command
Deck the hull condition takes its severity colour: Intact green, Damaged
amber, Critical red. Achievements show their numbers in bold, credit amounts
in gold, and progress as a gradient toward the medal's colour.

**Fixes.** The Pilot Record's action bar fits on one row at 80 columns again.
Narrower terminals use a short form with `[A] Awards` (v1.2.2). Scenes hold
their last frame for 2 seconds instead of 0.8, and any key still skips them.

## War Dialer v1.2.1 and Retro Trivia v1.1.2 (PR #1295)

War Dialer holds each scene's caption for about three seconds, and its corner
hint reads `key continues` while it does. Retro Trivia
holds the finale's last picture before the results screen.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.24.0 is the reverse: no save or
world written by these versions is refused by the previous ones.

## Verification boundaries

- **The games' own suites** ran in their repositories before each release.
- **NetBBS side:** the vendored files match their releases' `SHA256SUMS` and
  tagged commits. The presentation gallery gains walks for the Viewport's
  station and discovery pictures. The three pictures, the recoloured
  Achievements, Command Deck and Journey Resumed screens, and War Dialer's held caption were drawn at 80×24 and
  checked by eye.
- **Full Windows suite:** 11,921 passed and 136 skipped on the merged tree. Two tests
  failed under the parallel run's load, a ZMODEM upload and a bot announcement,
  neither touched here. Each passed three times out of three when run alone. The
  timing-sensitive test passed.
- **Not yet checked live:** the Viewport's idle animation over real Telnet and
  SSH sessions and in SyncTERM. The gallery shows only its still.
