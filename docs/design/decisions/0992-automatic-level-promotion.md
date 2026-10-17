# Issue #992 — automatic level promotion — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A public node either checked new accounts by hand every day or opened
everything to brand-new accounts; ReLink ran its own cron script. Normative
description: §4.3.

**Decision 1 — checked at login, not on a timer.** A level matters only
while the account is logged in, so the login is the moment to decide, and
the caller can be told. Rejected: a periodic sweep, which promotes accounts
nobody is using and needs a task of its own.

**Decision 2 — a level set by hand takes the account out of the rules**
(the maintainer's decision). Otherwise a rule undoes a SysOp's demotion at
the next login. The account screen turns the rules back on. At upgrade only
accounts the moderation log shows demoted start marked; earlier promotions
cannot be told from ReLink's scripted ones, and are left free to climb.

**Decision 3 — logins counted on the account.** Session history keeps at
most a few rows per account and prunes them as others log in, so a quiet
newcomer's count could fall. `users.login_count` starts from the rows still
there.

**Decision 4 — the node acts, and the log says so.** An automatic promotion
is logged with no acting account ("(system)"), not in a SysOp's name: no
person made that change at that moment. Rejected: naming the SysOp who set
the rule, which is what ReLink's script did.
