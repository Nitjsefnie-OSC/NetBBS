# NetBBS v7.28.0

This release makes NetBBS look right on classic BBS terminals that show only
the 16 ANSI colours. The browser terminal behind the **Connect** button on BBS
directory sites is one of them: until now it showed a NetBBS sign-in as
blinking text under a garbled banner. Such terminals now get NetBBS in 16 real
colours. It also fixes a stale page footer left behind in chat scrollback.

**Nothing migrates:** the node database stays at schema 134. The Link,
real-time, door API and bot API versions are unchanged. Doors can now be told
one more value for `color_depth`, `16`. Upgrading is a wheel swap and a
restart, and so is rolling back to v7.27.0. On rollback, a caller who chose
16 colours gets 256 colours, and their Profile setting cycles back to auto.

It is a minor release because callers get a new colour depth, picked
automatically for some terminals, and doors can be told a new value.

## 16 colours for classic BBS terminals (issue #1348, PR #1349)

NetBBS draws in 256 colours, and in truecolor where the terminal shows it. A
terminal that knows only the 16 ANSI colours does not skip a 256-colour code.
It reads `ESC[38;5;Nm` one parameter at a time: the `5` turns on blink, and
the palette number lands on whatever attribute has that number. 7 reverses
the text, 8 hides it, and 30-47 pick an unrelated colour. The fTelnet web
terminal that BBS directories embed for their Connect button is a 2020 build
of exactly this kind. It showed Reliable Link's sign-in prompt blinking under
a banner of inverted and hidden blocks.

**Who gets 16 colours:**
- A terminal that reports a classic BBS type: `ansi-bbs`, `ansi`, `pcansi`,
  `cterm` or `cp437`.
- A Telnet caller whose terminal reports no type, or one NetBBS does not
  recognise.

**Who keeps what they had:** SyncTERM (`syncterm`), modern terminals (xterm,
PuTTY and the rest NetBBS already recognises), any Telnet client that sends
`COLORTERM`, SSH clients with a modern terminal type, and the browser
terminal.

**How it looks:** every colour becomes the nearest of the 16 as classic
terminals draw them (the VGA palette), measured by what the eye sees. Dark art
stays dark, and a tinted interface colour, such as a muted blue label, never
turns grey. Bright foregrounds go out as bold, the form every classic
terminal reads, and the brightness never spills into the next piece of text.
Banners drawn in truecolor keep their shapes and lose their fine shading.

**For callers:** **Profile ▸ Color depth** now offers 16 alongside auto, 256
and truecolor, and the choice takes effect at once. A caller on Windows'
built-in telnet program, which calls itself `ansi`, now gets 16 colours under
auto. Setting 256 brings the 256 colours back. The User Handbook's "Your
terminal" section lists the settings.

**For SysOps:** nothing to set up. The node log's line about each Telnet or
SSH caller's terminal type now ends with "16 colours" when they get them.
Your banners and presets need no 16-colour version: each is shown in the
nearest of the 16. A Snoop shows the caller's screen in full colour.

**For door authors:** the door info file's `color_depth` can now be `16`. War
Dialer and Retro Trivia already draw in 16 colours when told. A door that
ignores the value and draws in 256 colours, Voidrunner among them, is still
shown in the nearest of the 16, because NetBBS rewrites door output too. The
Developer Handbook has the details.

## Fix: a turned page's footer no longer stays in chat scrollback (PR #1347)

A long answer in chat, such as `/mrc bbses`, is shown a page at a time, with
a footer like `page 2 of 11 · PgDn more · PgUp back · Esc done`. That footer
was kept in the chat's scrollback like any other line. Scrolling back with
PgUp, or redrawing after a resize, showed a stale footer for every page
already turned, the last one right above the scrolled view's own `PgDn newer
· Esc live` line. Now only the footer of the page you are on is kept, and
none once the rest of the answer is skipped or you type a line.

## Upgrade and rollback

- **Upgrade:** install the wheel and restart. No migration, no setting to
  change.
- **Rollback to v7.27.0:** the previous wheel and a restart. A stored 16
  colour-depth choice is read as 256 colours there.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,277
  passed, 136 skipped, 1 failed. The failure was
  `test_bot_scopes.py::test_real_chat_heading_who_actions_join_leave_and_no_replay`,
  which timed out waiting for chat to close its bot subprocess. It uses a
  test session that never reaches the colour code. Run alone, it passed
  every time: 9 runs on this release, one of them taking 29.5 seconds
  against its 30-second limit, and 11 runs on the tree before the colour
  change, one of them taking 7 seconds. Most runs take under a second, so
  this is an occasional stall in shutting down the bot process, not
  something this release introduced. The timing-sensitive test passed.
- The 16-colour output has been checked in tests and in renders of the
  Reliable Link sign-in screen. It has **not** been checked yet against the
  live fTelnet Connect button on a directory site. That needs this release
  deployed on Reliable Link.
- How each banner looks in 16 colours depends on its art. The CIELAB
  matching was tuned on Reliable Link's pixel-art banner. Truecolor art with
  many mid-tones (purples, pastels) shows as flat blocks of the nearest
  colour.
