# Issue #1302 — audible chat bells — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller in another window should notice when chat needs them. Before this
(built in #1333), only one thing rang: an MRC private line, which the receive
loop in `chat_flow` prefixed with `\x07` (#305). A native `/msg` or `/private`
(`_deliver_private_message`, delivered as a `_TimestampedNotice` through
`ChatHub.send_to`), a Link direct message (`link_direct`), a `/dm` invitation
and a mention of one's name were silent. `_TimestampedNotice` cannot mark a bell:
`daybreak` broadcasts one to everyone each day. Remote text already arrives
without BEL (`sanitize_text` strips every control character), and the PgUp
scrollback pager strips it from replayed lines (`_REPLAY_CONTROLS`). The web
client (`netbbs-terminal.js`, xterm.js 6.0.0) had no bell handling, so a web
caller heard nothing.

**Decision 1 — three levels, private by default.** A per-caller preference:
`off`, `private`, or `private and mentions`.
- **Private** rings for a native `/msg` or `/private`, a Link direct message,
  an MRC private line and a `/dm` invitation.
- **Mentions** also rings for a line that names the caller: their username
  or their current alias, as a whole word, in any case, with or without `@`.
  The caller's own lines and bot lines never ring.
- **Where it is set:** Profile → Communication, and `/bell
  [off|private|mentions]` in chat, beside `/timestamps`. A bare `/bell`
  shows the current level.
- **MRC privates:** they now follow the level too. A caller at `off` no
  longer hears them.

**Decision 2 — `/away` does not silence it.** A caller who is away is the
one a bell is for.

**Decision 3 — the web client plays a tone.**
- **The tone:** `term.onBell` plays a short WebAudio tone. The browser's
  autoplay rule is met by creating the `AudioContext` on the caller's first
  key or click. Until then, no sound.
- **The title:** while the tab is hidden, a bell prefixes the title with
  `(!)` until the tab has focus again.
- **No setting:** the web client has no setting of its own. The caller's
  level decides what reaches it, and a door's own BEL sounds on the web too.

**Decision 4 — BEL only.** No visual bell (a DECSCNM flash). Telnet and SSH
pass BEL to the caller's terminal, which decides whether it beeps, flashes
or stays silent.

**How the bell is sent.**
- **Rate:** each session has a bucket of 3 bells, refilled by 1 every 10
  seconds. One sender rings a session at most once per 30 seconds. The table
  that tracks senders is bounded.
- **A separate write:** the bell is `\x07` written on its own, after the
  line. It is never part of the line, so it is never in a transcript or a
  replay. It never rings for the caller's own lines, and never while the
  scrollback pager is open.
- **An explicit flag:** the decision to ring comes from an explicit ring
  flag on what the hub delivers, never from the notice's class.
- **Remote text never rings:** inbound text never carries its own BEL. A
  regression test keeps `sanitize_text` stripping it.
- **Scope:** v1 rings on the chat screens and in direct chat. A private
  message queued for a session outside chat does not ring.
