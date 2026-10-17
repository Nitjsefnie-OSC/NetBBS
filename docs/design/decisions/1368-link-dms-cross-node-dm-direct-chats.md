# Issue #1368 — Link DMs: cross-node `/dm` direct chats — decided and built

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§16 issue #270, Decision 4, left a fullscreen direct chat between callers of
two nodes for later: it "needs mutual invite/accept frames and a shared room
spanning two nodes". This builds it.

**Decision 1 — the same feature, wider reach.** `/dm name@TheirBBS` and Who's
online **[I]nvite to chat** on a remote entry behave as a local direct chat
does (§6.3): the waiting screen, the 60-second timeout, C to cancel, the
main-menu interrupt and the one-line notice elsewhere (#843), the outcome
above Who's list (#1124), the pinned row and `/close`.

**Decision 2 — a capability, not a version bump.** `direct_chat_v1` and four
real-time frames: `direct_chat_invite {chat_id, to_user_id, from_user_id,
from_display_label, created_at}`, `direct_chat_answer {chat_id, outcome}`,
`direct_chat_line {chat_id, body, created_at}` and `direct_chat_end {chat_id,
reason}`. An unknown frame type closes the whole real-time session
(`RealtimeFrame.from_json_bytes` raises before the strike path), so a peer that
does not advertise the capability is never sent one; its caller is told the
BBS does not support direct chats yet. A version bump would have broken every
mixed pair's channels and relays as well.

**Decision 3 — refusals do not leak.** The answer is `accepted`, `declined`,
`busy` or `unavailable`. A block, an opt-out from live messages, an unknown
name or an offline caller all read as `unavailable`, which the inviter sees as
the reason-free "can't be reached for live chat right now". The block and
opt-out check is repeated for every line, and a block made mid-chat ends it.

**Decision 4 — each node keeps its own half.** `netbbs.link.realtime_dm`
maps the inviter's `chat_id`, keyed by the peer's authenticated fingerprint,
to a `RemoteDirectRoom` that the direct-chat screen reads in place of a hub
room. No third node can write into it, and no peer names a local hub channel.

**Decision 5 — the inviter's clock decides.** An acceptance arriving after
the inviter cancelled or timed out is answered with `direct_chat_end`, so the
accepting caller sees the chat close at once rather than wait alone.

**Decision 6 — a dropped link is not someone leaving** (§8.10.2). When the
session closes and none comes back within five seconds, an invitation in
flight lapses and an open chat ends with "the connection to name@TheirBBS was
lost".

**Decision 7 — bounds.** Lines of 4,000 bytes and identifiers of 128 bytes,
as for live messages; one pending invitation per target session; at most 16
pending invitations and 16 open chats per peer; five seconds between one
node's invitations to the same local caller (per node, since the inviter's
name is that node's to choose).

**Decision 8 — trust.** The real-time policy is checked on every received
frame and before every send; the identity-change caution shows on both sides;
every remote label and line is sanitized.
