# Issue #914 — a compromise reaching a node that knows the signer only by introduction — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The Phase 4 exercise's row 9: B knew A only through R. After A rotated its
signing key as compromised, R served B a copy of A's post signed with the old
key, which B accepted, because B's bundle for A predated the rotation and still
held that key as current. Nothing failed, so nothing asked for a fresher
bundle. Normative description: §8.11, "Stale bundles".

**Decision 1 — the carrier sends the chain beside the content** (the
maintainer's choice). Rejected: refreshing introduced bundles by age, which
leaves a window of up to the refresh interval and works only if a carrier
answers an identity request; and gossiping third-party `key_transition`
events, which would overturn the rule that a transition event is accepted
only from its own subject.

**Decision 2 — introduced identities only.** A direct peer's chain comes from
the peer, by hello and gossip; a third node is never an authority over it. An
identity the receiver has never been introduced to is not created from a chain,
since a chain carries no descriptor.

**Decision 3 — merged, never replaced.** Both a carried chain and a fresher
bundle are merged into what is on file, checked against the stored root key.
Without that, a stale bundle could undo a learned compromise.

**Decision 4 — bounded, and absent when there is no news.** At most 32 chains
of at most 64 transitions; a chain holding only the first authorization is not
sent. An ordinary response is unchanged, an older requester ignores the field,
and an older carrier simply sends none. Longer chains fall back to the
stale-bundle refresh.
