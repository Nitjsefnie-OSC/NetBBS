# Issue #1105 — a gated resource names its gates on entry — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller entering a gated board, file area or channel saw nothing about why it
was gated until someone was refused. Normative description: §3.6.

**Decision 1 — one header line, only for gates that restrict someone.** The
values are the effective ones after the Community cascade, the ones the checks
use. A level of 0, an explicit minimum age of 0 and a write level no higher
than the read level are left out, and an ungated resource gets no line, so most
screens are unchanged. Rejected: a line on every resource ("Requires:
nothing"), which adds a row everywhere to say nothing.

**Decision 2 — shown to everyone who can enter.** A caller who already passes
learns why others cannot; a SysOp, who passes every gate (§5.5), sees what
the resource asks of callers. Rejected: staff and moderators only, which hides
the explanation from the callers it is for.

**Decision 3 — on the entry screens, not in lists.** Board, file area and
channel entry carry the line. Lists keep their "needs verification" note and
the console's gate column (§3.6); a row has no room for the full set, and a
list's slot art (§3.2) is the SysOp's own drawing.

**Decision 4 (issue #1115) — the caller's unmet gates are marked on the same
line.** A caller who could read a board but not post saw "Requires: level
255+ to post" and, below it, "Read only: posting needs level 255.", the same
fact twice. The gates the caller fails, asked with the access checks
themselves (`unmet_gates`), are drawn in `ERROR_COLOR`, with ` (not met)` for
an ASCII caller, and the separate line is dropped wherever the marked gate
explains it. The maintainer's wording was "the warning colour"; it is the
error colour because `WARNING_COLOR` (amber, 214) sits next to `GATE_COLOR`
(orange, 208) and the mark would not show. Rejected: keeping both lines and
rewording the second, which still says one thing twice.
When the line is too wide for the screen, the unmet gates move to its front,
since it is cut from the end; and the separate line is dropped only when every
unmet gate survives the cut whole (`unmet_gates_shown`), so a narrow screen
keeps the reason.
