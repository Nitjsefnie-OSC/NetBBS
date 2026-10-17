# Issue #843 — aliases that pass for the SysOp; invitations nobody saw — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The persona test found `/nick InkWell[sysop]` accepted and shown as
`<~InkWell[sysop]~>`, beside a SysOp whose own status bar reads
"InkWell[sysop]". A direct-chat invitation to a caller in Who's online sat
unseen while the inviter waited. Normative description: §6.3.

**Decision 1 — the username beside every alias.** Refusing look-alikes
narrows the gap; showing `alias|username` closes it, since no alias can then
stand alone. Issue #64 kept the stream to the alias alone as less cluttered.
The field test is the evidence that clutter was the lesser cost.

**Decision 2 — every username is protected from aliases, only SysOp names
from display names.** An alias is chosen to be a different name, so refusing
one that reads as another caller costs nothing. A display name is meant to be
a person's own, and two callers named Anna must both be able to use it.

**Decision 3 — local aliases only.** A Link name is shown as `name@node` and an
MRC name with its board, and neither is granted here, so this node has nothing
to refuse. Rejected: comparing aliases against remote names too, which would
make a local alias depend on who happens to be linked.

**Decision 4 — tell a busy invitee, do not interrupt their screen.** The
notice uses the path a SysOp's message already takes into any screen. Rejected:
answering the invitation from inside every picker, which would spread the
invite handshake across screens that own their own keys; and writing into a
door or a Zmodem transfer, which would corrupt what that screen is drawing or
sending.
