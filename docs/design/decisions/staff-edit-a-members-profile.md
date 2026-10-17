# Staff edit a member's Profile — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The maintainer asked (2026-10-09) that whoever may edit user accounts can edit
every field of one through NetBBS itself. The account detail showed a few of
an account's settings; the rest -- bio, signature, location, blocks, display,
mail and chat settings -- only the member could change. Normative
description: §5.6.

**Decision 1 — their Profile, not more rows.** `[E]dit profile` opens the
member's own Profile screen acting on their account. Rejected: a row per
setting on the account detail, which would run to several pages at 80x24 and
repeat Profile's fields and help in a second place.

**Decision 2 — the reach of the display name and birthdate (issue #1110).** A
SysOp, or a staff member with manage accounts on an account below 255 that
holds no staff permission; never your own account, and never the guest
account. Each write is checked again in the database. The SSH keys stay a
SysOp's, and sharing a verification over Link stays the member's opt-in.

**Decision 3 — the username stays read-only.** It names the account to other
nodes: password-only Link identity, mail addressed by name, post authors, and
retired names. A rename is its own design if it is ever wanted.

**Decision 4 — recorded, without private text.** Every change is in the
account's admin history; a setting with its new value, text the member wrote
or names they block only as changed.

**Decision 5 — the guest defaults are a SysOp's (maintainer, 2026-10-09).** At
first `[E]dit profile` was not offered on the guest account. But what the guest
account stores is what every guest starts with, under each guest's own
per-call changes, and nothing could set it short of turning guest login off and
signing in as the account. A SysOp now sets it with `[E]dit profile` on the
guest account: the display defaults and the shared account's public face (bio,
signature, messaging and MRC settings), without Name & details, read receipts,
sort preferences, keys or password. Staff with manage accounts do not get it:
the defaults are the whole node's.
