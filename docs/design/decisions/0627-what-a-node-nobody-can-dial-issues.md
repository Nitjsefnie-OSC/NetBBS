# Issue #627 — what a node nobody can dial issues — trust objects closed, attestations open

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Found while working out how the Phase 4 exercise could run on the three live
nodes, two of which are outgoing-only. Trust objects and attestations are both
pulled from their issuer, and an outgoing-only node has no address, so a vouch
its SysOp issued was signed, stored and served to nobody, and said nothing
about that. Normative description: §12.7.

**Decision 1 — the issuer deposits at its relays; subscribers pull the
carrier form.** The wire already defined a pull whose responder is not the
issuer, and the cursor was already kept per responder and issuer. Rejected:
flooding trust objects with content, which §12.7 rules out for the reason it
always did, and having subscribers wait to be dialed, which an outgoing-only
subscriber never is.

**Decision 2 — deposited objects are stored apart from admitted ones.**
Carriage and authority are different facts about an object, as "introduced"
and "met" are about a node (#630). Rejected: a flag in the admitted store,
where an object present counts as replayed and would never be applied after a
later reporter grant.

**Decision 2a — a relay admits what it carries in its own pass, never on
arrival.** Rejected: admitting inside the deposit handler, which only ever
sees one request's objects. A depositor named a reporter afterwards, a
widened grant and a batch rejected for a reason time undoes would each have
left objects that are never offered again.

**Decision 2b — a deposit names the object it continues from.** A depositor
sends only what is new, so nothing else would ever tell it that a relay has
lost something. Rejected: depositing everything on every pass, and trusting
the relay's count.

**Decision 3 — relay consent is the gate.** A relay already decides whom it
holds mail for and how many; the same decision covers trust objects. Rejected:
accepting deposits from any established peer, which would make every full
node a store for every node it knows.

**Decision 4 — only the issuer deposits.** The objects authenticate
themselves; their order does not.

**Decision 5 — verification accepts an introduced issuer; authentication of a
wire peer does not.** `resolve_known_signing_key` is for checking something a
third party delivered. Every route that authenticates its caller keeps
`resolve_peer_signing_key`.

**Decision 6 — attestations take their own path.** Decided in issue #632's
entry: a sealed snapshot per recipient, through the recipient's relays.

**Not done, deliberately.** A node that drops a relay tells nobody, it only
stops naming it, so a relay does not forget what it carried for it; expiry and
the per-depositor bound are what limit that, and revocations, which do not
expire, count against the bound. For the same reason a relay decides whether
to read its own copy by the depositor's descriptor and not by its own record
of whom it agreed to relay for. A
subscriber does not try relays it has not met. SysOp-written trust signals
remain #589's next slice.
