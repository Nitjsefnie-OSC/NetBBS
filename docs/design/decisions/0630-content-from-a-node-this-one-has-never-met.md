# Issue #630 — content from a node this one has never met — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Found by running the planned three-node deployment in the loop harness: one
full node and two outgoing-only nodes that seed off it and share one of its
boards. Normative description: §8.11.

A node could verify nothing signed by a node it had not completed a hello
with, and two nodes complete one only when one dials the other. §8.8 accepted
that as "rarely restrictive" on the expectation that small deployments end up
fully meshed. They do not: a node dials its seeds, everyone is given the same
seed, and two outgoing-only nodes cannot dial each other. So in the ordinary
topology the two never saw each other's posts. Under enforced policy the
unknown author read as probationary and was refused quietly, was not listed
as a trust subject, and so could not be established; each refusal was
downloaded again every pass, and 200 of them starved the subscription. With
the author established by some other route, the stranger check rejected the
carrier's whole response from the first post.

**Decision 1 — a carrier may serve a third node's hello bundle.** §10.6 and
the #90 entry had already worked out why this is safe and deferred it as not
needed. It is needed. The bundle verifies against itself, so the carrier is
trusted with nothing and can at worst withhold.

**Decision 2 — introduced is not met.** An introduced identity verifies what
it signed and does nothing else. It cannot push, pull, relay or be mailed,
and it is stored apart from peers so that no present or future check of "is
this a peer" can mistake it for one. Rejected: a flag on the peer record,
which would have made every reader of that table responsible for remembering
it, the mail path among them.

**Decision 3 — introduce before policy, and start on probation.** The
introduction grants nothing, and it is what makes the node visible to the
SysOp who alone can establish it. Rejected: introducing only authors policy
already admits, which is circular, since a node that is not a subject is
never admitted.

**Decision 4 — carriers pass on introduced identities too.** Rejected: the
peer list's first-hand-only rule. That rule exists because a secondhand
address degrades with distance; a bundle does not.

**Decision 5 — one unusable event costs one event.** Responses from a carrier
are handled per event, and an event that cannot be used yet is set aside and
declared as seen. Rejected: only skipping it, which leaves it to be sent
again every pass and lets a page fill with such events. A refusal that does end
a response is returned with what was accepted before it, which the caller
persists: a batch that raised half-way had already put its earlier events
into memory, the caller persisted none of them, and being now "known" they
were never accepted, and so never persisted, again.

**Decision 6 — a stale bundle is refreshed from the carrier.** Key
transitions of third nodes are not gossiped, and this slice does not change
that; it asks again instead.

**Decision 7 — a met node outranks an introduced one by name.** Introduction
puts names on file that nobody here vouched for by dialing. The
familiar-name comparison is therefore one-sided (§8.11), mail addresses
resolve among met nodes only, and meeting a node re-judges every introduced
node that wears its name, whichever was on file first. Rejected: first-seen
wins throughout, which hands a real peer's name to whoever posts first.
Between two nodes neither of which this node has met it is still the rule,
there being nothing to tell them apart.

**Decision 8 — a carrier answers only for whose content it served.**
Rejected: gating the route like the peer list, which a peer on probation is
refused. A new node is on probation at its seed for its first month, so that
would have withheld introductions from exactly the nodes that need them.

**Not done, deliberately.** Verifying §5.5 attestations against an introduced
identity; trust objects followed in issue #627. Link mail to
an introduced node (§10.6 stays deferred). Automatic graduation for nodes
never met. Any way for an introduced node to be dialled, which includes
fetching its files. Persistence of the set-aside list, which is rebuilt at
the cost of one repeat per event after a restart. A way for a requester to
tell a responder which signers to leave out, which is what would lift the
10,000-event bound.
