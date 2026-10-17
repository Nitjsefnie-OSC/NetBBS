# Issue #1004 — the access map — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Dogfooding found that a SysOp could not tell what giving an account a level
meant without opening every board, file area, channel, door, Community and
setting, nor be sure a promotion opened nothing unwanted. Normative
description: §5.7. Step 1 (#1005) builds the map; the screens follow.

**Decision 1 — one map, held to the checks by tests.** The map is computed
from the same effective-level functions the checks use, a test compares each
gate with the real check at every threshold, and a scan of the source fails
on any level check the map does not account for. Rejected: routing every
check through the map, which would rewrite every flow's gate for no change in
behaviour; and a hand-kept list, which is the drift the map exists to end.

**Decision 2 — levels only; conditions named, grants left out.** Age,
verified-name, members-only and hidden appear as a gate's conditions, and
read or write grants are not on the map, because they belong to accounts,
not to levels. Rejected: answering "what can this account do" in the same
structure, which the change preview (#1006) does per account where it needs
to.

**Decision 3 — a write gate opens at the higher of its read and write
levels.** That is what a caller experiences. The gate still shows its own
write level and where it comes from.

**Decision 4 — the change preview is a screen, not a question** (#1006). It
shows its content with `[A]pply` and `[B]ack` in the action bar, as §3.5 asks
of every screen. Rejected: a yes/no prompt after the list, which §3.5 keeps
for irreversible or network-touching actions, and a level change can be
undone. A change that opens and closes nothing skips the screen, since
there is nothing to check. Refusals are checked first, so a SysOp never reads
a preview for a change that would then be refused.

**Decision 5 — levels run from 0 to 255.** Nothing enforced the range before:
an account could be created at, or changed to, a negative level or one above
255. Both are refused now.

**Decision 6 — level names are labels in node configuration** (#1009).
Rejected: named levels as the stored value, with gates and accounts
referring to a name, which would make renaming a level a migration of every
gate and leave Link genesis events, which carry numbers, needing a mapping.
A JSON object in the config table, rather than a table of its own, because
it is a handful of short strings read together.

**Decision 7 — a name is typed where a level is asked for on the user
screen and the Levels screen, not in the resource editors.** Those are where
a SysOp thinks in names (promote alice to Member). A resource editor's level
fields keep taking numbers, and show the name beside the value.
