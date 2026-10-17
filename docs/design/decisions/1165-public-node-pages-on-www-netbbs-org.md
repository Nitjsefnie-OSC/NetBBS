# Issue #1165 — public node pages on www.netbbs.org — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A node that registers a netbbs.org name and joins NetBBS Link got nothing it
could point people at. Normative description: §8.13, and §8.2 for
`node_page`.

**Decision 1 — managed-DNS names, met by Reliable Link.** The name is already
unique and screened, so it is the page's address without another namespace.
Rejected: a page for every node any part of the network knows, which would
publish unverified claims under the project's domain.

**Decision 2 — built ahead of time, not served live.** A periodic job writes
static pages next to the rest of www.netbbs.org. Rejected: a route in the
managed-DNS service, which would put a public page renderer in front of the
credential store.

**Decision 3 — Reliable Link's caller view, not its tables.** The page shows no
more than a caller on Reliable Link may see, which keeps out every SysOp-only
fact by construction. Rejected: reading the tables directly, where one
forgotten column would publish addresses or trust states. The address the
managed-DNS service last saw is never published.

**Decision 4 — an opt-out here, unlike the node map.** The node map has none
(issue #767 Decision 4), because a board missing from it must mean callers
cannot reach it through that board. A web page carries no such meaning: it is
the project describing a node to the whole internet, and a SysOp may not want
that. Published by default with `noindex`, so a new node shows up without
being found by every search engine; indexing is the SysOp's choice too. The
choice is a signed descriptor field, so no website account exists to lose or
to phish.

**Decision 5 — history follows the fingerprint.** A released name can pass to
another node, and the dates are about the node, not the name.

**Not done, deliberately.** Free text such as a tagline, which would put remote
prose under the project's domain; a reliability or uptime history, which reads
as shaming for a hobbyist board that sleeps at night. Software version and
carried boards waited for the descriptor to publish them; issue #1171 below
adds both. The tagline came later, on the terms issue #1172 set (§8.13): a
hard length cap, printable text only, escaped and never a link, never on the
badge, and a project-side list that takes one node's tagline down without its
page.
