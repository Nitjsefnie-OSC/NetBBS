# Issue #594 — a deleted account's username stays retired on a Link node — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

`local_user_id` is the username, and `users.username` is unique only among
live rows, so deleting an account frees its Link identity for the next
registrant. Link mail sends and delivers by it (`deliver_link_message` resolves
the recipient with `get_user_by_username`), a carried post's `author_label` is
`username@home-node-fingerprint` and `link_events` keeps it, the trust subject
is derived from it, and a remote identity attestation names it. A
re-registered name inherits all of that: authorship, trust state, a live
attestation until its revocation propagates, and mail a remote sender wrote to
the previous holder. Normative description: §4.3.

**Decision 1 — retire the name; do not change the identifier.** A stable,
never-reissued per-account identifier was the alternative. It needs a
migration assigning one to every account, a wire decision about the
`node_vouched_user` payload every peer already has on disk, a display
decision because an opaque ID is exactly what must not be shown, and a
compatibility story for labels peers retain. It also would not be enough: a
Link mail address is `name@node` by design, so whoever holds the name gets
the mail whatever identifier sits underneath. Retiring the name is the part
every answer needs, so it is the part built.

**Decision 2 — recorded at deletion, on a node that has ever run Link.**
`delete_user` writes the retired name in the transaction that deletes the
account, when the node runs Link now or has at any time before. "Ever" is a
sticky marker the node sets the first time it starts with Link effectively
on. The migration that introduces it seeds it on a node that ran Link before
the marker existed, from any artifact Link leaves behind: a stored peer, a
retained event, a linked board, channel or file area, a piece of Link mail,
or a recorded decision to run Link. Stored peers alone would not do, since a
node can originate a linked board and republish it later without ever having
stored one. A node that has never run Link records nothing and
its names stay reusable, which is both BBS tradition and the cost the issue
names: a small board should not lose names forever to a network it is not on.
A recorded name stays retired whatever the Link setting does afterwards.
Registration checks the record case-insensitively, as the uniqueness index
does.

One kind of account is not retired: a registration still awaiting approval
that was never sent Link mail. Declining registrations is routine on an
approval-required node, and retiring those names would let strangers
permanently consume names merely by asking for them. The exemption rests on
the pending state because that state proves the account never had a session:
it is set only at registration, cleared only by approval, and refused by
every login path. "Never logged in" was considered and rejected as the test,
because it proves nothing: open registration hands a new caller straight into
a first session without recording a login, so an account that registered,
posted on a linked board and never returned has no login on record. Received
Link mail is part of the test because it reaches an account by name without
the account doing anything: delivery resolves the recipient by username,
pending approval or not, and acknowledges it. An approved account that was
never used is retired like any other; the SysOp can release it. The screen that confirms a deletion and the
deletion itself ask one shared predicate, so the warning cannot promise a hold
that does not happen.

The test is "ever", not "now", because what peers hold does not evaporate
when Link is switched off. Keying on the setting at the moment of deletion
would let an account that was Link-visible be deleted during a maintenance
window with Link off, re-registered, and then carried back onto the network
under an identity its peers already know. The price is over-reservation on a
node that once ran Link and no longer does, where an account that never
federated is retired anyway; the SysOp can release it (Decision 3), and the
alternative is tracking per account whether it was ever Link-visible, which
every Link subsystem would have to feed.

**Decision 3 — the SysOp can release a name, on purpose.** A list of retired
names reachable from user administration, with release as a confirmed,
audited action. A SysOp who deleted a test account, or whose caller comes
back after asking to be removed, is making the trust decision this project
leaves with the SysOp. Without a release the only repair is editing the
database.

**Decision 4 — a remote caller is told the name is unavailable; a SysOp is
told why.** Self-service registration refuses a retired name in the words it
uses for a taken one, so registration is no oracle for who used to have an
account. The create-user screen and the admin CLI say that the name belonged
to a deleted account, that it is held because the node is on Link, and where
to release it.

**Decision 5 — forward only.** The migration creates an empty table. The
moderation log names past deletions, but in free text, and reconstructing
reservations from a log line is the kind of inference that retires the wrong
name. Names already freed stay freed.

**Not done, deliberately.** Nothing repairs an identity a peer already holds
for a name reused before this ships. Nothing in the codebase renames an
account; a rename, if one is ever built, frees a name the same way a deletion
does and has to retire it the same way. Issue #1111 decided how a rename
works, and its old name is retired exactly so.
