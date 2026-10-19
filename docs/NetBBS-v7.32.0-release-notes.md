# NetBBS v7.32.0

The bundled games now report what callers earn. With game events switched on
for a door, Baud announces ranks, achievements, season medals, captured
exchanges and perfect trivia rounds in chat, and `!top <door>` shows the
game's own top ten. v7.31.0 built this on the NetBBS side; this release brings
the games that use it. Baud's help also gets the same colours as chat
`/help`.

**This release does not migrate:** the node database stays at schema 148.
Rolling back to v7.31.0 is the old wheel and a restart.

**Versions:** the Link real-time protocol stays at 4, the door API at 5 and the
bot API at 1. Bot blocks gain one optional field (see below).

It is a minor release: the bundled doors gain behaviour, and bots gain a block
option. Nothing that worked before changes.

## The bundled games report game events (#1408, PRs #1435 and #1436)

| Door | Release | What it reports |
| --- | --- | --- |
| Voidrunner | v1.4.0 | rank promotions, silver and gold accolades; `!top voidrunner` is the Hall of Fame by best credits |
| War Dialer | v1.3.1 | rank tiers, season medals, exchange captures; `!top wardialer` is the season ranking |
| Retro Trivia | v1.2.0 | perfect rounds (no `!top`: the game keeps no scores) |

- **Nothing changes until you switch it on.** On a door's <kbd>O</kbd>
  **Outbound** screen, <kbd>E</kbd> **Events on**. Then, in the bot editor
  (**Content ▸ Open chat bots**), add the door under **Door-event sources**,
  choose an **Announcement channel** and switch on **Baud door
  announcements**. For `!top`, switch on **Grant door standings query**. Only
  doors every caller may play (minimum play level 0) can be sources.
- **Only live results are announced.** A Voidrunner career that unlocks old
  accolades the first time it opens in v1.4.0 sends none of them, and a save
  that fails reports nothing. Bronze accolades are not announced.
- **Standings appear from the first session.** Each game sends its table
  when reporting starts, then again whenever its order changes. War Dialer
  v1.3.0 only sent it once the top ten changed; v1.3.1 fixes that (found in
  review, Thiesi/WarDialer#19).
- **Callers choose:** **Profile ▸ Game results announced in chat** (on by
  default). Off, a caller is never named and is left out of `!top`.
- **Elsewhere the games are unchanged.** On other BBS software, standalone,
  or with events off, they write no files.
- War Dialer v1.3.0 also returns to the Scene hub after each of the Scene's
  screens.

## Baud's help in the colours of chat `/help` (PR #1434)

Chat `/help` colours a command in the accent colour and bold, its parameters in
the label colour, and `< > [ ] |` muted. Baud's `help` block now looks the same,
with the `, ` between commands that share a row muted too.

**For bot authors:** a titled block may carry `"labels": "commands"`, and the
host then draws each label as command syntax. Any other value is refused, and
leaving it out draws plain labels as before. botkit's `reply_block(...,
labels="commands")` sets it. A v7.31.0 node shows such a block's one-line
fallback instead, and a v7.31.0 host refuses the field from a bot.

## Upgrade and rollback

- **Upgrade:** install the wheel and restart. Nothing migrates.
- **Rollback to v7.31.0:** install the v7.31.0 wheel and restart. The bundled
  games go back to their v7.31.0 versions, which report nothing. Events and
  standings they already reported stay stored, and `!top` keeps showing the
  last table.
- **Mixed versions:** a v7.31.0 node shows Baud's help from a v7.32.0 node in
  a linked channel as its one-line fallback.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,740 passed, 136 skipped, none failed (16:05); the timing-sensitive test passed too.
- The games' event reporting is tested in each game's own repository, and in
  NetBBS end to end with a fixture door and a real Baud process. The vendored
  games' reports have not yet been watched on a live node.
