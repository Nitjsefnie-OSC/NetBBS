# Issue #624 — guided operational-key rotation — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§4.5 said rotation was "a guided SysOp action", but no screen, command or
task rotated an operational key. The receiving half was built and tested and
could not be reached. Checking what a rotation would actually do found a
second gap. Every event branch verified a signature against the sender's
*current* key only, which made §4.5's "historical signatures remain
verifiable" false. So the first signing-key rotation on a real node would
have left all of its boards, posts, files and in-flight mail unverifiable to
any peer that had not received them yet, carriers' copies included, and a
board it originated could no longer be joined.

**Decision 1 — two kinds of rotation, stated by the root.** A rotation either
*retires* the old key or declares it *compromised*. The distinction is an
optional `"compromised": true` on the root-signed `revoke`. A retirement is
byte-identical to every revoke built before the field existed, and older
receivers ignore the field. Since no node had ever rotated, the meaning of a
plain revoke was still free to choose. It means retirement, because the
common case must not cost anything. Rejected: one mode in which every
rotation is a compromise, which would make routine hygiene re-sign and
re-push the node's whole history. Also rejected: honouring every key the
chain ever authorized, which would make rotation no answer to a leak, the
very case the issue exists for.

**Decision 2 — which checks accept a retired key.** Long-lived events accept
one: genesis, posts and their revisions, lifecycle events, channel messages,
file descriptors, mail and its acknowledgements. They are checked against the
current key and then every key retired without being called compromised
(`verifying_operational_keys`). Everything signed fresh for one exchange
still checks the current key alone: hellos, descriptors, requests, chunk
descriptors and withdrawals. So do trust objects and attestations, which
their issuer re-issues on rotation (§12; #622 for vouches, #623 for
attestation revocations). There an old-key signature is a stale copy or a
replay, never history.

**Decision 3 — a compromise response re-signs the node's own objects under
the same ids.** A content id does not cover the signature, so a re-signed
object is the same object to every peer. `resign_own_content` selects rows by
the signature itself: an object is re-signed exactly when it verifies under
one of the node's compromised keys. It runs after the rotation and again at
every startup, where it costs nothing on a node with no compromised key and
finishes a response that a stop interrupted.

A copy that another node already holds is not reached. It stays accepted
there, and a node that later pulls it from that carrier skips it *per
object*, as it does a trust object signed by a superseded key: a stale copy
must never end the response it arrives in. A carrier that has learned the compromise holds such a copy as
stale and asks for it again, so the re-signed copy reaches it and then the
nodes behind it (§8.11 "Stale copies", issue #672).

**Decision 4 — a rotation is saved before anything live changes.** It is also
journaled, because the node is running. The new key is staged beside its
file, `transitions.json` is replaced (the commit point), and only then is the
key moved into place. `NodeIdentity.load` finishes a staged key the chain
already names and discards one it does not. A save that fails leaves the
running node exactly as it was. Retired signing keys are kept under
`retired/` for one purpose: opening mail a peer sealed to the old key before
it learned the new one. Nothing signs with them.

**Decision 5 — the running node reads its identity at the moment of use.**
`LinkContext.node_identity` and `LiveDirectChat` read through
`LinkNode.identity` instead of holding a copy. A caller who opened a screen
before the rotation would otherwise go on signing with the revoked key. The
real-time listener and every standing anchor connector hold their own copy,
so a transport rotation hands them the new identity *before* it closes the
sessions keyed to the old one. Otherwise a reconnect could race ahead with the
key being retired.

**Decision 6 — the surfaces.** On the running node the action is **Link
status → Keys**. The screen shows the root fingerprint, which rotation never
changes, and each key's history. **Signing key** and **Transport key** each
open a screen with **Rotate** and **Compromised**, and each of those takes
one confirmation. On a stopped node the action is `python -m netbbs.admin
rotate-key {signing,transport} [--compromised] [--identity-dir DIR]`, which
refuses while a node process holds the database. Both surfaces write a
`rotate_node_key` audit entry. The Phase 4 exercise gained a rotation row.

Not built: a screen action that declares an already-retired key compromised.
The chain and `operational_key_history` accept that second revoke, and
nothing issues it yet.
