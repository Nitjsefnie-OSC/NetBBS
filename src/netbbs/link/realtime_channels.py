"""
Live linked-channel chat bridge (design doc §8.10.2, issue #148's first
vertical) -- the seam between `netbbs.link.transport`'s Noise session
layer (which knows nothing about channels) and `netbbs.chat`'s local
domain state (which knows nothing about Link).

`LiveChannelBridge` is one instance per running node, shared by every
`LinkRealtimeSession` this node holds in either direction (the same
`on_frame` callback goes to `LinkRealtimeServer`, `dial_realtime_
session`, and `LinkRealtimeConnector` alike). It owns exactly the state
a bare session doesn't know about: which peer sessions are currently
subscribed to which of *this node's own* channels. Inbound frames turn
into the same `netbbs.chat.hub.ChatHub` broadcast a local participant's
own message/join/leave already produces -- a remote live event renders
through the exact same path (`_render_channel_message`) scrollback
replay does, per that renderer's own docstring (GitHub issue #64). This
module never touches `channel_messages`/scrollback itself: a live frame
is ephemeral by design (§8.10), never a substitute for the durable
async `channel_message` event `netbbs.link.channels.queue_channel_
message_if_linked` already queues separately.

Deliberately does not decide *when* to dial a peer on its own --
`ensure_live_subscription` is a plain function a caller (a channel-join
flow) invokes; nothing here runs a background connector loop.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from typing import Awaitable, Callable

from netbbs.chat.channels import Channel
from netbbs.chat.hub import ChatHub
from netbbs.chat.presence import PresenceRegistry
from netbbs.chat.scrollback import ChannelMessage as LocalChannelMessage
from netbbs.chat.scrollback import get_scrollback
from netbbs.link.channels import channel_origin_fingerprint, get_channel_by_channel_id, is_channel_linked
from netbbs.link.enforcement import (
    LinkPolicyAction,
    content_visible_for_subject,
    decide_node_action,
    decide_user_authorship,
    link_content_visible,
    ensure_node_subject,
    event_author,
    node_transport_state,
)
from netbbs.link.events import ChannelMessage as LinkChannelMessage
from netbbs.link.node_identity import NodeIdentity
from netbbs.link.node_profiles import identity_for_fingerprint, link_address_label
from netbbs.link.protocol import (
    LinkNode,
    LinkProtocolError,
    RealtimeProtocolVersionError,
    RealtimeFrame,
    build_channel_message_frame,
    build_node_presence_delta_frame,
    build_node_presence_snapshot_frame,
    build_presence_delta_frame,
    build_presence_snapshot_frame,
    build_scrollback_snapshot_frame,
    build_subscribe_frame,
    new_realtime_message_id,
)
from netbbs.link.trust import TrustState, TrustSubject
from netbbs.link.transport import (
    LinkRealtimeSession,
    LinkRealtimeSessionRegistry,
    LinkTransportError,
    dial_realtime_session,
    dialable_realtime_addresses_for_peer,
)
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import utc_now_iso

# design doc §8.10.1: "Subscriptions, remote presence entries ... are
# bounded per peer and node."
_MAX_SUBSCRIBERS_PER_CHANNEL = 200
_MAX_PRESENCE_SNAPSHOT_ENTRIES = 200

# Issue #194: how many of this node's own most-recent local scrollback
# entries to offer a freshly-subscribing peer, and how much of each
# entry's body to send. Must not exceed protocol.py's own
# _REALTIME_MAX_SCROLLBACK_SNAPSHOT_ENTRIES/_REALTIME_MAX_SCROLLBACK_
# ENTRY_BODY_BYTES ceilings (kept as separate, non-imported constants the
# same way _MAX_PRESENCE_SNAPSHOT_ENTRIES already duplicates rather than
# imports protocol.py's own presence-entry bound) -- that module's
# validator is the actual hard enforcement; this is just how much this
# side chooses to offer, and a mismatch would only ever make an outbound
# snapshot smaller than allowed, never invalid.
_MAX_SCROLLBACK_SNAPSHOT_ENTRIES = 20
_MAX_SCROLLBACK_ENTRY_BODY_BYTES = 400


def _bounded_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cut `text` down to at most `max_bytes` UTF-8 bytes without
    splitting a multi-byte character in two. Issue #194's scrollback
    snapshot bundles many entries into one 16 KiB-bounded frame (unlike a
    single live channel_message), so a body that easily fits on its own
    may still need shortening here -- acceptable because this is a
    "catch-up" glance, not a substitute for the full durable copy already
    on its way through the existing async materialization path."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _snapshot_entries(db: Database, channel: Channel) -> list[dict]:
    """Build origin-attested entries with enough author identity for the
    subscriber to apply its own trust policy. Carried-message identities
    are extracted from the signed event accepted by this node; origin-local
    labels are explicitly qualified so they cannot resolve as subscriber-
    local accounts with the same username."""
    origin_fingerprint = channel_origin_fingerprint(db, channel)
    if origin_fingerprint is None:
        return []
    entries: list[dict] = []
    # Issue #275: an MRC line is unauthenticated text from another
    # network, not this origin's attested content -- exporting it under
    # the origin's fingerprint would apply the subscriber's trust policy
    # to the wrong identity. Filtered *before* the cap, so a chatty MRC
    # room cannot consume the whole snapshot budget with nothing to show.
    eligible = [
        message for message in get_scrollback(db, channel) if message.external_source is None
    ]
    for message in eligible[-_MAX_SCROLLBACK_SNAPSHOT_ENTRIES:]:
        author_node_fingerprint: str | None = None
        author_user_id: str | None = None
        author_label = message.author_label
        content_id = message.link_content_id
        if message.kind not in {"mute", "unmute", "ban", "unban", "kick", "daybreak"}:
            if message.link_content_id is None:
                author_node_fingerprint = origin_fingerprint
                author_user_id = message.author_label
                author_label = f"{message.author_label}@{origin_fingerprint}"
                if message.link_event_json is not None:
                    try:
                        content_id = LinkChannelMessage.from_dict(
                            json.loads(message.link_event_json)
                        ).content_id
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        content_id = None
            else:
                row = db.connection.execute(
                    "SELECT envelope_json FROM link_events WHERE content_id = ?",
                    (message.link_content_id,),
                ).fetchone()
                try:
                    subject = event_author(json.loads(row[0])) if row is not None else None
                except (TypeError, ValueError, json.JSONDecodeError):
                    subject = None
                if subject is None or subject.kind != "user" or subject.opaque_user_id is None:
                    continue
                author_node_fingerprint = subject.node_fingerprint
                author_user_id = subject.opaque_user_id
        body, body_truncated = (
            _bounded_utf8(message.body, _MAX_SCROLLBACK_ENTRY_BODY_BYTES)
            if message.body is not None else (None, False)
        )
        entries.append({
            "kind": message.kind,
            "author_label": author_label,
            "author_node_fingerprint": author_node_fingerprint,
            "author_user_id": author_user_id,
            "content_id": content_id,
            "body": body,
            "body_truncated": body_truncated,
            "created_at": message.created_at,
        })
    return entries


def _accept_scrollback_snapshot(
    db: Database, *, channel_id: str, peer_fingerprint: str, entries: list[dict]
) -> tuple[Channel, list[LocalChannelMessage]]:
    channel = _decide_channel_subscribe_authorization(
        db, channel_id=channel_id, peer_fingerprint=peer_fingerprint
    )
    if channel_origin_fingerprint(db, channel) != peer_fingerprint:
        raise LinkProtocolError("scrollback_snapshot sender is not the channel's current origin")
    messages = []
    for entry in entries:
        authored = entry["author_node_fingerprint"] is not None
        if authored:
            home = node_transport_state(db, entry["author_node_fingerprint"])
            if home in {TrustState.BLOCKED, TrustState.QUARANTINED}:
                continue
            subject = TrustSubject.user(entry["author_node_fingerprint"], entry["author_user_id"])
            if not content_visible_for_subject(db, subject):
                continue
        if authored:
            home_label = identity_for_fingerprint(db, entry["author_node_fingerprint"]).label
            author_label = link_address_label(entry["author_user_id"], home_label)
        else:
            author_label = entry["author_label"]
        messages.append(LocalChannelMessage(
            id=-1, channel_id=channel.id, kind=entry["kind"],
            author_label=author_label,
            author_fingerprint=(entry["author_node_fingerprint"] if authored else None),
            body=entry["body"], created_at=entry["created_at"],
            link_content_id=entry["content_id"], body_truncated=entry["body_truncated"],
        ))
    return channel, messages


#: How many channel lines already shown live a bridge remembers (issue
#: #860), so the signed event arriving later is not shown a second time.
#: Live delivery and its async catch-up are minutes apart at most, so this
#: only needs to outlast one sync interval of chat.
_MAX_SHOWN_LIVE = 1024

ShownKey = tuple[str, str, str, str]


def shown_key(author_node: str, user_id: str, created_at: str, body: str) -> ShownKey:
    """What a channel line *is*, for showing it once (issue #860): its
    author's home node, the author's local user id, when it was written and
    what it says. Derived here from what was received -- never a key the
    sender names -- the same way for a live frame and for the signed event:
    the sending node builds both from one `channel_messages` row
    (`queue_channel_message_if_linked`: `local_user_id` and the frame's
    `user_id` are its `author_label`, `created_at` and `body` the row's).
    A live line is attributed to its session peer, or to the node the
    channel's origin names, so a peer can only ever mark its own callers'
    lines as shown, and an origin only lines it would show anyway."""
    return (author_node, user_id, created_at, body)


def _live_author_allowed(db: Database, node_fingerprint: str, user_id: str, *, relayed: bool) -> bool:
    """This node's own policy for one live line's author (issue #860), the
    same as for their signed `channel_message` (`decide_event_authorship`):
    the caller is not quarantined or blocked -- a probationary caller is
    allowed -- and their node may publish here. For the session's own peer
    that is the real-time check already passed; for a node the origin relays
    for, which this node may never have met, it is the `EVENTS` rule the
    async copy of the same line meets, so the two never disagree.

    Decided before anything is written: a fingerprint the origin names is
    remote input, and an unregistered subject is probationary, which `EVENTS`
    refuses -- so a refused name leaves no trust-subject row behind (review
    of #1028). Registered only once allowed, like any node first seen."""
    if relayed:
        if not decide_node_action(db, node_fingerprint, LinkPolicyAction.EVENTS).allowed:
            return False
        ensure_node_subject(db, node_fingerprint)
    return decide_user_authorship(db, node_fingerprint, user_id).allowed


def _decide_channel_subscribe_authorization(db: Database, *, channel_id: str, peer_fingerprint: str) -> Channel:
    """Design doc §8.10.2: "checks that the channel exists, is linked,
    is locally allowed by trust policy, and is available to the
    subscribing peer" -- called again on every delivered message, not
    only at subscribe time, since a subscription is not a permanent
    grant. Raises `LinkProtocolError` (a bounded strike at the session
    layer, not a hard close -- see `LinkRealtimeSession._reader_loop`)
    for any failure, deliberately without distinguishing which reason
    applies: none of "unknown channel," "not linked," or "not trusted"
    are anything a rejected peer needs to be told apart."""
    channel = get_channel_by_channel_id(db, channel_id)
    if channel is None or not is_channel_linked(db, channel):
        raise LinkProtocolError(f"channel {channel_id!r} is not a linked channel this node carries")
    ensure_node_subject(db, peer_fingerprint)
    if not decide_node_action(db, peer_fingerprint, LinkPolicyAction.REALTIME).allowed:
        raise LinkProtocolError(f"node {peer_fingerprint!r} is not currently allowed real-time traffic")
    return channel


class LiveChannelBridge:
    """See module docstring. `hub`/`lane` are this node's own
    already-running singletons (one `ChatHub`, one `DatabaseLane`) --
    this class adds no storage or broadcast mechanism of its own.

    `presence`/`registry` (issue #164) are this node's own already-
    running `PresenceRegistry`/`LinkRealtimeSessionRegistry` singletons,
    needed for node-wide (not channel-scoped) presence: `presence`
    answers "who's actually online on this node right now" the same way
    the local Who's Online screen already does, and `registry` is what
    lets a broadcast reach every currently-connected peer session, not
    just channel subscribers."""

    def __init__(
        self, *, hub: ChatHub, lane: DatabaseLane, presence: PresenceRegistry, registry: LinkRealtimeSessionRegistry,
        peer_capable: Callable[[str], bool] | None = None,
        presence_capable: Callable[[str], bool] | None = None,
    ) -> None:
        self._hub = hub
        self._lane = lane
        self._presence = presence
        self._registry = registry
        self.bot_link = None
        # Issue #860: whether a peer advertises `CHANNEL_RELAY_CAPABILITY`,
        # the only peers sent a frame's optional fields. None: no peer is.
        self._peer_capable = peer_capable or (lambda fingerprint: False)
        # Issue #1281: whether a peer advertises `PRESENCE_RELAY_CAPABILITY`,
        # the only peers that report their callers up or are sent a presence
        # frame's optional `node_fingerprint`. None: no peer is.
        self._presence_capable = presence_capable or (lambda fingerprint: False)
        # Content IDs of channel lines already shown live here, oldest first,
        # so the signed event that follows is not shown again.
        self._shown_live: OrderedDict[ShownKey, None] = OrderedDict()
        # channel_id -> {peer_fingerprint: session}
        self._subscribers: dict[str, dict[str, LinkRealtimeSession]] = {}
        self._watchers: set[asyncio.Task] = set()
        # peer_fingerprint -> {user_id: display_label} -- the last-known
        # node-wide presence snapshot/delta stream received from that
        # peer. Cleared for a peer the moment its session closes
        # (_untrack_on_close) -- stale presence from a now-disconnected
        # peer must not linger.
        self._remote_node_presence: dict[str, dict[str, str]] = {}
        # channel_id -> {node_fingerprint: {user_id: display_label}} -- who
        # is in a linked channel on other nodes (issue #195's merged live
        # roster, per node since issue #1281). On a subscriber all of it
        # arrives over the session to the channel's origin, which
        # `_channel_roster_via` records so that session's close clears it --
        # one session is shared across every channel from the same origin,
        # so this can't key on session identity alone. On the origin each
        # subscriber reports its own callers, and its bucket goes when it
        # unsubscribes or its session closes. Bounded per channel by
        # `_MAX_PRESENCE_SNAPSHOT_ENTRIES` across all nodes.
        self._channel_rosters: dict[str, dict[str, dict[str, str]]] = {}
        self._channel_roster_via: dict[str, str] = {}
        # Channels whose last local holder has left: an origin's presence
        # still on the wire, sent before it read the unsubscribe, must not
        # bring the roster back (review of #1284). Reopened by the next
        # subscribe (`reopen_presence`) or local interest.
        self._presence_closed: set[str] = set()
        # channel_id -> pending scrollback_snapshot entries (issue #194),
        # a one-time pickup for whichever local caller's subscribe flow is
        # currently waiting on it (netbbs.net.chat_flow._subscribe_live).
        # Popped, not merely read, by pop_channel_scrollback -- design doc
        # §16 Decision 2: "rendered once, never durably stored on the
        # subscribing side. Unlike presence, snapshots are also correlated
        # to the exact subscribe attempt so a late response can never be
        # consumed by a later caller.
        # request_id -> (channel_id, expected origin) and response entries.
        # Correlation prevents a late reply from one completed join attempt
        # from being consumed by a later caller joining the same channel.
        self._pending_scrollback_requests: dict[str, tuple[str, str]] = {}
        self._remote_channel_scrollback: dict[str, list[LocalChannelMessage]] = {}
        # peer_fingerprint -> whether this node has already sent that
        # peer its initial node_presence_snapshot for the *current*
        # session -- track_session can be called more than once per
        # session (existing convention), but the snapshot must only go
        # out once per connection, not once per call.
        self._node_presence_sent: set[str] = set()
        # id(session) -> its one close-watcher task. track_session is
        # called for every presence frame a peer sends (issue #168), so
        # the watcher must be created exactly once per session, not once
        # per call -- a chatty peer must not grow this node's task set.
        self._watched: dict[int, asyncio.Task] = {}
        # channel_id -> opaque holder ids (issue #159): this node's own
        # *subscriber*-side interest in a linked channel it doesn't
        # originate -- the mirror image of `_subscribers` above, which is
        # the *origin*-side "who subscribes to my channels" bookkeeping.
        # Needed because a live subscription to a remote origin is a
        # node-level resource (one `LinkRealtimeSession` per remote
        # fingerprint, shared by every local caller who wants it), but
        # `_chat_loop` used to send `unsubscribe` unconditionally on
        # leaving one channel view -- correct only when exactly one local
        # party ever cared about that channel at a time, wrong the moment
        # a second caller (or a caller's own background subscription
        # alongside another active view) is also relying on the same
        # feed: the first to leave would silently cut off live delivery
        # for everyone else still interested.
        self._local_interest: dict[str, set[int]] = {}
        # Issue #168: frames that are not this bridge's own business --
        # relay rendezvous (`netbbs.link.realtime_relay`) and live direct
        # messages (`netbbs.link.realtime_direct`) -- are routed to the
        # first registered handler that claims them. The bridge stays the
        # single `on_frame` seam every session is constructed with; the
        # other components plug in rather than each needing a session
        # hook of their own.
        self._frame_handlers: list[tuple[
            Callable[[LinkRealtimeSession, RealtimeFrame], bool],
            Callable[[LinkRealtimeSession, RealtimeFrame], Awaitable[None]],
        ]] = []

    def register_frame_handler(
        self,
        owns: Callable[[LinkRealtimeSession, RealtimeFrame], bool],
        handle: Callable[[LinkRealtimeSession, RealtimeFrame], Awaitable[None]],
    ) -> None:
        self._frame_handlers.append((owns, handle))

    def register_local_interest(self, channel_id: str, holder: int) -> bool:
        """Record that `holder` (a caller's own `id(session)`) wants live
        delivery for `channel_id`. Returns whether this is the *first*
        local holder -- informational only, since re-sending `subscribe`
        to an origin that's already subscribed is harmless and idempotent
        (`_handle_subscribe` just re-registers), so callers aren't
        required to gate that frame on this return value the way they
        must gate `unsubscribe` on `release_local_interest`'s."""
        holders = self._local_interest.setdefault(channel_id, set())
        first = not holders
        holders.add(holder)
        self._presence_closed.discard(channel_id)
        return first

    def reopen_presence(self, channel_id: str) -> None:
        """A `subscribe` for `channel_id` is about to go out: the origin's
        answer, which can arrive before the caller registers its interest,
        is welcome again."""
        self._presence_closed.discard(channel_id)

    def has_local_interest(self, channel_id: str) -> bool:
        """Whether some local caller holds a live subscription to
        `channel_id` right now -- the only time `remote_channel_presence`
        is being kept current. The roster is not cleared on unsubscribe
        (only when the peer's session closes), so a reader that wants a
        count, not just the last roster seen, checks this first."""
        return bool(self._local_interest.get(channel_id))

    def release_local_interest(self, channel_id: str, holder: int) -> bool:
        """Record that `holder` no longer wants live delivery for
        `channel_id`. Returns whether `holder` was the *last* local
        holder -- the caller must send `unsubscribe` to the origin only
        when this is `True`; otherwise some other local caller/view is
        still relying on the same feed and unsubscribing would silently
        cut them off. A `holder` that was never registered (e.g. this
        caller never actually got a live session) is a safe no-op,
        returning `False`."""
        holders = self._local_interest.get(channel_id)
        if holders is None or holder not in holders:
            return False
        holders.discard(holder)
        last = not holders
        if last:
            del self._local_interest[channel_id]
            # On a subscriber nothing keeps the roster current any more
            # (issue #1281): the origin stops sending once told this node
            # unsubscribed. On the origin the roster is its subscribers'
            # own reports, which a local holder leaving (a bot, say) does
            # not touch (review of #1284).
            if self._channel_roster_via.pop(channel_id, None) is not None:
                self._channel_rosters.pop(channel_id, None)
            # Only presence *from* an origin is checked against this, so on
            # the origin itself it changes nothing.
            self._presence_closed.add(channel_id)
        return last

    async def track_session(self, session: LinkRealtimeSession) -> None:
        """Spawn the bounded (one per session) watcher that purges
        `session` from every channel's subscriber set once it closes --
        a peer that disconnects without unsubscribing must not linger
        as a phantom subscriber forever. Safe to call more than once for
        the same session (a no-op watcher only ever removes what it
        finds).

        Also sends that peer an initial `node_presence_snapshot` --
        push-on-connect (issue #164), exactly once per session even if
        this is called more than once for it. Best-effort: a session
        that's already gone by the time this runs is handled the same
        way every other outbound send in this class handles a dead
        session, and `_untrack_on_close`'s own watcher (already spawned
        above) will clean up `_node_presence_sent` once `session.closed`
        actually fires."""
        if id(session) not in self._watched:
            watcher = asyncio.get_running_loop().create_task(self._untrack_on_close(session))
            self._watched[id(session)] = watcher
            self._watchers.add(watcher)
            watcher.add_done_callback(self._watchers.discard)
            watcher.add_done_callback(lambda _t, key=id(session): self._watched.pop(key, None))

        if session.remote_fingerprint in self._node_presence_sent:
            return
        self._node_presence_sent.add(session.remote_fingerprint)
        entries = [
            {"user_id": username, "display_label": username}
            for username in sorted(self._presence.online_usernames())
        ][:_MAX_PRESENCE_SNAPSHOT_ENTRIES]
        try:
            await session.send(build_node_presence_snapshot_frame(entries))
        except LinkTransportError:
            pass

    async def _untrack_on_close(self, session: LinkRealtimeSession) -> None:
        await session.closed.wait()
        for channel_id, subscribers in list(self._subscribers.items()):
            if subscribers.pop(session.remote_fingerprint, None) is not None and not subscribers:
                del self._subscribers[channel_id]
        self._remote_node_presence.pop(session.remote_fingerprint, None)
        self._node_presence_sent.discard(session.remote_fingerprint)
        gone = session.remote_fingerprint
        for channel_id, fingerprint in list(self._channel_roster_via.items()):
            if fingerprint == gone:
                del self._channel_roster_via[channel_id]
                self._channel_rosters.pop(channel_id, None)
        # On the origin: that subscriber's callers leave every channel it
        # reported them in, told to the other subscribers, without a notice
        # here -- a dropped link is not anyone leaving (design doc §8.10.2).
        for channel_id in list(self._channel_rosters):
            if channel_id not in self._channel_roster_via:
                await self._drop_node_roster(channel_id, gone, announce=False)
        for request_id, (_, fingerprint) in list(self._pending_scrollback_requests.items()):
            if fingerprint == session.remote_fingerprint:
                self.finish_scrollback_request(request_id)
        if self.bot_link is not None:
            await self.bot_link.on_disconnect(session)

    async def close(self) -> None:
        if self._watchers:
            await asyncio.gather(*self._watchers, return_exceptions=True)

    async def on_frame(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        if frame.type == "subscribe":
            await self._handle_subscribe(session, frame)
        elif frame.type == "unsubscribe":
            await self._handle_unsubscribe(session, frame)
        elif frame.type == "channel_message":
            await self._handle_channel_message(session, frame)
        elif frame.type == "scrollback_snapshot":
            await self._handle_scrollback_snapshot(session, frame)
        elif frame.type == "presence_delta":
            await self._handle_presence_delta(session, frame)
        elif frame.type == "presence_snapshot":
            await self._handle_presence_snapshot(session, frame)
        elif frame.type == "node_presence_snapshot":
            await self._handle_node_presence_snapshot(session, frame)
        elif frame.type == "node_presence_delta":
            await self._handle_node_presence_delta(session, frame)
        else:
            for owns, handle in self._frame_handlers:
                if owns(session, frame):
                    await handle(session, frame)
                    return
            # "error": nothing actionable locally yet from a peer-reported
            # rejection of a frame this node sent. A relay/direct frame
            # with no handler registered (a node that doesn't run those
            # components) is ignored the same way -- never a strike.

    async def _handle_subscribe(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        channel_id = frame.payload["channel_id"]
        channel = await self._lane.run(
            _decide_channel_subscribe_authorization, channel_id=channel_id,
            peer_fingerprint=session.remote_fingerprint,
        )
        subscribers = self._subscribers.setdefault(channel_id, {})
        if session.remote_fingerprint not in subscribers and len(subscribers) >= _MAX_SUBSCRIBERS_PER_CHANNEL:
            raise LinkProtocolError(f"channel {channel_id!r} is already at its live-subscriber limit")
        subscribers[session.remote_fingerprint] = session
        await self.track_session(session)

        seen_usernames: set[str] = set()
        entries = []
        for participant in self._hub.participant_ids(channel.name):
            if participant.username in seen_usernames:
                continue
            seen_usernames.add(participant.username)
            entries.append({"user_id": participant.username, "display_label": participant.username})
        if self._presence_capable(session.remote_fingerprint):
            # Issue #1281: every other node's callers the origin knows of,
            # named by their node; not the subscriber's own.
            for node, users in self._channel_rosters.get(channel_id, {}).items():
                if node == session.remote_fingerprint:
                    continue
                entries.extend(
                    {"user_id": user_id, "display_label": label, "node_fingerprint": node}
                    for user_id, label in users.items()
                )
        await session.send(
            build_presence_snapshot_frame(channel_id, entries[:_MAX_PRESENCE_SNAPSHOT_ENTRIES])
        )
        await self._send_scrollback_snapshot(session, channel, request_id=frame.message_id)
        if self.bot_link is not None:
            await self.bot_link.on_subscribe(session, channel)

    async def _send_scrollback_snapshot(
        self, session: LinkRealtimeSession, channel: Channel, *, request_id: str
    ) -> None:
        """Issue #194 Decision 1: a sibling frame sent right alongside the
        presence_snapshot above, at the same call site, sourced from this
        node's own already-bounded, origin-policy-filtered local
        scrollback (`netbbs.chat.scrollback.get_scrollback`). Entries also
        preserve author identity for the subscriber's independent policy
        check. Best-
        effort and silent on failure: an empty local scrollback, a
        validation failure (this node's own bound choices above are meant
        to prevent that, but nothing here depends on them being perfect),
        or a transport failure must never surface as an error to the
        subscribing peer or block anything else `_handle_subscribe`
        already did -- the existing async catch-up path is still there
        regardless (design doc §16)."""
        entries = await self._lane.run(_snapshot_entries, channel)
        if not entries:
            return
        # JSON escaping can make valid per-field values exceed the frame
        # ceiling. Drop oldest catch-up entries until the fully serialized
        # frame fits; never enqueue a frame that will later kill the session.
        frame = None
        while entries:
            try:
                frame = build_scrollback_snapshot_frame(
                    channel.channel_id, request_id, entries
                )
                break
            except LinkProtocolError:
                entries.pop(0)
        if frame is None:
            return
        try:
            await session.send(frame)
        except LinkTransportError:
            pass

    async def _handle_scrollback_snapshot(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        channel, messages = await self._lane.run(
            _accept_scrollback_snapshot, channel_id=frame.payload["channel_id"],
            peer_fingerprint=session.remote_fingerprint, entries=frame.payload["entries"],
        )
        request_id = frame.payload["request_id"]
        if self._pending_scrollback_requests.get(request_id) != (
            channel.channel_id, session.remote_fingerprint
        ):
            return
        self._remote_channel_scrollback[request_id] = messages

    def begin_scrollback_request(self, channel_id: str, origin_fingerprint: str) -> str:
        request_id = new_realtime_message_id()
        self._pending_scrollback_requests[request_id] = (channel_id, origin_fingerprint)
        return request_id

    def finish_scrollback_request(self, request_id: str) -> None:
        self._pending_scrollback_requests.pop(request_id, None)
        self._remote_channel_scrollback.pop(request_id, None)

    def pop_channel_scrollback(self, channel_id: str, request_id: str) -> list[LocalChannelMessage]:
        """One-time pickup for a just-received scrollback_snapshot (issue
        #194) -- pops and clears rather than a plain get, matching design
        doc §16 Decision 2's "rendered once, never durably stored on the
        subscribing side." A caller (`netbbs.net.chat_flow._subscribe_
        live`) polls this briefly after subscribing; empty means either
        nothing has arrived yet, it was already popped, or this node
        doesn't hold a live subscription for `channel_id` at all."""
        pending = self._pending_scrollback_requests.get(request_id)
        if pending is None or pending[0] != channel_id:
            return []
        return self._remote_channel_scrollback.pop(request_id, [])

    async def _handle_unsubscribe(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        channel_id = frame.payload["channel_id"]
        subscribers = self._subscribers.get(channel_id)
        if subscribers is None:
            return
        if subscribers.pop(session.remote_fingerprint, None) is not None and channel_id not in self._channel_roster_via:
            # On the origin (issue #1281): its last caller here left; the
            # leave it sends after unsubscribing would come too late to
            # count. On a subscriber the roster is the origin's word, which
            # a third node unsubscribing here cannot change (review of #1284).
            await self._drop_node_roster(channel_id, session.remote_fingerprint, announce=True)
        if not subscribers:
            del self._subscribers[channel_id]

    def _remember_shown(self, key: ShownKey) -> bool:
        """Record a line as shown here; False if it already was."""
        if key in self._shown_live:
            return False
        self._shown_live[key] = None
        while len(self._shown_live) > _MAX_SHOWN_LIVE:
            self._shown_live.popitem(last=False)
        return True

    async def _handle_channel_message(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        payload = frame.payload
        channel = await self._lane.run(
            _decide_channel_subscribe_authorization, channel_id=payload["channel_id"],
            peer_fingerprint=session.remote_fingerprint,
        )
        own = self._registry.own_fingerprint
        origin = await self._lane.run(channel_origin_fingerprint, channel)
        # Issue #860: a line can reach this node live three ways -- from the
        # channel's origin about its own caller, from a subscriber about its
        # caller (this node being the origin), or relayed by the origin about
        # a third node's caller. Only the origin may speak for another node.
        author_node = payload.get("author_node_fingerprint", session.remote_fingerprint)
        if author_node != session.remote_fingerprint and session.remote_fingerprint != origin:
            raise LinkProtocolError(
                f"node {session.remote_fingerprint!r} relayed a line in channel "
                f"{payload['channel_id']!r}, which it does not originate"
            )
        if author_node == own:
            return  # This node's own caller's line, echoed back.
        if not await self._lane.run(
            _live_author_allowed, author_node, payload["user_id"],
            relayed=author_node != session.remote_fingerprint,
        ):
            return  # Refused by this node's policy: not shown, and not passed on.
        # A `content_id` an older sender of this capability adds is accepted
        # and ignored: it is unverified, so nothing is keyed on it.
        if not self._remember_shown(
            shown_key(author_node, payload["user_id"], payload["created_at"], payload["body"])
        ):
            return
        node_label = (await self._lane.run(identity_for_fingerprint, author_node)).label
        message = LocalChannelMessage(
            id=-1, channel_id=channel.id, kind="message",
            author_label=link_address_label(payload["user_id"], node_label),
            # The authenticated Noise peer (or, relayed, the node the origin
            # names) is the technical identity behind this ephemeral
            # assertion. Keep it for collision warnings while continuing to
            # render the friendly label by default.
            author_fingerprint=author_node,
            body=payload["body"], created_at=payload["created_at"],
        )
        await self._hub.broadcast(channel.name, message)
        if origin == own:
            await self._relay_to_subscribers(channel, frame, author_node=author_node)
        if self.bot_link is not None:
            await self.bot_link.runtime.observe(channel, message, caller={
                "id": f"link:{author_node}:{payload['user_id']}", "name": message.author_label,
            })

    async def _relay_to_subscribers(
        self, channel: Channel, frame: RealtimeFrame, *, author_node: str
    ) -> None:
        """The origin passes a subscriber's live line on to its other live
        subscribers (issue #860), so a Linked channel is one room live, not
        only after each subscriber's next sync. Only to a peer advertising
        `CHANNEL_RELAY_CAPABILITY`: without `author_node_fingerprint` the line
        would read as the origin's own caller, so an older subscriber gets it
        through async catch-up instead."""
        payload = frame.payload
        relayed = build_channel_message_frame(
            payload["channel_id"], payload["user_id"], payload["display_label"],
            payload["body"], payload["created_at"], author_node_fingerprint=author_node,
        )
        for session in await self._live_subscribers(channel):
            if session.remote_fingerprint == author_node or not self._peer_capable(session.remote_fingerprint):
                continue
            try:
                await session.send(relayed)
            except LinkTransportError:
                pass

    async def on_carried_message_materialized(
        self, content_id: str, payload: dict, message: LocalChannelMessage
    ) -> None:
        """Show a `channel_message` that arrived by async sync to the
        callers already in its channel (issue #860), unless it was shown
        live already. `payload` is the verified signed event's payload.
        `LinkNode.on_channel_message_materialized` points here. Hidden
        content (a quarantined or blocked author) is not shown, the same as
        in scrollback."""
        author = payload["author"]
        if not self._remember_shown(shown_key(
            author["home_node_fingerprint"], author["local_user_id"], payload["created_at"], payload["body"],
        )):
            return
        channel = await self._lane.run(get_channel_by_channel_id, payload["channel_id"])
        if channel is None or not await self._lane.run(link_content_visible, content_id):
            return
        await self._hub.broadcast(channel.name, message)

    async def _presence_sender_role(
        self, session: LinkRealtimeSession, channel: Channel, nodes: set[str],
    ) -> str | None:
        """Who may say what about a linked channel's presence (issue
        #1281), for a report naming callers of `nodes`. `"origin"`: the
        channel's origin, about its own callers or, naming the node, a
        subscriber's. `"subscriber"`: this node is the origin and a current
        subscriber reports its own callers. `None`: a report to drop quietly
        -- a subscriber's leave that crossed its own unsubscribe. Anything
        else is a strike: only the origin speaks for another node."""
        sender = session.remote_fingerprint
        origin = await self._lane.run(channel_origin_fingerprint, channel)
        if origin == self._registry.own_fingerprint:
            if nodes - {sender}:
                raise LinkProtocolError(
                    f"node {sender!r} reported another node's caller in channel {channel.channel_id!r}"
                )
            return "subscriber" if sender in self._subscribers.get(channel.channel_id, {}) else None
        if sender != origin:
            raise LinkProtocolError(
                f"node {sender!r} sent presence for channel {channel.channel_id!r}, which it does not originate"
            )
        return "origin"

    async def _presence_allowed(self, sender: str, node: str, user_id: str) -> bool:
        """This node's own policy for a remote caller in its roster, the
        same as for their live line (`_live_author_allowed`, issue #860):
        a blocked or quarantined caller, or one of a node not allowed to
        publish here, is not listed."""
        return await self._lane.run(_live_author_allowed, node, user_id, relayed=node != sender)

    def _roster_room(self, channel_id: str) -> int:
        return _MAX_PRESENCE_SNAPSHOT_ENTRIES - sum(
            len(users) for users in self._channel_rosters.get(channel_id, {}).values()
        )

    async def _announce_presence(
        self, channel: Channel, node: str, user_id: str, kind: str, *, relay_from: str | None,
    ) -> None:
        """A remote caller joined or left: the notice to this node's callers,
        the bots that watch, and -- on the origin, `relay_from` being the
        subscriber that reported it -- every other subscriber that takes it."""
        node_label = (await self._lane.run(identity_for_fingerprint, node)).label
        message = LocalChannelMessage(
            id=-1, channel_id=channel.id, kind=kind,
            author_label=link_address_label(user_id, node_label),
            author_fingerprint=node, body=None, created_at=utc_now_iso(),
        )
        await self._hub.broadcast(channel.name, message)
        if self.bot_link is not None:
            await self.bot_link.runtime.observe(channel, message, caller={
                "id": f"link:{node}:{user_id}", "name": message.author_label,
            })
        if relay_from is not None:
            await self._relay_presence(channel, node, user_id, kind)

    async def _relay_presence(self, channel: Channel, node: str, user_id: str, kind: str) -> None:
        """The origin passes a subscriber's caller joining or leaving on to
        its other live subscribers advertising `PRESENCE_RELAY_CAPABILITY`
        (issue #1281), naming the node; an older subscriber would read the
        caller as the origin's own, so it is not told."""
        frame = build_presence_delta_frame(channel.channel_id, kind, user_id, user_id, node_fingerprint=node)
        for session in await self._live_subscribers(channel):
            if session.remote_fingerprint == node or not self._presence_capable(session.remote_fingerprint):
                continue
            try:
                await session.send(frame)
            except LinkTransportError:
                pass

    async def _drop_node_roster(self, channel_id: str, node: str, *, announce: bool) -> None:
        """On the origin: `node` stopped reporting `channel_id` (it
        unsubscribed, or its session closed). Its callers leave the roster
        and every other subscriber is told; with `announce`, this node's
        callers see them leave too."""
        users = self._channel_rosters.get(channel_id, {}).pop(node, None)
        if not self._channel_rosters.get(channel_id, True):
            del self._channel_rosters[channel_id]
        if not users:
            return
        channel = await self._lane.run(get_channel_by_channel_id, channel_id)
        if channel is None:
            return
        for user_id in users:
            if announce:
                await self._announce_presence(channel, node, user_id, "leave", relay_from=node)
            else:
                await self._relay_presence(channel, node, user_id, "leave")

    async def _handle_presence_delta(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        payload = frame.payload
        channel = await self._lane.run(
            _decide_channel_subscribe_authorization, channel_id=payload["channel_id"],
            peer_fingerprint=session.remote_fingerprint,
        )
        sender = session.remote_fingerprint
        node = payload.get("node_fingerprint", sender)
        role = await self._presence_sender_role(session, channel, {node})
        if role is None or node == self._registry.own_fingerprint:
            return  # A crossed leave, or this node's own caller echoed back.
        if role == "origin" and channel.channel_id in self._presence_closed:
            return  # Sent before the origin read this node's unsubscribe.
        user_id = payload["user_id"]
        kind = "join" if payload["change"] == "join" else "leave"
        if role == "origin":
            self._channel_roster_via[channel.channel_id] = sender
        rosters = self._channel_rosters.get(channel.channel_id, {})
        if kind == "join":
            if user_id in rosters.get(node, {}) or not await self._presence_allowed(sender, node, user_id):
                return
            if self._roster_room(channel.channel_id) <= 0:
                return  # bounded -- silently dropped, not a protocol violation to strike over
            self._channel_rosters.setdefault(channel.channel_id, {}).setdefault(node, {})[user_id] = (
                payload["display_label"]
            )
        else:
            users = rosters.get(node)
            if users is None or users.pop(user_id, None) is None:
                return
            if not users:
                del rosters[node]
            if not rosters:
                self._channel_rosters.pop(channel.channel_id, None)
        await self._announce_presence(
            channel, node, user_id, kind, relay_from=sender if role == "subscriber" else None,
        )

    async def _handle_presence_snapshot(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        channel = await self._lane.run(
            _decide_channel_subscribe_authorization, channel_id=frame.payload["channel_id"],
            peer_fingerprint=session.remote_fingerprint,
        )
        sender = session.remote_fingerprint
        own = self._registry.own_fingerprint
        entries = frame.payload["entries"][:_MAX_PRESENCE_SNAPSHOT_ENTRIES]
        nodes = {entry.get("node_fingerprint", sender) for entry in entries} or {sender}
        role = await self._presence_sender_role(session, channel, nodes)
        if role is None or (role == "origin" and channel.channel_id in self._presence_closed):
            return
        fresh: dict[str, dict[str, str]] = {}
        for entry in entries:
            node = entry.get("node_fingerprint", sender)
            if node == own or not await self._presence_allowed(sender, node, entry["user_id"]):
                continue
            fresh.setdefault(node, {})[entry["user_id"]] = entry["display_label"]
        if role == "origin":
            # Issue #195: the merged live roster, replaced outright -- a
            # fresh subscribe supersedes whatever was tracked before. No
            # notices: these callers were already there.
            self._channel_rosters[channel.channel_id] = fresh
            self._channel_roster_via[channel.channel_id] = sender
            return
        # Issue #1281: a subscriber's own callers, sent as it subscribes.
        # What changed since its last word is announced and relayed.
        mine = fresh.get(sender, {})
        rosters = self._channel_rosters.setdefault(channel.channel_id, {})
        before = rosters.get(sender, {})
        others = _MAX_PRESENCE_SNAPSHOT_ENTRIES - sum(
            len(users) for node, users in rosters.items() if node != sender
        )
        kept = dict(list(mine.items())[:max(0, others)])
        if kept:
            rosters[sender] = kept
        else:
            rosters.pop(sender, None)
        for user_id in before.keys() - kept.keys():
            await self._announce_presence(channel, sender, user_id, "leave", relay_from=sender)
        for user_id in kept.keys() - before.keys():
            await self._announce_presence(channel, sender, user_id, "join", relay_from=sender)

    async def _node_realtime_allowed(self, fingerprint: str) -> bool:
        """Design doc §8.10.2's "checked again at message delivery"
        principle, extended to node-wide presence: a session can outlive
        the peer's trust degrading below `ESTABLISHED` (it isn't force-
        closed the instant that happens), so an incoming/outgoing
        node-presence frame re-checks fresh rather than trusting that
        the session's continued existence still implies continued
        trust."""
        return await self._lane.run(
            lambda db: decide_node_action(db, fingerprint, LinkPolicyAction.REALTIME).allowed
        )

    async def _handle_node_presence_snapshot(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        if not await self._node_realtime_allowed(session.remote_fingerprint):
            return
        # Issue #168: a session that arrives for a direct message or a
        # relay anchor never subscribes to a channel, so this is the first
        # (and only) place the receiving side learns it exists. Tracking
        # here is what spawns the close-watcher that clears this presence
        # again, and what sends *our* snapshot back so the peer's own
        # "is that user online there" check has an answer. Idempotent.
        await self.track_session(session)
        entries = frame.payload["entries"][:_MAX_PRESENCE_SNAPSHOT_ENTRIES]
        self._remote_node_presence[session.remote_fingerprint] = {
            entry["user_id"]: entry["display_label"] for entry in entries
        }

    async def _handle_node_presence_delta(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        if not await self._node_realtime_allowed(session.remote_fingerprint):
            return
        await self.track_session(session)
        online = self._remote_node_presence.setdefault(session.remote_fingerprint, {})
        user_id = frame.payload["user_id"]
        if frame.payload["change"] == "join":
            if user_id not in online and len(online) >= _MAX_PRESENCE_SNAPSHOT_ENTRIES:
                return  # bounded -- silently dropped, not a protocol violation to strike over
            online[user_id] = frame.payload["display_label"]
        else:
            online.pop(user_id, None)

    def remote_node_presence(self) -> dict[str, dict[str, str]]:
        """`{peer_fingerprint: {user_id: display_label}}` -- the
        last-known node-wide presence for every peer this node currently
        holds a live session with. A UI caller (Who's Online, issue
        #164) reads this directly; nothing here renders it."""
        return {fingerprint: dict(entries) for fingerprint, entries in self._remote_node_presence.items()}

    def remote_channel_roster(self, channel_id: str) -> list[tuple[str, str, str]]:
        """`(node_fingerprint, user_id, display_label)` for everyone this
        node knows to be in `channel_id` on another node (issues #195,
        #1281): on a subscriber, the origin's callers and the other
        subscribers' it relays; on the origin, each subscriber's own. A UI
        caller (`/who`, `/names`, `/list`) reads this directly; nothing here
        renders it."""
        return sorted(
            (node, user_id, label)
            for node, users in self._channel_rosters.get(channel_id, {}).items()
            for user_id, label in users.items()
        )

    def remote_channel_presence(self, channel_id: str) -> dict[str, str]:
        """`{user_id: display_label}` of `remote_channel_roster`, every
        node's callers merged -- for a caller that does not need to know
        which node each is on."""
        return {user_id: label for _node, user_id, label in self.remote_channel_roster(channel_id)}

    def remote_channel_origin_fingerprint(self, channel_id: str) -> str | None:
        """On a subscriber, the origin whose session the roster for
        `channel_id` arrives over; `None` when there is none (not
        subscribed live, or this node is the origin)."""
        return self._channel_roster_via.get(channel_id)

    async def _live_subscribers(self, channel: Channel) -> list[LinkRealtimeSession]:
        """Currently-registered subscriber sessions for `channel`,
        filtered by a fresh trust re-check (design doc §8.10.2:
        "Authorization is checked ... again at message delivery" --
        a subscription from before a peer was quarantined must stop
        receiving pushes, not just future subscribe attempts). A peer
        that no longer passes is dropped from the subscriber set
        outright, not merely skipped this once."""
        subscribers = self._subscribers.get(channel.channel_id)
        if not subscribers:
            return []
        live: list[LinkRealtimeSession] = []
        for fingerprint, session in list(subscribers.items()):
            allowed = await self._lane.run(
                lambda db: decide_node_action(db, fingerprint, LinkPolicyAction.REALTIME).allowed
            )
            if allowed:
                live.append(session)
            else:
                subscribers.pop(fingerprint, None)
        if not subscribers:
            del self._subscribers[channel.channel_id]
        return live

    async def broadcast_local_message_live(self, channel: Channel, message: LocalChannelMessage) -> None:
        """Push a just-locally-authored channel message out live -- the
        outbound half of `_handle_channel_message`. On the channel's origin,
        to every currently live-subscribed peer session; on a subscriber, up
        to the origin over the session it already has (issue #860), which
        shows it to its callers and relays it to its other subscribers. One
        slow/dead peer session degrades to just that session closing
        (`LinkRealtimeSession.send` already handles a full queue) and must
        never block delivery to anyone else or to the local caller who just
        sent the message."""
        sessions = await self._live_subscribers(channel)
        origin = await self._lane.run(channel_origin_fingerprint, channel)
        if origin is not None and origin != self._registry.own_fingerprint:
            upstream = self._registry.get(origin)
            if upstream is not None and await self._lane.run(
                lambda db: decide_node_action(db, origin, LinkPolicyAction.REALTIME).allowed
            ):
                sessions.append(upstream)
        if not sessions:
            return
        # This node's own line, should its signed event ever come back by sync.
        self._remember_shown(shown_key(
            self._registry.own_fingerprint, message.author_label, message.created_at, message.body or "",
        ))
        frame = build_channel_message_frame(
            channel.channel_id, message.author_label, message.author_label,
            message.body or "", message.created_at,
        )
        for session in sessions:
            try:
                await session.send(frame)
            except LinkTransportError:
                pass

    async def broadcast_local_presence_live(self, channel: Channel, *, change: str, username: str) -> None:
        """A local caller joined or left `channel` (called after `hub.leave`
        for a leave): to its live subscribers on the origin, and -- issue
        #1281 -- up to the channel's origin from a subscriber, if the origin
        takes it. A leave while another session of the same account is
        still in the channel is no leave."""
        if change == "leave" and self._hub.participants_for_username(channel.name, username):
            return
        sessions = await self._live_subscribers(channel)
        upstream = await self._upstream_presence_session(channel)
        if upstream is not None:
            sessions.append(upstream)
        if not sessions:
            return
        frame = build_presence_delta_frame(channel.channel_id, change, username, username)
        for session in sessions:
            try:
                await session.send(frame)
            except LinkTransportError:
                pass

    async def _upstream_presence_session(self, channel: Channel) -> LinkRealtimeSession | None:
        """The live session to `channel`'s origin when this node is a
        subscriber and the origin takes its callers' presence (issue #1281)."""
        origin = await self._lane.run(channel_origin_fingerprint, channel)
        if origin is None or origin == self._registry.own_fingerprint or not self._presence_capable(origin):
            return None
        session = self._registry.get(origin)
        if session is None or not await self._lane.run(
            lambda db: decide_node_action(db, origin, LinkPolicyAction.REALTIME).allowed
        ):
            return None
        return session

    async def send_local_roster_upstream(self, channel: Channel) -> None:
        """Right after subscribing, tell the origin who is in `channel` here
        (issue #1281): a caller who joined before the live session existed
        sent a join that went nowhere."""
        session = await self._upstream_presence_session(channel)
        if session is None:
            return
        seen: set[str] = set()
        entries = []
        for participant in self._hub.participant_ids(channel.name):
            if participant.username not in seen:
                seen.add(participant.username)
                entries.append({"user_id": participant.username, "display_label": participant.username})
        try:
            await session.send(build_presence_snapshot_frame(channel.channel_id, entries[:_MAX_PRESENCE_SNAPSHOT_ENTRIES]))
        except LinkTransportError:
            pass

    async def _live_node_wide_sessions(self) -> list[LinkRealtimeSession]:
        """Every currently-connected session whose peer still passes a
        fresh `REALTIME` trust check -- the node-wide counterpart to
        `_live_subscribers`, which is inherently channel-scoped and so
        has no `_subscribers` entry for node-wide traffic to re-validate
        against. `registry` (not `_subscribers`) is the source of truth
        for "every peer this node currently holds a live session with";
        a peer that no longer passes is simply skipped for this
        broadcast, not force-disconnected -- the same session may still
        be legitimately open for other reasons the caller doesn't get
        to unilaterally tear down from here."""
        sessions = self._registry.all_sessions()
        if not sessions:
            return []
        live: list[LinkRealtimeSession] = []
        for session in sessions:
            if await self._node_realtime_allowed(session.remote_fingerprint):
                live.append(session)
        return live

    async def broadcast_node_presence_live(self, *, change: str, username: str) -> None:
        """Push a local login/logout out to every currently-connected
        peer, node-wide -- the outbound half of
        `_handle_node_presence_delta`. Call this from the same place
        `netbbs.chat.presence.PresenceRegistry.enter`/`leave` already
        fires (issue #164), not from a channel join/leave -- node-wide
        presence answers "who's online on this node, period," the same
        question local Who's Online already answers, not "who's watching
        this channel."""
        sessions = await self._live_node_wide_sessions()
        if not sessions:
            return
        frame = build_node_presence_delta_frame(change, username, username)
        for session in sessions:
            try:
                await session.send(frame)
            except LinkTransportError:
                pass


def remote_origin_fingerprint(db: Database, channel: Channel, own_fingerprint: str) -> str | None:
    """The fingerprint of `channel`'s origin node, when that is somebody
    else -- otherwise `None`.

    "Somebody else" folds together the two cases that mean there is
    nothing to dial: a channel that is not Linked at all, and a channel
    this node originated itself. Both are ordinary states, not failures,
    which is the distinction `ensure_live_subscription`'s own `None`
    return cannot carry -- it also means "Linked, but the origin would
    not answer just now". A caller that shows the caller a notice needs
    those apart; see `netbbs.net.chat_flow`, where everyone entering a
    purely local channel was told the real-time link to its origin was
    down.
    """
    origin_fingerprint = channel_origin_fingerprint(db, channel)
    if origin_fingerprint is None or origin_fingerprint == own_fingerprint:
        return None
    return origin_fingerprint


def has_remote_origin(db: Database, channel: Channel, own_fingerprint: str) -> bool:
    """`remote_origin_fingerprint` as the yes/no a caller deciding whether
    to say anything needs, so the question is asked in one place."""
    return remote_origin_fingerprint(db, channel, own_fingerprint) is not None


async def ensure_live_subscription(
    *,
    channel: Channel,
    node_identity: NodeIdentity,
    link_node: LinkNode,
    lane: DatabaseLane,
    registry: LinkRealtimeSessionRegistry,
    bridge: LiveChannelBridge,
    dial_timeout_seconds: float = 10.0,
) -> tuple[LinkRealtimeSession, str] | None:
    """
    If `channel` is Linked, ensure this node holds (or can establish) a
    live session to its origin node and has sent it a request-correlated
    `subscribe` --
    best-effort except for an authenticated protocol-version mismatch,
    which raises `RealtimeProtocolVersionError` so callers can distinguish
    an upgrade requirement from transient unavailability. A caller who can't get live delivery
    still has the existing async catch-up path (design doc §8.10.2:
    "the caller sees that live traffic may have been missed"), so
    degrading silently to `None` is the correct signal here, not a
    caller-visible error.

    Reuses an already-live session to the origin from `registry` if one
    exists (this node may already be connected to it for another
    channel); otherwise dials the origin's advertised real-time
    address(es) in order, first success wins.

    Takes `lane`, never a raw `Database` -- an interactive caller (a
    channel-join flow) must never touch a `sqlite3.Connection` directly
    off the event loop (see `netbbs.storage.execution.DatabaseLane`'s
    own docstring); the one synchronous read this needs (`channel_
    origin_fingerprint`) is dispatched through it like every other
    business-logic call from `netbbs.net`.
    """
    origin_fingerprint = await lane.run(
        remote_origin_fingerprint, channel, node_identity.fingerprint
    )
    if origin_fingerprint is None:
        return None  # not linked, or this node is the origin -- nothing to dial
    session = registry.get(origin_fingerprint)
    if session is None:
        for host, port in dialable_realtime_addresses_for_peer(link_node, origin_fingerprint):
            try:
                session = await asyncio.wait_for(
                    dial_realtime_session(
                        host, port, node_identity, on_frame=bridge.on_frame, registry=registry,
                        lane=lane, enforce_trust_policy=True, expected_fingerprint=origin_fingerprint,
                    ),
                    timeout=dial_timeout_seconds,
                )
            except RealtimeProtocolVersionError:
                raise
            except Exception:
                continue
            await bridge.track_session(session)
            break
        else:
            return None
    request_id = bridge.begin_scrollback_request(channel.channel_id, origin_fingerprint)
    bridge.reopen_presence(channel.channel_id)
    try:
        await session.send(build_subscribe_frame(channel.channel_id, message_id=request_id))
    except LinkTransportError:
        bridge.finish_scrollback_request(request_id)
        return None
    # Issue #1281: frames on one session arrive in order, so the origin has
    # registered this subscription by the time it reads who is here.
    await bridge.send_local_roster_upstream(channel)
    return session, request_id
