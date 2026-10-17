# Issue #761 — the SysOp's live session monitor, snoop and break-in chat — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A SysOp had no live view of what callers were doing and no way to act on one
session beyond a message or a disconnect. The work is split across #762
(activity and idle), #763 (the Monitor), #764 (screen copy and snoop) and #765
(break-in chat). The decisions below are the maintainer's, recorded on the
tracker on 2026-09-27.

**Decision 1: an in-BBS screen, not a local command.** The monitor is
Operations → Node and sessions → Monitor, inside the SysOp console. All live
session state lives in the node process, and `python -m netbbs.admin` reaches
the node only through the database. Rejected: a local `netbbs top`. It would
need a new IPC channel and wire protocol, with no portable form (asyncio can't
serve Unix sockets on Windows), and it would add attack surface. A SysOp who
wants it locally connects to `localhost`.

**Decision 2: the Monitor shows places, never content.** A caller's activity
is a trail of place names, such as `Boards › Retro` or `Doors › Voidrunner`.
It never includes a message subject, a mail or direct-chat partner, a file
name or a search string. Idle time counts caller input only; transport
keepalive does not count.

**Decision 3: snoop is silent, and callers are told in advance.** A SysOp may
watch any live session, the caller's screen exactly as they see it, private
messages included, without the caller being told at the time. What a caller
may rely on:
- **Disclosure.** The last line of chat's `/help` says it: "The SysOp can
  watch any live session." The User Handbook's "What the SysOp can see" says
  it in full.
- **Record.** Every snoop is logged to the node log with the SysOp, the
  caller and the duration.
- **Passwords.** Masked input is never echoed, so it never reaches the screen
  copy that snoop shows.

Rejected:
- an on-screen "being watched" indicator, the classic BBS alternative, which
  the maintainer declined;
- no snoop at all. The per-session screen copy is needed for break-in either
  way.

**Decision 4: break-in chat puts the caller back exactly where they were.**
The SysOp takes over the caller's terminal for a two-pane chat. When it ends,
the caller's screen is repainted as their program left it, including anything
it printed meanwhile and a half-typed line, and they carry on. The mechanism
suspends the session's I/O and never its task (worklog, "Break-in suspends a
session's I/O, never its task").
- A break-in is refused during a binary transfer, and a transfer waits for a
  break-in to end.
- A break-in is refused while the caller is typing a password. A password
  prompt reached during a chat shows `*` there.
- A caller in a door keeps playing unattended during the chat, so the SysOp is
  warned first.
- Only the SysOp ends the chat. Every chat is logged.

Rejected: an "invite to direct chat" stand-in, which the maintainer judged not
enough; and cancelling the caller's task back to the main menu, which loses
their place.

**Decision 5: narrow terminals drop columns, never wrap.** Below 80 columns
the Monitor drops the address, terminal size and transport columns, in that
order. User, idle time and activity always stay. How narrow the console must
still work is decided in §3.4 (issue #662).
