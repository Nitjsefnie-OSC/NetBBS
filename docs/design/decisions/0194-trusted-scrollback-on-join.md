# Issue #194 — trusted scrollback-on-join — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** decide whether/how a node gets recent scrollback the instant it
live-subscribes to a linked channel. Today it gets presence plus
messages going forward only (§8.10.2's own "does not offer shared recent
scrollback"), a deliberate v1 scope cut, not an oversight — but a real
gap against this feature's own "frictionless" bar.

**Not a permanent-loss problem, and not shaped like issue #168 at all.**
Every channel message on a linked channel is already a signed, durable
`channel_message` event (`queue_channel_message_if_linked`), delivered to
every linked node eventually through the existing inventory/pull-based
catch-up path (issue #85) regardless of live-connection status.
`netbbs.link.sync`'s scheduling loop runs every `sync_interval_seconds`
(five minutes by default), so today a freshly live-subscribed channel
can sit silent for up to that long before the *existing* async path
fills in what was missed. This decision only shrinks that window at
subscribe time — it never touches durability. It also carries none of
issue #168's relay/crypto-fork complexity: `ensure_live_subscription`
(`netbbs.link.realtime_channels`) already always dials the channel's
*origin* node specifically (`channel_origin_fingerprint`), never a third
node, so there is exactly one source of truth to ask, not a multi-hop
question.

**Decision 1 (locked in) — source is the origin's own local scrollback,
sent as a new frame alongside the existing subscribe-time
`presence_snapshot`.** `_handle_subscribe` already sends a channel's live
presence roster the moment a peer subscribes (§8.10.2); a
`scrollback_snapshot` frame is a sibling addition at the identical call
site, sourced from `netbbs.chat.scrollback.get_scrollback` — the same
already-bounded, origin-policy-filtered function the local UI renders
from. A subscriber accepts the frame only from the channel's current
authenticated origin and only when its `request_id` matches a still-pending
subscribe attempt; unsolicited or late snapshots cannot fill a later join.

**Decision 2 (locked in) — bounded the same way every other snapshot
already is, and rendered once, never durably stored on the subscribing
side.** A received `scrollback_snapshot` is ephemeral and render-only —
exactly the same "Live channel messages are ephemeral node-attested
assertions" principle §8.10.2 already states for ordinary live messages.
Writing it into the subscriber's own `channel_messages` was considered
and rejected: the same content already arrives durably, independently,
through the existing async materialization path, and persisting both
would risk duplicate or conflicting rows for one message with no natural
dedup key across the two paths. Snapshot entries therefore carry the signed
event's existing content ID when one exists, letting the join flow suppress
anything already rendered from local materialized history. A caller sees
only the remaining gap once at subscribe time; the durable copy still lands
separately, on its own schedule, as it already does today.

**Decision 3 (locked in) — issue #164's author-trust rule is enforced at
both nodes.** The origin's `get_scrollback` applies its own policy first.
Each entry also carries the author identity the origin derived from the
accepted signed event (or the origin plus local user ID for origin-local
content), so the subscriber independently suppresses authors it marks
`BLOCKED` or `QUARANTINED`. Origin-local display labels are qualified with
the authenticated origin; they must never resolve as same-named local
accounts on the subscriber.

The revised snapshot attribution contract is real-time protocol v2. The Noise
identity payload declares that application version, and incompatible peers are
rejected during the authenticated handshake before either side advertises a
usable live session. The join flow reports this as an explicit upgrade
requirement rather than folding it into generic transient unavailability.
Frame versions remain an inner defensive boundary.
The subscriber reconstructs every authored display label from the attested
`user@node` identity rather than trusting the wire label. Moderation entries
retain their target label for rendering but are authorless system events, so a
target's trust state cannot suppress audit history the target did not author.

`chat/scrollback.py`'s own module docstring — "the separate, harder
question of a newly-joined Link node needing catch-up scrollback from
peers... stays explicitly deferred to whenever Phase 5 starts" — is
updated alongside this decision: Phase 5 has been active for a while now,
and this issue is that decision, not a further deferral of it.
