# Issue #11 — canonical Link format

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§7.2/§7.3/§7.4 now state the complete rule: canonical byte encoding (sorted
keys, recursive NFC, compact separators), the safe-integer bound, duplicate-
key wire rejection, omitted-versus-null field semantics, mandatory
object-type domain separation, event-identity distinctness (a nonce for
immutable creation events; `previous_event_id` chains, never `created_at`,
for per-object chains), `(home_node_fingerprint, local_user_id)` as a
node-vouched author's globally-scoped identity, and golden test vectors
(`tests/fixtures/link_canonical_vectors.json`).

Still open: this specification and its vectors exist only as this
codebase's own Python implementation plus one fixture file. No independent,
non-Python implementation has yet exercised the vectors to prove real
cross-language interoperability, and the rule is not yet published as an
external protocol document outside this repository. Closing that gap is
implementation/publication follow-up, not a further design decision.
