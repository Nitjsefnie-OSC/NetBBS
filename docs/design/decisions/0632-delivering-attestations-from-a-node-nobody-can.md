# Issue #632 — delivering attestations from a node nobody can dial — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A remote identity attestation used to travel only by being pulled from its
issuer, with a cursor, and the issuer enforced its recipient list when it was
pulled. A node nobody can dial cannot serve a pull, and most real nodes are
outgoing-only, so nothing such a node published about its callers reached
anyone. Trust objects solved the same problem by depositing at the issuer's
relays (#627); that does not carry over, because an attestation carries a
caller's verified birthdate or real name and must reach only its recipients.

**Decision 1 — a sealed, signed snapshot per recipient** (the maintainer's
choice). The issuer sends each recipient node one `sealed_attestation_bundle`:
every signed attestation and revocation that recipient should currently hold
from it, unchanged, sealed to the recipient's key (§10's `encrypt_for`) and
signed by the issuer. Its outer envelope names only the issuer, the recipient
node, a `sequence` and a time. The issuer pushes it directly when the
recipient can be dialed and otherwise deposits it at the *recipient's* relays,
named in the recipient's descriptor. A relay keeps one slot per (issuer,
recipient), in its own table so it never takes a mail slot; a newer bundle
replaces an older one. What replaces the cursor is the recipient's last
applied `sequence` per issuer: an older or equal one is refused, so a relay
cannot replay a stale snapshot. A recipient that was away needs only the
newest bundle, which is what its slot holds. Rejected: a sealed *pull*, which
still needs a dialable issuer; and a push of individual changes, which brings
the cursor back as a per-recipient ledger and needs acknowledgements to notice
a relay losing one.

**Decision 2 — a snapshot is authoritative, so removing a recipient retracts.**
Whatever a recipient holds from an issuer and the latest snapshot leaves out is
forgotten, audited as "withdrawn for this recipient". Removing a recipient
sends it a final, empty snapshot, retried until a route takes it and given up
after 90 days. This reverses the cost #596 Decision 3 accepted, that a removed
recipient kept what it held until expiry. A revocation is not used for this,
because a revocation is a universal signed statement: sent to a removed node,
it could be passed to another and retract a value still valid there, whereas an
empty snapshot is sealed to and names one recipient. The cost: a relay that
withholds a newer bundle delays a retraction by up to the attestation's 90-day
lifetime, the window §5.5 already accepts for an issuer that goes dark.

**Decision 3 — a relay learns that A sent B something, roughly how much, and
when.** The plaintext is padded to a power of two from 4 KiB, so the size says
little, and no user identifier is outside the seal. The relay is the
recipient's chosen agent, under its signed consent, and already sees more of
a letter (sender and recipient user IDs). Because a bundle can displace
another in its slot, a relay never takes one on an unverifiable claim, unlike
a letter: the deposit carries the issuer's own hello bundle, which
authenticates itself against its root key (whose hash is the issuer's
fingerprint), is merged with any chain the relay already holds so a stale one
cannot revive a compromised key, and names the key the snapshot's outer
signature must verify under. Only the issuer can fill or replace its slot; a
deposit in another issuer's name is refused, whatever sequence it claims. The
recipient still checks everything itself.

**Decision 4 — the recipient's relays, learned from its descriptor.** Sealing
needs the recipient's key and verifying needs the issuer's, and two
outgoing-only nodes never complete a hello, so both sides accept a node known
by introduction (`resolve_known_signing_key`, #627 Decision 5). A recipient
with no route — no relay published, or none the issuer has met — is a visible
per-recipient status on the issuer's Published identity screen, never a silent
drop.

**Decision 5 — one path, and no overlap.** Everything moves to the sealed
push. A bundle is sent only to a recipient, and deposited only at a relay,
whose descriptor advertises `sealed_attestations`. The plan was to keep the
pull for one release for recipients that do not; the maintainer decided on
2026-10-02 to remove it in the same release instead (issue #1046), with its
route, its signed request type and its cursor table (migration 116). The
consequence, accepted: a node on v7.14.0 or earlier stops receiving
attestations from an upgraded issuer until it upgrades; the issuer's
Published identity says so per recipient ("needs a newer NetBBS to receive
this"), and the caller's count does not include it. Rejected: keeping the
pull for dialable issuers, which would give receivers two sets of semantics
and leave removed recipients unreachable.

**Bounds.** Snapshot plaintext at most 768 KiB (sealed and encoded it stays
under the 2 MiB request limit), refused visibly beyond that; at most 4,000
objects. A relay holds bundles from at most 32 issuers per recipient and drops
one after 90 days. An issuer sends to at most 20 recipients per sync pass,
re-sends when a recipient's snapshot changes and at least every 7 days, and
uses a time-based `sequence` so a restore from backup does not move it
backwards.
