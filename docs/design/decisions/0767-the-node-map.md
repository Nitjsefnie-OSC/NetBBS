# Issue #767 — the node map — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A caller had no way to see which boards the network holds, and a SysOp could
see only the nodes this one had met. Normative description: §8.12, and §8.2 for
`dial_in`.

**Decision 1 — callers and the SysOp, one screen.** The SysOp's view is the
same list with more rows and columns, and replaces the peer list behind Link
status. Rejected: a SysOp-only screen, which leaves callers the one audience
that needs a BBS list most; and two screens, which would drift apart.

**Decision 2 — guests through the level, not a toggle.** Guest login is an
account and no code branches on it (§4.6), so the map has a minimum level like
everything else a SysOp gates. Rejected: a guest switch on the map, which is
the first thing §4.6 rules out.

**Decision 3 — unverified candidates are the SysOp's alone.** A peer-list
candidate has completed no hello and names nobody who vouched for it, and
anyone can put a name on one. Introduced nodes are listed for callers: they
carry a verified bundle and name their carrier.

**Decision 4 — no opt-out.** A board missing from the list must mean callers
cannot reach it through this board. With an opt-out it could also mean its
SysOp asked, and a caller could no longer tell whether a missing board is
unreachable or merely unlisted; a list that says it may be incomplete is one
nobody trusts.
The one gap that remains is structural, that nodes pass on only nodes they have
met, and the title states it. Rejected: an `unlisted` descriptor flag. Leaving
quarantined and blocked nodes off the caller view is not an opt-out: it is
this board's own judgement, and such a node is unreachable through this board,
which is exactly what its absence tells a caller.

**Decision 5 — "last heard" is first-hand or signed by the node.** Contact
this node observed, or a descriptor time the node signed itself. Rejected: the
peer record's update time, which a peer list refreshes secondhand and which
made dead peers look live (issue #766); and the time a carrier's bundle
arrived, which says when this node asked, not when the other one ran. The
signed time is capped at when this node first stored it, because a skewed
or dishonest clock could otherwise date a descriptor years ahead and never
go stale.

**Decision 6 — dial-in addresses are signed by the board they describe, and
stated by its SysOp.** They travel in the descriptor because a descriptor
reaches every node that knows the board, introduced ones included, and a
carrier cannot alter it. They are stated rather than derived because a node
cannot see what is in front of its listeners (the issue #201 entry, Decision
6); the SysOp's screen suggests entries from the node's DNS name and
listener ports, and publishes nothing the SysOp has not saved, except an
`https://` `[web] public_url`, which the SysOp has already stated. Rejected: deriving them from
the listener ports, which on the shipped defaults would advertise ports that
nobody can dial from outside; and a new wire message, when an optional
descriptor field is how #669 already extended the descriptor. Plain `http://`
is not accepted, for the reason the #201 entry gives for the web listener.

**Decision 7 — a bad dial-in claim costs only itself.** Malformed entries are
dropped where they are read and never fail a hello. Rejected: refusing the
hello, as invalid profile claims are, which would let a typo in a display field
cut a node off from Link.

**Not done, deliberately.** A drawn topology: nodes do not record who passed
on a candidate, and edges beyond this node's own would be guesses. Jumping from
a node's detail view into its boards. Removing stale nodes by age.
Reachability claims beyond direct and introduced, such as whether Link mail
reaches a node.
