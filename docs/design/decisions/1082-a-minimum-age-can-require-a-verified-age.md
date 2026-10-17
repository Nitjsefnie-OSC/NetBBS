# Issue #1082 — a minimum age can require a verified age — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A SysOp could not run an area that needs a verified age: every age gate
accepted a self-entered birthdate when the account had no age attestation.
Normative description: §5.5.

**Decision 1 — shaped like the name requirement.** A nullable
`age_requirement` (`NULL` or `verified`) on boards, file areas and channels,
`default_age_requirement` on Communities, and an MRC open-room setting, with
the same Community cascade. Rejected: a separate boolean, which would not
inherit the way `NULL` does, and folding the flag into `min_age`, which would
change the meaning of a stored number. Existing gates are `NULL` on upgrade, so
none changes meaning.

**Decision 2 — old enough by one's own birthdate is not hidden.** Such a
caller sees the resource marked "needs verification" and is refused on entry
with how to get verified, mirroring the name requirement. Too young, or no
birthdate, still hides it: those callers have nothing to act on.

**Decision 3 — a local SysOp passes; nobody else does.** *Amended
2026-10-06.* The issue first decided that nobody bypasses the gate, level 255
included, matching the name requirement. The maintainer reversed that for both
gates: a SysOp could otherwise be locked out of an area on their own node, and
level 255 already overrides every level gate and moderator grant. Level 255
now passes the minimum age, the verified-age requirement and the name
requirement (§5.5); staff permissions and level 254 still do not, and remote
authors are still held to their own attestations. Rejected: a bypass for staff
too, which would let a delegated role skip a legal gate the SysOp set.

**Decision 4 — carried over Link as a recommendation.** A genesis carries
`default_age_requirement` beside `default_name_requirement`, omitted when
unset, so a genesis from a node that sets none is unchanged. A carrying node
stores a known value and drops anything else.

**Decision 5 — edited in the Min age field.** The editors show
"18, verified only" and take `18v`, so no editor gains a row: the area and
channel screens must still fit 80x24 whole.
