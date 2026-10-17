# Issue #85 — inventory/pull-based catch-up and multi-hop relay

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§8.8 now states the complete design: a signed `InventoryRequest` bundle
(not a canonical event, matching `PeerListMessage`'s own precedent), a new
`POST {LINK_PATH_PREFIX}/inventory/{fingerprint}` route whose response
reuses the exact `push_events` raw-event-list wire shape, a responder-side
diff query unioning three sources (self-originated, locally-authored on any
carried board, and peer-received) so it is genuinely multi-hop, and a
nullable `board_id` column added to `link_events` to make that query cheap.
Bounded by the existing `_MAX_EVENTS_PER_REQUEST`/`max_carried_boards`
quotas, not new numbers.

**One necessary correctness fix, not zero protocol changes**, discovered
while implementing: `handle_events`'s board-scoped branches previously
required the wire-level sender to equal the content's own claimed
origin/author, which made a genuinely relayed event structurally
unverifiable (the relay is a different node than the author). Fixed by
resolving each branch's signing key against the content's own claimed
origin/author instead, gated on that fingerprint independently already
being a completed peer — preserving the same "never accept from a stranger"
property while correctly relocating which fingerprint it applies to. See
§8.8's own "real, worth-stating limitation" note for what this does and
does not enable.

Issue #94 subsequently widened responder enumeration to resources absent
from the request, so an empty inventory can discover a wholly novel board,
channel, or file-area catalogue through a carrier. The signed requester
authentication from issue #106 and destination/freshness/replay binding
from issue #124 are now part of that disclosure boundary; §8.8 is the
normative current request format.

Explicitly excludes retention/purging (issue #86, sequenced after this one)
and Link messages (already point-to-point by design, §10, untouched by the
`handle_events` fix above).
