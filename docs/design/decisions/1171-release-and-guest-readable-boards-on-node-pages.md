# Issue #1171 — release and guest-readable boards on node pages — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Follow-up to issue #1165. Normative description: §8.13, and §8.2 for the
fields.

**Decision 1 — major.minor, never the patch level.** "Runs NetBBS 7.17" tells a
visitor what the board can do. "7.17.1" would also tell anyone scanning the
pages which boards have not taken a security fix. Rejected: the full version.

**Decision 2 — only boards the node's guest may read.** A board's name can say
more than its SysOp wants on the open web, and a board behind a level, an age
gate or an account is not open to the public anyway. The guest account is the
node's own statement of what a stranger may read (§4.6), so the check is the
read and age gate a guest meets, not a new setting. Rejected: every carried
board, and a per-board publish flag, which would be one more setting for the
same answer.

**Decision 3 — nothing extra while the page is off.** The two fields ride the
descriptor, which every peer sees, so a SysOp who turned the page off would
otherwise still publish them network-wide for nobody's benefit.

**Decision 4 — the genesis name, not the local one.** A carried board can have a
different local name on each node (§9.3, issue #671); the page lists the
board by the name its origin gave it, so the same board reads the same on
every page.
