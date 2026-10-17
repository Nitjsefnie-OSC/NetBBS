# Issue #1110 — a SysOp corrects a caller's display name and birthdate — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Only the caller could set their display name and birthdate (Profile → Name &
details), so a SysOp could not fix a typo or remove a display name that had to
go. Normative description: §5.5 and §5.6.

**Decision 1 — the same reach as a password reset.** A SysOp may edit any
account, and a staff member with manage accounts may edit an account below 255
that holds no staff permission, never their own. Both fields are details of
the account the way its password is, so they follow that permission rather
than a new one. Rejected: SysOp only, which would leave the helper who runs
the node while the SysOp is away unable to remove an offending display name.

**Decision 2 — the caller's own rules, and one more for both.** The same
display-name rules as the caller's own edit apply (the reserved `=` marker,
the byte cap, the look-alike and staff-title checks of issues #835 and #843).
A birthdate may be neither in the future nor before 1900-01-01, for the caller
too: a typo such as `0198` would otherwise make anyone old enough for every
age gate. Blank clears either field, which a SysOp needs. The caller's own
screen takes the same keys for the same values (Decision 6).

**Decision 3 — audited, without the birthdate.** Each edit is recorded in the
account's admin history with who made it. A display name is shown to everyone,
so the record keeps the old and new name; a birthdate is private unless its
owner shows it, and the history is read by every SysOp and manager, so the
record says only that it was set, changed or cleared.

**Decision 4 — verified values are separate.** An age or real-name
attestation is not touched by these edits and still decides every gate; the
account screen says so when one is on record. The caller's own visibility
settings for both fields are left as they are. Display names do not travel
over NetBBS Link, so nothing is carried.

**Decision 5 (issue #1115) — revoking is an action of its own.** Clearing a
birthdate left the verified age standing, which is Decision 4 working, but
there was no way to take a verification back at all. Whoever may verify may
now revoke (`revoke_attestation`): a SysOp from the account screen's
`[V]erification: revoke`, offered only while one is on record, or anyone with
"Can verify identity" from the verify screen's `[R]evoke`. It asks first,
names the gates that will refuse the caller again, and is recorded in the
account's history. A verification the caller had shared over Link needs no
step of its own: the next sync signs a revocation for every live object whose
attestation is gone (`reconcile_issued_attestations`). Clearing the plain
field still never revokes. Rejected: clearing the birthdate or display name
also revoking, which makes a typo fix withdraw a verification someone checked
an ID for.

**Decision 6 (issue #1115) — the caller sees both, and can clear their own.**
A caller's Profile used to say only "Verified: age"; it now shows what this
node verified, with the value ("Verified by this node: born 1980-01-01, real
name …"), apart from the self-reported fields below it, so a caller who
clears their own birthdate can see why an age still counts. The display
name, location and birthdate open in a line of their own with the value in
it, and take the same keys as the SysOp's edit of them: Enter saves, blank
clears, Esc keeps. Before #1115 a caller could
change these but never remove them; #1115 cleared them with a typed `-`, and
the prompt that explained it left two columns of an 80-column screen to type
a birthdate into, so it was replaced. A birthdate still showing after a SysOp cleared
it was a screen opened before the clear: the stored value was gone.
