"""
Direct chats between callers of two nodes (issue #1368): `/dm name@TheirBBS`
and Who's online [I]nvite on a remote entry, the cross-node form of the
mutual invite/accept direct chat (design doc §6.3).

Four real-time frames, sent only to a peer advertising
`DIRECT_CHAT_CAPABILITY` -- an older node closes the whole session on an
unknown frame type:

- `direct_chat_invite {chat_id, to_user_id, from_user_id, from_display_label,
  created_at}`, from the inviter's node;
- `direct_chat_answer {chat_id, outcome}`: accepted, declined, busy, or
  unavailable -- a block, an opt-out or an offline caller all read as
  unavailable, so none of them can be probed;
- `direct_chat_line {chat_id, body, created_at}`, either way, once accepted;
- `direct_chat_end {chat_id, reason}`: left, or -- before an answer -- the
  inviter's cancel or timeout.

Each node keeps its own half of the room (`RemoteDirectRoom`), keyed by the
peer's authenticated fingerprint and the inviter's `chat_id`, so no third
node can write into it. The inviter's clock decides: an acceptance arriving
after the inviter gave up is answered with an end. A dropped link is not the
other caller leaving (§8.10.2): when the session closes and no new one comes
within a short grace, the room ends as lost.

Like `netbbs.link.realtime_direct`, this never imports `netbbs.net`; what
it asks of the caller-facing layer arrives as callables.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from netbbs.link.enforcement import LinkPolicyAction, decide_node_action
from netbbs.link.events import DIRECT_CHAT_CAPABILITY, descriptor_has_capability
from netbbs.link.protocol import LinkNode, RealtimeFrame, build_direct_chat_frame
from netbbs.link.transport import LinkRealtimeSession, LinkRealtimeSessionRegistry
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import utc_now_iso

_logger = logging.getLogger(__name__)

#: Bounds on what one peer can make this node hold (AGENTS.md: bound
#: remotely influenced resources).
MAX_PENDING_INVITES_PER_PEER = 16
MAX_ROOMS_PER_PEER = 16
#: How soon one remote caller may invite the same local caller again.
INVITE_COOLDOWN_SECONDS = 5.0
#: How long a dropped session may take to come back before a room is lost.
RECONNECT_GRACE_SECONDS = 5.0
#: Lines held for the local caller's screen; the oldest goes first.
ROOM_QUEUE_LIMIT = 500

LINE = "line"
END = "end"
LOST = "lost"


class DirectChatRefused(Exception):
    """The peer cannot take a direct chat: it does not advertise the
    capability, or no live path to it exists."""


@dataclass
class RemoteDirectRoom:
    """This node's half of a direct chat with a caller on another node.
    `events` yields `(LINE, body)`, `(END, reason)` or `(LOST, None)`;
    after an end or a loss nothing more comes."""

    peer_fingerprint: str
    chat_id: str
    local_user: str
    peer_user: str
    peer_label: str
    _chats: "LinkDirectChats"
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=ROOM_QUEUE_LIMIT))
    closed: bool = False

    def _post(self, kind: str, value: object = None) -> None:
        if self.closed:
            return
        if kind != LINE:
            self.closed = True
        if self.events.full():
            # The caller fell behind: drop the oldest line, never an end.
            try:
                self.events.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.events.put_nowait((kind, value))

    async def send_line(self, body: str) -> bool:
        """Send one line to the other caller; whether it went. A failure
        ends the room as lost."""
        if self.closed:
            return False
        ok = await self._chats._send(self.peer_fingerprint, "direct_chat_line", {
            "chat_id": self.chat_id, "body": body, "created_at": utc_now_iso(),
        })
        if not ok:
            self._chats._forget_room(self)
            self._post(LOST)
        return ok

    async def leave(self) -> None:
        """The local caller left: tell the other side, once."""
        was_open = not self.closed
        self.closed = True
        self._chats._forget_room(self)
        if was_open:
            await self._chats._send(self.peer_fingerprint, "direct_chat_end", {
                "chat_id": self.chat_id, "reason": "left",
            })


@dataclass
class OutgoingChatInvite:
    """An invitation this node's caller sent. `answer` resolves to
    accepted, declined, busy, unavailable or lost; on accepted, `room`
    is set."""

    peer_fingerprint: str
    chat_id: str
    from_user: str
    to_user: str
    peer_label: str
    answer: asyncio.Future
    room: RemoteDirectRoom | None = None


@dataclass
class IncomingChatInvite:
    """An invitation from another node's caller to one of this node's.
    The caller-facing layer offers it and settles it with `accept` or
    `refuse`; `withdrawn` is set if the inviter cancels or gives up first,
    or the link drops."""

    peer_fingerprint: str
    chat_id: str
    from_user: str
    from_label: str
    to_user: str
    _chats: "LinkDirectChats"
    withdrawn: asyncio.Event = field(default_factory=asyncio.Event)
    settled: bool = False

    async def accept(self) -> RemoteDirectRoom | None:
        """Open the room and tell the inviter; `None` if it was withdrawn
        meanwhile."""
        if self.settled or self.withdrawn.is_set():
            return None
        self.settled = True
        self._chats._pending_in.pop((self.peer_fingerprint, self.chat_id), None)
        room = RemoteDirectRoom(
            peer_fingerprint=self.peer_fingerprint, chat_id=self.chat_id, local_user=self.to_user,
            peer_user=self.from_user, peer_label=self.from_label, _chats=self._chats,
        )
        self._chats._rooms[(self.peer_fingerprint, self.chat_id)] = room
        if not await self._chats._send(self.peer_fingerprint, "direct_chat_answer", {
            "chat_id": self.chat_id, "outcome": "accepted",
        }):
            self._chats._forget_room(room)
            room._post(LOST)
        return room

    def drop(self) -> None:
        """Settle without an answer: it lapsed here, and the inviter's own
        timeout tells them."""
        self.settled = True
        self._chats._pending_in.pop((self.peer_fingerprint, self.chat_id), None)

    async def refuse(self, outcome: str) -> None:
        """Answer declined, busy or unavailable."""
        if self.settled:
            return
        self.settled = True
        self._chats._pending_in.pop((self.peer_fingerprint, self.chat_id), None)
        if not self.withdrawn.is_set():
            await self._chats._send(self.peer_fingerprint, "direct_chat_answer", {
                "chat_id": self.chat_id, "outcome": outcome,
            })


class LinkDirectChats:
    """One per node, beside `LiveDirectChat`, whose `ensure_session` finds
    the path to a peer; the `LiveChannelBridge` routes the four frames here
    via `owns_frame`/`handle_frame`.

    `offer` hands an incoming invitation to the caller-facing layer, which
    settles it; `line_allowed(peer_fingerprint, from_user, to_user)` is the
    local block/opt-out check, made for each invitation and each line."""

    def __init__(
        self,
        *,
        link_node: LinkNode,
        lane: DatabaseLane,
        registry: LinkRealtimeSessionRegistry,
        ensure_session: Callable[[str], Awaitable[LinkRealtimeSession]],
        offer: Callable[[IncomingChatInvite], Awaitable[None]] | None = None,
        line_allowed: Callable[[str, str, str], Awaitable[bool]] | None = None,
        reconnect_grace_seconds: float = RECONNECT_GRACE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._node = link_node
        self._lane = lane
        self._registry = registry
        self._ensure_session = ensure_session
        self.offer = offer
        self.line_allowed = line_allowed
        self._grace = reconnect_grace_seconds
        self._clock = clock
        self._pending_out: dict[tuple[str, str], OutgoingChatInvite] = {}
        self._pending_in: dict[tuple[str, str], IncomingChatInvite] = {}
        self._rooms: dict[tuple[str, str], RemoteDirectRoom] = {}
        self._last_invite: dict[tuple[str, str, str], float] = {}
        self._watchers: dict[str, asyncio.Task] = {}

    # -- what the caller-facing layer uses ------------------------------------

    def peer_capable(self, fingerprint: str) -> bool:
        """Whether `fingerprint` advertises direct chats, by its peer record
        or, for a node only ever learned secondhand, its candidate
        descriptor."""
        peer = self._node.peers.get(fingerprint)
        descriptors = [d for d in (
            peer.descriptor if peer is not None else None, self._node.candidate_descriptors.get(fingerprint),
        ) if d is not None]
        return any(descriptor_has_capability(d, DIRECT_CHAT_CAPABILITY) for d in descriptors)

    async def invite(
        self, fingerprint: str, *, from_user: str, from_label: str, to_user: str, peer_label: str,
    ) -> OutgoingChatInvite:
        """Send an invitation; raises `DirectChatRefused` when the peer
        cannot take one."""
        if not self.peer_capable(fingerprint):
            raise DirectChatRefused("capability")
        chat_id = secrets.token_hex(12)
        invite = OutgoingChatInvite(
            peer_fingerprint=fingerprint, chat_id=chat_id, from_user=from_user, to_user=to_user,
            peer_label=peer_label, answer=asyncio.get_running_loop().create_future(),
        )
        self._pending_out[(fingerprint, chat_id)] = invite
        if not await self._send(fingerprint, "direct_chat_invite", {
            "chat_id": chat_id, "to_user_id": to_user, "from_user_id": from_user,
            "from_display_label": from_label, "created_at": utc_now_iso(),
        }):
            self._pending_out.pop((fingerprint, chat_id), None)
            raise DirectChatRefused("unreachable")
        self._watch(fingerprint)
        return invite

    async def withdraw(self, invite: OutgoingChatInvite, reason: str) -> None:
        """The inviter cancelled or timed out before an answer."""
        if self._pending_out.pop((invite.peer_fingerprint, invite.chat_id), None) is None:
            return
        if not invite.answer.done():
            invite.answer.set_result(reason)
        await self._send(invite.peer_fingerprint, "direct_chat_end", {"chat_id": invite.chat_id, "reason": reason})

    # -- frames ---------------------------------------------------------------

    def owns_frame(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> bool:
        return frame.type.startswith("direct_chat_")

    async def handle_frame(self, session: LinkRealtimeSession, frame: RealtimeFrame) -> None:
        fingerprint = session.remote_fingerprint
        allowed = await self._lane.run(
            lambda db: decide_node_action(db, fingerprint, LinkPolicyAction.REALTIME).allowed
        )
        if not allowed:
            return
        payload = frame.payload
        key = (fingerprint, payload["chat_id"])
        try:
            if frame.type == "direct_chat_invite":
                await self._on_invite(fingerprint, payload)
            elif frame.type == "direct_chat_answer":
                await self._on_answer(key, payload["outcome"])
            elif frame.type == "direct_chat_line":
                await self._on_line(key, payload["body"])
            elif frame.type == "direct_chat_end":
                self._on_end(key, payload["reason"])
        except Exception:
            _logger.exception("handling %s from %s failed", frame.type, fingerprint[:12])

    async def _on_invite(self, fingerprint: str, payload: dict) -> None:
        chat_id, from_user, to_user = payload["chat_id"], payload["from_user_id"], payload["to_user_id"]
        if (fingerprint, chat_id) in self._pending_in or (fingerprint, chat_id) in self._rooms:
            return
        now = self._clock()
        cooldown_key = (fingerprint, from_user.casefold(), to_user.casefold())
        too_soon = now - self._last_invite.get(cooldown_key, float("-inf")) < INVITE_COOLDOWN_SECONDS
        crowded = (
            sum(1 for fp, _ in self._pending_in if fp == fingerprint) >= MAX_PENDING_INVITES_PER_PEER
            or sum(1 for fp, _ in self._rooms if fp == fingerprint) >= MAX_ROOMS_PER_PEER
        )
        refused = self.offer is None or too_soon or crowded or (
            self.line_allowed is not None and not await self.line_allowed(fingerprint, from_user, to_user)
        )
        if refused:
            await self._send(fingerprint, "direct_chat_answer", {"chat_id": chat_id, "outcome": "unavailable"})
            return
        self._last_invite[cooldown_key] = now
        if len(self._last_invite) > 4096:
            self._last_invite.clear()
        incoming = IncomingChatInvite(
            peer_fingerprint=fingerprint, chat_id=chat_id, from_user=from_user,
            from_label=payload["from_display_label"], to_user=to_user, _chats=self,
        )
        self._pending_in[(fingerprint, chat_id)] = incoming
        self._watch(fingerprint)
        asyncio.get_running_loop().create_task(self._offer(incoming))

    async def _offer(self, incoming: IncomingChatInvite) -> None:
        try:
            await self.offer(incoming)
        except Exception:
            _logger.exception("offering a direct chat from %s failed", incoming.peer_fingerprint[:12])
            await incoming.refuse("unavailable")

    async def _on_answer(self, key: tuple[str, str], outcome: str) -> None:
        invite = self._pending_out.pop(key, None)
        if invite is None or invite.answer.done():
            if outcome == "accepted":
                # Accepted after this node's caller gave up: the inviter's
                # clock decides, so the other caller is told at once.
                await self._send(key[0], "direct_chat_end", {"chat_id": key[1], "reason": "cancelled"})
            return
        if outcome == "accepted":
            invite.room = RemoteDirectRoom(
                peer_fingerprint=key[0], chat_id=key[1], local_user=invite.from_user, peer_user=invite.to_user,
                peer_label=invite.peer_label, _chats=self,
            )
            self._rooms[key] = invite.room
        invite.answer.set_result(outcome)

    async def _on_line(self, key: tuple[str, str], body: str) -> None:
        room = self._rooms.get(key)
        if room is None or room.closed:
            return
        if self.line_allowed is not None and not await self.line_allowed(key[0], room.peer_user, room.local_user):
            # Blocked mid-chat: the chat ends for both.
            self._forget_room(room)
            room._post(END, "left")
            await self._send(key[0], "direct_chat_end", {"chat_id": key[1], "reason": "left"})
            return
        room._post(LINE, body)

    def _on_end(self, key: tuple[str, str], reason: str) -> None:
        incoming = self._pending_in.pop(key, None)
        if incoming is not None:
            incoming.withdrawn.set()
        room = self._rooms.pop(key, None)
        if room is not None:
            room._post(END, reason)
        invite = self._pending_out.pop(key, None)
        if invite is not None and not invite.answer.done():
            invite.answer.set_result("unavailable")

    # -- plumbing -------------------------------------------------------------

    async def _send(self, fingerprint: str, frame_type: str, payload: dict) -> bool:
        """Send one frame on whatever session is live with `fingerprint` now
        -- never a held one, since a collision can replace it."""
        try:
            session = await self._ensure_session(fingerprint)
            await session.send(build_direct_chat_frame(frame_type, payload))
        except Exception as exc:  # noqa: BLE001 -- LinkTransportError, unreachable, refused: not delivered
            _logger.info("could not send %s to %s: %s", frame_type, fingerprint[:12], exc)
            return False
        return True

    def _forget_room(self, room: RemoteDirectRoom) -> None:
        if self._rooms.get((room.peer_fingerprint, room.chat_id)) is room:
            del self._rooms[(room.peer_fingerprint, room.chat_id)]

    def _holds(self, fingerprint: str) -> bool:
        return any(fp == fingerprint for fp, _ in (*self._pending_out, *self._pending_in, *self._rooms))

    def _watch(self, fingerprint: str) -> None:
        """Watch the link to `fingerprint` while anything here depends on it."""
        task = self._watchers.get(fingerprint)
        if task is None or task.done():
            self._watchers[fingerprint] = asyncio.get_running_loop().create_task(self._watch_link(fingerprint))

    async def _watch_link(self, fingerprint: str) -> None:
        while self._holds(fingerprint):
            session = self._registry.get(fingerprint)
            if session is not None:
                try:
                    await asyncio.wait_for(session.closed.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue  # still open; stop watching once nothing depends on it
            deadline = self._clock() + self._grace
            while self._registry.get(fingerprint) is None and self._clock() < deadline:
                await asyncio.sleep(0.25)
            if self._registry.get(fingerprint) is None:
                self._lose(fingerprint)
                return

    def _lose(self, fingerprint: str) -> None:
        """The link to `fingerprint` is gone: everything waiting on it ends."""
        for key in [k for k in self._pending_out if k[0] == fingerprint]:
            invite = self._pending_out.pop(key)
            if not invite.answer.done():
                invite.answer.set_result(LOST)
        for key in [k for k in self._pending_in if k[0] == fingerprint]:
            self._pending_in.pop(key).withdrawn.set()
        for key in [k for k in self._rooms if k[0] == fingerprint]:
            self._rooms.pop(key)._post(LOST)
