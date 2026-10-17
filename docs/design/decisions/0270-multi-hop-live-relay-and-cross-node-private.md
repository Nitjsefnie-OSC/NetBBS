# Issue #270 — multi-hop live relay and cross-node /private

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** close the two gaps §8.10.3's first vertical left open --
relay-of-relay live sessions and the private-conversation mode across
nodes -- without reopening the raw-proxy decision (§16 "Issue #168").

**Decision 1 (locked in) — three steps, all shipped together:** anchor
advertisement (`live_relays` in the signed endpoint descriptor),
dial-the-anchor (a requester that can reach the target's advertised relay
meets it there, single hop), and the chained bridge (two relays, A–R1–R2–B)
only when the requester cannot reach any of the target's anchors. Most
cross-anchor cases never chain; chaining is the resilience path, not the
common one.

**Decision 2 (locked in) — discovery is the target's advertised anchors,
not relay-side search.** The requester names the relay to forward toward
(`via_relay`), taken from the target's own descriptor. A relay-side
fan-out ("ask every reliable node whether B is there") was rejected: it
turns one request into up to a roster's worth, adds latency, and needs no
descriptor change only at the cost of search traffic on every miss.

**Decision 3 (locked in) — hop bound of one forward.** A forwarded request
(`hops: 1`) is never forwarded again, so a chain is at most two relays and
every relay's own caps and byte/idle bounds apply per hop. Longer chains
would need routing state no relay currently keeps and are not a v1 need.

**Decision 4 (locked in) — `/private user@node-fingerprint` ships; cross-
node `/dm` invites do not.** Private mode rides the existing live
direct-message path line by line (small); a cross-node fullscreen direct
chat needs mutual invite/accept frames and a shared room spanning two
nodes -- a separate step, roughly the size of the direct-message vertical.

All frame additions (`via_relay`, `hops`, `for_fingerprint`) ride real-time
protocol v3, unreleased at the time, so no further bump was needed.
Normative description: §8.10.3.
