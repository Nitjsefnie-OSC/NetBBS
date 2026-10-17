# Issue #589 — trust-object issuance — slice 1 decided and built; signals open

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Until this slice no node could issue a trust object. `trust_wire` verified,
stored, re-served and enforced on a peer's signed objects, and nothing in
`src/` called a builder, so the carrier store was empty on every node and
§12.7's pull served nothing. No dogfood run, however long, could have shown
trust propagating. Normative description: §12.4 and §12.7.

**Decision 1 — slice 1 is the vouch, and only by a SysOp's act.** A vouch is
the one trust object whose trigger needs no judgment about evidence: a person
decides to stand behind an identity. When a node *accuses* another, which
observations become a signed signal, and whether any of that is automatic,
stays open in the issue. `build_trust_signal`, the digest-evidence path and
the local-observation lifecycle remain without a production caller, and the
ratchet in `tests/test_link_production_callers.py` still lists them.

**Decision 2 — an intent, and one reconcile that signs.** The screen records
an intent; `reconcile_issued_vouches` decides what should exist and signs it.
This is the shape remote attestation already has, for its reasons. The offline
admin console has no node identity, so a screen that signed would not work
there. And two code paths that each decide when a signed object should exist
eventually disagree, so the sync pass and the SysOp console call the same
function, and the listing derives its status from the same predicate.
Rejected: signing in the screen, with the sync pass only renewing.

**Decision 3 — own objects live in the carrier store.** A vouch this node
signs is stored in `link_trust_wire_objects` under its own fingerprint, which
is exactly what the existing pull serves for a requested issuer. No new
endpoint, no new wire type, no separate issued-objects table. It is not
recorded in `link_trust_vouches`: that is what local policy counts, and it
counts only configured reporters.

**Decision 4 — the reason is mandatory, bounded and published.** It is inside
the signed payload, so every subscriber stores it, and the screen says so
before asking for it. Replacing it retires the old vouch and signs a new one,
since signed bytes cannot be edited. 280 characters, because nothing on the
receiving side bounds it short of the response's byte limit.

**Decision 5 — 90 days issued, renewed at 30, reissued on key rotation.** The
180-day ceiling is what a receiver tolerates; a node that goes dark cannot
withdraw, so the issued lifetime is the window in which an unseen withdrawal
still leaves support standing. The same numbers as an issued attestation, so
there is one pair to remember. Rotation is detected by verifying the stored
signature under the current key rather than by remembering which key signed,
which needs no column and cannot drift.

**Decision 6 — a restriction here suspends the vouch, and the intent
survives it.** Vouching for an identity the issuing node has quarantined or
blocked says something the node does not act on, so the reconcile revokes it
and the screen refuses to record one. §12.4 makes that safe: a revocation
accuses nobody. The intent is kept so that lifting the restriction restores
the vouch without anyone having to remember it.

A rotation also re-signs what it would otherwise orphan. A subscriber that has
learned the new key can verify nothing the old one signed. For a vouch the
reissue covers that. A revocation is reissued by nothing, so one signed shortly
before a rotation and not yet pulled would be skipped as an old-key object,
and the vouch it retires would stay live at that subscriber until it ran out.
The reconcile therefore signs such a revocation again under the current key
while its target has not expired; a subscriber that already holds the first
one skips the second as a repeat.

**Decision 7 — the receiving side stops treating an unusable object as a
hostile response.** Three rejections that were fatal to a whole batch became
per-object skips, and changing a reporter's grant now resets its cursor
(§12.7). None of this could be reached while nothing was ever issued. With one
real issuer and one subscriber that has established it, it is reached as soon
as the issuer's SysOp vouches for a caller and the subscriber only granted it
node vouches: the batch is abandoned and that subscription never moves again.
The same would go for the first operational-key rotation. A slice that let
nodes issue vouches and left their subscribers wedged would not have made the
subsystem real.

The rotation handling above became reachable with issue #624, which built the
rotation itself; the Phase 4 exercise gained its rotation row there.

The same decision covers a responder that no longer knows a subscriber's
cursor, filed as #621 while this slice was in review and fixed with it for
both pulls, because it is the same wedge by another route and the recovery
exercise restores a node on purpose.

An object signed by a key the subscriber does not know is the one case that is
*not* skipped, because there the subscriber may be the stale party, and a
skip would lose every object the issuer re-signed. The first cut of this slice
skipped it; review caught that it turned a rotation into permanent loss.

**Decision 8 — pages in storage order.** See §12.7. The attestation page made
the same change for the same reason.

**Not done, deliberately.** No vouch for the node's own users and none for
itself. No trust signals. A subscriber told nothing about *why* an object was
skipped beyond its diagnostics log. (Carrier pulls in the sync loop, also
left out of this slice, followed in issue #627.)
