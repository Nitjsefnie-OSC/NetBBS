# Issue #900 — look-alike node names — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Friendly names were compared by Unicode form and case only, so "0utBound" beside
a known "OutBound" raised no warning and needed no qualifier. With #899 dropping
the DNS name from chat lines, that left the friendly name as the only anchor in
exactly the place where passing for a familiar node pays. Normative description:
§4.4.

**Decision 1 — two keys for two questions.** "Could a reader confuse these?"
uses the presentation skeleton (`look_alike_key`): the cryptographic-identity
warning, and every screen that qualifies a shared name (chat, node map, Who,
the board-origin picker). "Is this the same name?" keeps the exact key: a rename
under one fingerprint, the claim history's deduplication, and resolving a typed
reference. Rejected: resolving typed references by skeleton, which would let a
look-alike node receive what a caller addressed to the real one.

**Decision 2 — DNS names are not folded.** Registration already makes them
unique, and `presentation_skeleton` drops `-` and `.`, so `out-bound.example.org`
and `outbound.example.org` would become one. Screens that show a DNS name show
all of it, so Who and the board-origin picker add a technical identity only
where full labels could still be confused: the friendly names read alike and
the DNS names do not tell the nodes apart, because one has none or both share
it.

**Decision 3 — this node's own look-alike name is a warning, not a refusal.** A
SysOp who renames their node to read like a node it knows is told so and the
name is kept: two hobbyists choosing one name is harmless, and the nodes that
know both already flag whichever arrived second.
