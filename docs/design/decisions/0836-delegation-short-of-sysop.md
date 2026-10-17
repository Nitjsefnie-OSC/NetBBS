# Issue #836 — delegation short of SysOp — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A SysOp who stepped away left signups and held posts that nobody could act
on, and the only way to hand them over was level 255, which includes power
over the SysOp who gave it. Normative description: §5.6, with §4.3 and §5.2.

**Decision 1 — named staff permissions, with Co-SysOp as a preset.** Approve
accounts, manage accounts and moderate everything, granted per account, and
a one-step Co-SysOp preset that sets all three. Rejected: a Co-SysOp level
band such as 250-254, the classic BBS convention, because nodes already use
those levels as resource gates, and an upgrade would silently give console
powers to accounts a SysOp raised only to open a board; and account authority
as a new kind of moderator grant, which would stretch a per-resource table
into node-wide authority and still leave no one-step way to hand over the
node's routine work.

**Decision 2 — staff never reach the SysOp.** Staff act only on accounts below
255 that hold no staff permission, cannot raise anyone to 255, and cannot
grant anything. A second SysOp at 255 keeps full power, including over the first;
that is what 255 means, and staff is how to give less.

**Decision 3 — everyday account work, but no deleting and no 255.** Manage
accounts covers what a helper needs while the SysOp is away: disabling and
enabling, password resets for callers locked out, and levels up to 254 so a
helper can open level-gated boards to members. Deletion is permanent and, on a
Link node, retires the name (§4.3), where a disable can be undone by the SysOp
on their return. Level 255 is the SysOp's own authority and is given only by a
SysOp.

**Decision 4 — read and write grants pass level gates.** The bits already
existed and did nothing. Giving them meaning lets a SysOp open an
announcements board to a helper without a new concept. Age and verified-name
gates still hold, because they are facts about the person, not trust the SysOp
extends.

**Decision 5 — away is per person, and never outlives what it says.** A
node-wide notice would be wrong the moment one of two SysOps came back. A
return date that passes ends the notice. A notice without one is shown with
the day it was set and reminded to its owner at each login, so callers can
judge a stale one and its owner is prompted to end it. Rejected: requiring a
return date, which a SysOp who does not know when they will be back could
only guess.
