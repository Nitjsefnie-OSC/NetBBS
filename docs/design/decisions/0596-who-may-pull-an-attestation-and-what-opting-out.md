# Issue #596 — who may pull an attestation, and what opting out retracts — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Found by the review of the v7.7.0 release. Two facets of one gap between what
a caller is told when they share a SysOp-verified birthdate or real name over
Link and what the node does.

*Disclosure scope.* `_handle_attestation_pull` admits any completed peer whose
signed request is fresh and whom `LinkPolicyAction.TRUST` allows, and
`load_issued_attestation_page` takes no requester at all. The receiver's
`link_attestation_authorities` is a list the issuer never sees. So every
federated peer can read every attestation this node has signed, while §5.5
will not show the same value to the node's own SysOp.

*Retraction.* `_revoke_issued` stamps the row and leaves `envelope_json`, which
carries `attested_value`, in place; the page query filters on nothing. A peer
that links next year and pulls from the start reads a value whose subject
opted out this year.

v7.7.0 shipped consent text that says both things honestly and enforces
neither. These decisions replace that text with enforcement. Normative
description: §5.5.

**Decision 1 — an issuer-side recipient list, and it starts empty.** A new
SysOp-configured, audited set of node fingerprints: the nodes this node tells.
The pull handler keeps every gate it has and adds one: the requester must be
on the list. An empty list means nothing leaves the node whatever its callers
have toggled. An upgrade seeds the list from nothing. In particular not from
the attestation authorities: whose verifications a SysOp accepts says nothing
about whom they are willing to tell, and the two lists point in opposite
directions. Rejected: correcting the consent text and enforcing nothing, which
is what v7.7.0 did as a stopgap and which leaves a value §5.5 withholds from
the local SysOp readable by every peer.

**Decision 2 — a recipient is a node, not a node and an attribute.** The
receiving side scopes an authority to `age` and `name` separately, and the
mirror image was considered. Rejected for two reasons. The caller already
scopes per attribute, with two separate toggles, so the finer knob exists
where the consent is. And a per-attribute grant makes each requester's stream
a function of its grant history, which the protocol cannot express: the pull
cursor is the subscriber's, it names a position, and a requester whose scope
widens has already moved past the rows it was not entitled to last week.
With a per-node list that cannot happen, because of Decision 3.

**Decision 3 — a node that is not a recipient is refused outright, and told
so.** HTTP 403 with `reason_code` `not_an_attestation_recipient`, in the shape
a policy rejection already has. Not a revocations-only stream, although a
revocation carries no value: serving part of the stream advances the
requester's cursor past the attestations it was not shown, and a later grant
would then deliver none of them until each came up for renewal. A refused
requester's cursor does not move, so a grant, a removal and a re-grant all
resume from the right place with no cleverness on either side. The refusal is
visible rather than silent because what it discloses is a relationship between
two nodes that the other SysOp has to act on, by asking to be added. §12.8's
silence protects a per-user gate decision; this is not one.

A node removed from the list stops receiving revocations too. That cost was
accepted here and is reversed by issue #632 Decision 2: removal now sends the
node an empty snapshot, which makes it forget what it held.

**Decision 4 — the caller is told how many, the SysOp sees which.** The
consent text says the value reaches only the nodes the SysOp has named, that
the SysOp may name more later, and the toggle shows how many that currently
is, so a caller can see that sharing to nobody shares nothing. Rejected:
per-caller approval of each recipient. A caller cannot evaluate a node
fingerprint, and re-asking every sharing caller at every grant would make the
list unusable by the one person who can evaluate it. The SysOp is already the
party the caller trusted with the verified value itself; choosing where the
node's assertion of it goes is the same trust, not a new one.

**Decision 5 — a retired attestation stops being served and loses its
value.** When an issued attestation is revoked or expires, the issuer blanks
the columns that carry the value (`attested_value`, the envelope, the
signature) and keeps the row: content ID, attribute, timestamps, what revoked
it. That removes the value from the live, served database; it is not forensic
erasure, and "Not done" below says what it leaves. The
page query serves only live attestations and every revocation, and filters on
liveness at read time so that what is served never depends on when a sweep
last ran. The migration redacts rows already revoked. This supersedes two
sentences of §5.5: "neither expiry nor revocation deletes the signed
historical row" and "the served stream includes expired and revoked objects".

Resumability survives, which was the stated reason for serving history whole.
A cursor names a position, and the tombstone keeps that position resolvable
for a subscriber whose last object was the one redacted. What a returning
subscriber needs from history is the revocation of anything it holds, and
every revocation is still in the stream, after the object it retires. A
retired attestation the subscriber never received is nothing it needs:
acceptance already ignores it, and serving it would hand over a value whose
subject has withdrawn it. A revocation whose target the subscriber never
received is skipped per object, as it is today.

**Decision 6 — a receiver forgets what it is told is withdrawn.** On ingesting
a revocation, and once an attestation it holds has expired, a node blanks the
stored value and envelope and keeps the row, which its revocation, effective
projection and audit rows reference. This is what "switching it off withdraws
it" can honestly mean between two nodes running this software, and the consent
text says the rest: a node that already copied the value cannot be forced to
forget it.

**Not done, deliberately.** No per-caller recipient choice and no
per-attribute recipient scope, for the reasons above. No revocation delivery
to a removed recipient. No way for an issuer to learn what a recipient did
with a value. No secure erasure: redaction blanks columns in the live
database, so the write-ahead log until its next checkpoint, freed pages, and
any backup taken while a value was shared can still hold it, and the issuing
node's own verification record keeps the value for as long as the SysOp keeps
the verification. The threat answered is a peer pulling a value, not someone
reading the disk; turning on SQLite's `secure_delete` for the whole node to
narrow that further was not judged worth its write cost.

**On upgrade.** Attestation sharing stops until the SysOp names recipients.
That is the intended default, and the release notes have to say it, because a
node that was sharing will otherwise read as broken.
