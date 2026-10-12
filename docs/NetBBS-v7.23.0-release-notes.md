# NetBBS v7.23.0

This release lets callers scroll back through chat with PgUp and PgDn, pages
long command replies, and adds colour to the chat replies that v7.22.0 laid
out as titled blocks.

**Nothing migrates:** the node database stays at schema 130. The Link,
real-time, door API, bot API, career and world versions are unchanged.
Upgrading is a wheel swap and a restart.

It is a minor release, not a patch, because chat gains new keys and
behaviour.

## Scroll back through chat (PR #1288)

A terminal's own scrollback is no help in chat: the pinned status and input
rows are redrawn every few seconds, and each redraw jumps the terminal back to
the bottom. Reading something that has scrolled away meant holding the
scrollbar.

Now **PgUp** scrolls back through the conversation a page at a time, over
the last 500 lines, and **PgDn** scrolls forward again. While you are scrolled
back, the chat area holds still, so you can read or copy at leisure:

```
[21:22] [MRC] line 15 of the evening's chatter
…
[21:22] [MRC] line 22 of the evening's chatter
────────────────────────────── 8 lines back · 1 new · PgDn newer · Esc live ──
```

- Lines that arrive meanwhile wait, counted on the bottom row, instead of
  pushing the view along.
- **Esc**, **PgDn** past the newest line, or sending a line returns to the
  live chat, drawn again with everything that arrived. A resize does too.
- The line you are typing is untouched: the keys work mid-sentence.
- This needs the pinned status and input rows, so a terminal shorter than
  four rows has no scrollback.

## Long replies come a page at a time (PR #1288)

The pager that v7.22.0 gave the MRC hub's answers now covers long chat
command replies too, such as `/list` on a board with many channels or `/who`
in a busy room:

```
── Channels ──────────────────────────────────────────────────────────── 21 ──
 #lobby    1 here
 …
──────────────────────────── page 1 of 4 · PgDn more · PgUp back · Esc done ──
```

- **PgDn** shows the next page. **Enter** on an empty line still works, for a
  terminal that sends no PgDn.
- **PgUp** scrolls back over the pages already shown.
- **Esc** skips the rest.

Short replies are unchanged. A long command reply never takes the pager away
from an MRC answer that is still being read.

## More colour (PRs #1288 and #1289)

The reply blocks use the colours chat already gives each thing:

- **`/who`:** your own name in your colour, other callers in gold, "here" in
  green, an away message in amber. An MRC user reads `nick@site` with the site
  in the MRC colour, as in chat lines. A linked BBS is in the node colour, and
  the column now reads `on Vertigo` rather than `on linked node Vertigo`.
- **`/list`:** the MRC and linked-BBS counts in those same colours.
- **`/mrc info`:** the hub's `Label: value` rows are drawn like our own rows,
  with labels in blue and values in white. Platform and Version are split into
  three coloured parts with dim slashes. The hub's frame lines, and any row
  that doesn't match that shape, keep the hub's own look.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.22.0 is the reverse.

## Verification boundaries

- **Navigation keys:** tests cover both line editors (the shared Telnet, SSH
  and console editor, and the web client's) handing over PgUp, PgDn and Esc
  without touching the line being typed.
- **Scrollback and paging:** tests drive the real chat loop through
  - scrolling back, a line arriving while scrolled, Esc and PgDn back to live;
  - a typed line returning to live;
  - a long `/list` paged and skipped;
  - a resize while scrolled back.

  The live, scrolled and paged screens were also drawn through a terminal
  emulator and checked by eye. The review's four findings were fixed with
  regression tests: a crash on a tiny resize, a stale height, a hub answer's
  pages being overwritten, and one Esc doing two things.
- **Colour:** renderer tests cover coloured cells, the `nick@site` split and
  the `/mrc info` rows and their fallback.
- **Full Windows suite:** 11,923 passed and 136 skipped, with no failures, and
  the timing-sensitive test passed. That run is on the tree `main` has after
  both merges; #1289's own run was green too (11,910 passed).
- **Not yet checked live:** PgUp/PgDn on real Telnet, SSH and web clients,
  including SyncTERM's key codes; and `/mrc info` against the live hub beyond
  the sample answer.
