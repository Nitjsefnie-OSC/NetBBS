# Issue #929 — SysOp art: SAUCE and live slots — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Steps 3 to 6 of #929. Normative description: §3.2, "SysOp art: storage and
SAUCE" and "Art with live slots", including its list screens, hand-drawn items
and pacing.

**Decision 1 — the `.ans` file is the only source.** SAUCE, pictographs and
iCE colours are handled when the file is read. Rejected: a normalised copy in
the database with its metadata, which would give two sources of truth while
SysOps edit art files over SFTP, and would need a migration.

**Decision 2 — the art path owns the control-range pictographs.** Only art
treats 0x01–0x1F and 0x7F as glyphs; everywhere else they stay controls, so
caller text cannot smuggle control bytes in by calling them art.

**Decision 3 — iCE colours for everyone.** Blink with a background colour
becomes a bright background on every session. Rejected: switching the
terminal into iCE mode with CTerm's own sequence, which only SyncTERM
understands and would still leave UTF-8 terminals blinking.

**Decision 4 — too-wide art falls back.** Rejected: wrapping or cutting it,
which turns a drawing into noise.

**Decision 5 — plain tokens in the art, not ENiGMA½-style codes plus a theme
file.** ENiGMA½ marks views with `%VM1`-style codes and sets their size and
style in a separate `theme.hjson`. `{menu WxH}` tokens keep position, size and
style in the one file the SysOp draws, and give the console something to
check. Rejected for the same reason: region definitions in SAUCE comments,
which editors strip and nobody sees.

**Decision 6 — items that do not fit fall back to the generated menu.**
Rejected: filling the region and moving the rest behind a "more" entry, which
silently moves items a caller can use out of sight.

**Decision 7 — lists in art show a compact row.** A list region shows the
number, the name and one column per screen; descriptions and tables stay on
the generated list. Rejected: drawing the generated list inside the region,
whose tables and descriptions do not fit a drawn box and whose page arithmetic
assumes the full screen; and hiding NetBBS's own list under the art, which
shows stale rows.

**Decision 8 — hand-drawn items are found, not declared.** A drawn `[K]` is
the item, with no extra token. Rejected: a sidecar map or SAUCE comments
holding coordinates, which editors strip and SysOps cannot see.

**Decision 9 — items a caller cannot use are blanked.** Chosen over dimming
them after a mockup: blanking matches the generated menu, which hides them,
and does not advertise SysOp keys. Dimming keeps the art's full shape but
shows keys that do nothing, and relies on a colour some terminals render
faintly.

**Decision 10 — pacing on the server, not CTerm's speed sequence.** SyncTERM
can emulate a line speed itself (`CSI Ps1 ; Ps2 * r`), but bytes already sent
cannot be skipped, it works only in SyncTERM, and turning it off again queues
behind the art. Server-side pacing works on every terminal and stops at once
on a key. The skipping key is swallowed, not passed on as Voidrunner passes
its interrupting key, because a prompt follows the art. A time limit, when the
SysOp sets one, sends the rest at once; a limit that sped the art up instead
was rejected, since the speed is part of how the art was meant to look.

**Decision 11 — a chosen speed plays to the end; a time limit is the SysOp's
choice.** Until v7.18.1 every paced draw stopped after a fixed 5 seconds and
dumped the rest, which the maintainer found looked broken: a SysOp who picks
2400 bps wants the art drawn at 2400 bps. A per-banner **Time limit** (off, 10,
30 or 60 seconds) now does what the cap did, and is off by default, so a speed
is obeyed unless the SysOp asks otherwise. A caller can still skip with any
key. Rejected: keeping a fixed cap, and a longer fixed cap, both of which cut
off a slow piece the SysOp chose the speed for.
