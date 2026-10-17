# Issue #168 — real-time relay for Link direct chat

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** decide between the two structurally different designs the issue
itself poses for live (Noise XX) relay between two mutually-unreachable
(outgoing-only-to-each-other) nodes: double-hop relay-as-participant
(the relay terminates one Noise session per leg and re-encrypts between
them) versus a raw-socket/TCP-level proxy below the Noise layer (the
relay blindly forwards bytes, never touching the handshake or its keys).
Not a newly-discovered gap — the design doc already named this as a
deferred "separate future protocol" (§8.10); this issue is that design
pass.

**Context that made this tractable now, not a decision in itself:** a
second, related idea is in discussion — turning ReLink (the project's
own persistent, internet-reachable test node) into a stable, always-up
default relay so a home SysOp who can't or won't expose a port has a
frictionless path onto the mesh, without that being a hard dependency
(any other willing peer, or a commercial provider for the parallel
managed-DNS idea under #201, works exactly as well — nothing forces
ReLink specifically). That product/infrastructure question is real but
still early and not decided here. What it *does* settle is the async-
relay model's own objection to the raw-proxy design: `relay_selection.py`'s
reliability-ranking machinery exists to route around relays that might
disappear, and a relay explicitly committed to staying up removes the
need to solve general reliability-ranked live-relay discovery before a
v1 can ship. The protocol decision below stands on its own regardless of
who ends up operating such a relay.

