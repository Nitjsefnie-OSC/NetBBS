# Issue #672 — carriers holding copies signed by a compromised key — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

After a compromise rotation the origin re-signs its own content, but a re-signed
object keeps its content id, and inventory diffs by content id: carriers kept
serving the old-signed copies, and the re-signed ones never spread past the
origin's own peers. Normative description: §8.11, "Stale copies".

**Decision 1 — a node that learns a compromise treats what that key alone
signed as missing** (the maintainer's choice). It stops declaring and serving
those copies, keeps their projections, and takes the re-signed copy in place
when the ordinary inventory exchange offers it. No change on the wire. Rejected:
the origin pushing its re-signed events once to each peer, which reaches only
its own peers and leaves every carrier further out, which already holds the old
copy, as stale as before; and naming the signer in every inventory entry, which
spreads through every hop but changes the inventory format of every event for a
rare case.

**Decision 2 — learned from any source.** A direct peer's revoke or hello, an
introduction, or a chain a carrier sends. A carrier also sends the chain of a
signer whose content the requester only declares, when that signer has marked a
key compromised, since a requester holding a stale copy is served nothing of
that signer's (§8.11).

**Decision 3 — bounded.** An identity's chain is looked at again only when it
has changed since the sweep last saw it, and a hello or introduction looks only
at the identity it changed, so an ordinary hello verifies nothing. Each newly
compromised key costs one look through the stored events of the kinds inventory
carries, once per process; a stale mark is stored, so a restart does not forget
it. Mail, its acknowledgements and key transitions travel outside inventory and
are left as they are.

**Decision 4 — only an event's own signer speaks for it.** A chain is any
root's to write, and nothing stops a node from authorizing another node's
public key as its own and then declaring it compromised. A copy is therefore
tested only against the compromised keys of the identity it names as its
signer (author, origin), never against another identity's; such a claim
affects nothing but the claiming node's own content. Rejecting a chain that
claims a key another identity already holds was considered and not done: the
receiver cannot know every identity's keys, so the rule could not be enforced
consistently, and attributing by signer already makes the claim harmless.