**Decision 1 (locked in) — raw-socket/TCP-level proxy, not double-hop.**
The deciding factor: raw-proxy requires **zero changes** to the already-
shipped `LinkRealtimeSession`/Noise XX handshake code. Two directly-
handshaked endpoints run the exact same mutual authentication they'd run
if actually adjacent; the relay is as invisible to Noise as any ordinary
router hop, since it never participates in the Diffie-Hellman exchange
and structurally cannot decrypt anything. That confines all new code to
connection setup (a small rendezvous exchange — "I want to reach
fingerprint X" / "I'm X, waiting"), not the confidentiality-critical
path itself, and it is a well-understood pattern elsewhere (a TURN
server, an SSH jump host, Tailscale's DERP relays), not a novel design.

A real alternative was considered and rejected for now, not dismissed:
double-hop can be built as a *hybrid* where the relay stays a genuine
protocol participant for control-plane frames (subscribe/presence/ping)
it's fine to see, while chat-content frames carry their own additional
encryption hop the relay can't read — giving real per-frame-type abuse
mitigation (raw-proxy can only see bytes/timing, never structure) on top
of content confidentiality. Rejected for v1 because it's solving a
structural-abuse-mitigation problem with no evidence yet that it's
needed, at real cryptographic-design cost paid up front: a second key-
exchange scheme layered inside the double-hop transport, a new session
shape (today's `LinkRealtimeSession` assumes exactly one remote
fingerprint, not a triangulated A-relay-B relationship), and a new frame
family. If frame-level abuse mitigation becomes a real operational
problem later, the hybrid design is the documented answer to revisit —
not re-derived from scratch.

**Trade-off stated plainly:** raw-proxy does not get either design to
zero metadata exposure — the relay still learns which two fingerprints
talked, for how long, and roughly how much traffic, same as the hybrid
double-hop's control-plane visibility would show. Raw-proxy also doesn't
plug into `relay_selection.py`'s existing reliability-ranking/consent
model at all — it's a structurally different kind of "relay" than the
async store-and-forward one, sharing a name but no code or selection
mechanism. If a fully decentralized *marketplace* of live relays (not
one well-known anchor) is ever wanted, that discovery/ranking layer
would need to be built fresh for this model rather than reusing the
async one — an accepted, deferred cost, not an oversight.

**Decision 2 (locked in) — bounded resource limits for live relay.** Modeled
on, but distinct from, the two existing bound families this touches: each
leg of a bridge is an ordinary `LinkRealtimeSession` (`netbbs.link.
transport`), already governed by its own `REALTIME_DEFAULT_*` bounds
(64-frame outbound queue, 100 frames per 10s window, 45s heartbeat lease,
5 protocol strikes); the async relay mailbox (`relay_mailbox.py`) already
bounds *its* resource at 50 envelopes per recipient. Neither transfers
as-is, because raw-proxy's actual cost shape is different from both:

- **Concurrent bridged-pair limit.** A relay running one bridge is a real
  third participant holding *two* live Noise sessions (one to each chat
  party) for that pair's entire conversation — meaningfully more
  standing cost per pair than either a single direct session or an
  at-rest mailbox entry. A new node-level `max_concurrent_relayed_pairs`
  limit (SysOp-configurable, same `nodeconfig.py` dataclass-field-plus-
  validation shape `ShutdownConfig` already uses) caps this directly,
  independent of the per-session frame-rate bound above, which caps a
  *single* session's chattiness, not how many a relay carries at once.
- **Per-pair byte-rate bound, not a frame-rate one.** `max_frames_per_
  window` counts discrete parsed frames — meaningless for raw-proxied
  bytes, which the relay by design never frames or parses at all. A
  bridge needs a bytes/second ceiling instead; exceeding it closes the
  bridge, the same "drop rather than silently degrade" precedent
  `LinkRealtimeSession.send()`'s existing slow-consumer handling already
  sets for a full outbound queue.
- **Idle-bridge timeout is protocol-agnostic, unlike the existing
  heartbeat lease.** `LinkRealtimeSession`'s own dead-peer detection
  inspects real ping/pong frames — raw-proxy structurally cannot do
  that, since the relay never sees frame semantics, only opaque
  ciphertext bytes. A bridge instead needs a dumb "zero bytes observed
  in either direction for N seconds" timer: the two actual endpoints'
  own (relay-invisible, encrypted) heartbeat traffic keeps a genuinely
  live bridge from ever tripping it, with no frame-aware logic required
  on the relay's side at all.
- **Bounded, timed-out pending-rendezvous table.** A node that shows up
  first, before its counterpart, waits — bounded in count (a cap on
  simultaneous pending requests per relay, the same "bound remotely
  influenced resources" principle `MAX_MAILBOX_ENVELOPES_PER_RECIPIENT`
  already applies to the async mailbox) and in time (a lone request
  that waits past its own timeout expires and is reported back to the
  requester as an explicit failure — CLAUDE.md's "fail clearly," not a
  request silently forgotten).
- **The existing slow-consumer-drops-the-session behavior composes
  across two hops for free, unneeding any new logic of its own**: each
  leg is its own ordinary `LinkRealtimeSession` with its own existing
  bound: a slow leg drops only that one session exactly as today, and
  the relay tears down the other leg in response (a bridge with only
  one live end isn't a bridge) — no bespoke two-hop-aware queue ever
  needs writing.

**Decision 3 (locked in) — v1 fallback UX for two mutually-unreachable
nodes.** Extends this project's own already-shipped local convention
rather than inventing a new one: `netbbs.net.chat_flow`'s `/msg`/
`/private` already require the recipient currently online, refusing
plainly ("X is not currently online.") when they aren't, with local mail
as the standing async alternative. The cross-node case gets the identical
shape: attempting live Link direct chat with a peer whose node can't
currently be bridged (no relay reachable, the relay's own pending-
rendezvous request times out, or the concurrent-pair cap above is full)
produces an explicit, never-silent refusal naming the situation plainly
(e.g. "<user> can't be reached for live chat right now.") and points at
Link mail (`link_message`/`relay_mailbox.py`, the *already-shipped* async
cross-node messaging path — not a new mechanism) as the immediate
alternative. The caller-facing message deliberately does not distinguish
*which* of the possible reasons applied — offline peer, no relay, relay
at capacity, rendezvous timeout — mirroring this project's existing
stance elsewhere (design doc §12) that such operational detail about a
*remote* node is not something a caller needs and could leak more than
intended about the other side's situation.

**Decision 4 (locked in) — rendezvous frame shape.** New frame types
added to the existing `REALTIME_FRAME_TYPES` set (`netbbs.link.
protocol`), not a new protocol version or frame family: extending that
frozenset without bumping `REALTIME_PROTOCOL_VERSION` is already this
file's own established pattern (e.g. the presence frames joined the
original subscribe/channel_message set the same way), and an old peer
encountering an unrecognized new type already fails cleanly via the
existing "unsupported real-time frame type" rejection — no separate
negotiation needed. Rides over the *requesting* node's own already-
authenticated `LinkRealtimeSession` to the relay (the ordinary session
that already exists from `relaying_for`/relay-consent setup) — no new
authentication mechanism, matching raw-proxy's own core premise of
confining new code to connection setup, never the confidentiality-
critical path:

- `relay_request` `{target_fingerprint}` — sent by either party wanting
  to reach the other through this relay.
- `relay_waiting` — the relay's reply when only this side has shown up
  so far (bounded by the pending-rendezvous timeout, Decision 2).
- `relay_ready` — sent to *both* sides once the counterpart has also
  shown up; raw-proxy byte-pumping between the two begins immediately
  after.
- `relay_reject` `{reason}` — the relay declines outright (pending-table
  full, no `relaying_for` relationship covering this pair, concurrent-
  pair cap reached) — explicit, matching Decision 3's fail-clearly
  requirement, never a silent drop.

This closes every acceptance criterion issue #168 named.

**Implemented** (§8.10.3 is the normative description): `netbbs.link.
realtime_relay` (relay server half and party half), `netbbs.link.
realtime_direct` (session establishment order, direct messages, the
reliable-node anchor connectors), `netbbs.net.link_direct` (the `/msg
user@node` flow and the receiving-side deliverer), five `[link]
live_relay_*` bounds with the defaults §8.10.3 lists, and a `relay_ready`
payload that names the relay's attach address so a party never has to
remember which address it reached the relay at. Two implementation
choices beyond the four decisions: the invitation reuses the
`relay_request` shape with the invitee's own fingerprint as target
(no fifth frame type), and a bridge is only ever offered between two
nodes both currently connected to the relay -- consent is the standing
session, so the asynchronous `relaying_for` model is not consulted.
