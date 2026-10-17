"""
The caller-facing half of direct chats with callers of other nodes (issue
#1368): `/dm name@TheirBBS`, the inviter's waiting screen and outcomes, the
receiving side's fan-out and main-menu prompt, and the direct-chat screen
with a remote room -- against fakes of `netbbs.link.realtime_dm`'s objects;
`tests/test_link_direct_chats.py` covers those over real sessions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from netbbs.auth.users import create_user
from netbbs.chat import ChatHub, DirectChatInvites, PresenceRegistry
from netbbs.chat.direct_invites import RemotePeer
from netbbs.link.realtime_dm import END, LINE
from netbbs.net import chat_flow, link_direct, main_menu
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from tests.test_direct_chat_flow import FakeSession

FP = "f" * 64


def _text(session) -> str:
    return "".join(session.written)


@dataclass
class _Room:
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    sent: list = field(default_factory=list)
    left: bool = False

    async def send_line(self, body):
        self.sent.append(body)
        return True

    async def leave(self):
        self.left = True


def test_the_direct_chat_screen_carries_a_remote_room(tmp_path):
    async def scenario():
        db = Database(tmp_path / "node.db")
        user = create_user(db, "alice", password="hunter2pw")
        session = FakeSession()
        room = _Room()
        task = asyncio.create_task(chat_flow.run_direct_chat_loop(
            session, ChatHub(), PresenceRegistry(), user, RemotePeer("bob@Elsewhere", "bob", FP), "x",
            remote_room=room,
        ))
        session.queue_line("hello bob")
        for _ in range(100):
            if room.sent:
                break
            await asyncio.sleep(0.01)
        await room.events.put((LINE, "hi alice"))
        await room.events.put((END, "left"))
        session.queue_key("x")  # the continue prompt after the other side left
        await asyncio.wait_for(task, 5)
        text = _text(session)
        assert room.sent == ["hello bob"]
        assert "hi alice" in text and "bob@Elsewhere has left the direct chat" in text
        assert room.left
        db.close()

    asyncio.run(scenario())


def test_dm_to_a_remote_address_leaves_the_channel_first():
    @dataclass
    class _Ctx:
        direct_invites: object = object()
        session_registry: object = object()
        link_context: object = None
        session: object = None

    @dataclass
    class _Link:
        direct_chats: object = object()

    action = asyncio.run(chat_flow._handle_dm(_Ctx(link_context=_Link()), "bob@Elsewhere"))
    assert isinstance(action, chat_flow._EnterDirectChat) and action.address == "bob@Elsewhere"


@dataclass
class _Outgoing:
    answer: asyncio.Future
    chat_id: str = "c1"
    room: object = None


class _Chats:
    def __init__(self, capable=True):
        self.capable = capable
        self.withdrawn: list = []
        self.invite_obj: _Outgoing | None = None

    def peer_capable(self, fingerprint):
        return self.capable

    async def invite(self, fingerprint, **kw):
        self.invite_obj = _Outgoing(answer=asyncio.get_running_loop().create_future())
        return self.invite_obj

    async def withdraw(self, invite, reason):
        self.withdrawn.append(reason)


@dataclass
class _LinkContext:
    direct_chats: object
    link_node: object = None


def _invite_flow(tmp_path, monkeypatch, chats, drive):
    async def reachable(session, address, *, link_context, report=None):
        return FP

    monkeypatch.setattr(link_direct, "check_live_reachability", reachable)
    monkeypatch.setattr(link_direct, "_node_label", lambda link_context, fp: "Elsewhere")

    async def scenario():
        db = Database(tmp_path / "node.db")
        lane = DatabaseLane(db.path)
        try:
            user = create_user(db, "alice", password="hunter2pw")
            session = FakeSession()
            task = asyncio.create_task(chat_flow.run_remote_direct_chat_invite_flow(
                session, lane, ChatHub(), PresenceRegistry(), user, "bob@Elsewhere",
                link_context=_LinkContext(direct_chats=chats),
            ))
            for _ in range(200):
                if chats.invite_obj is not None or task.done():
                    break
                await asyncio.sleep(0.01)
            await drive(session, chats)
            ran = await asyncio.wait_for(task, 5)
            return ran, _text(session)
        finally:
            lane.close()
            db.close()

    return asyncio.run(scenario())


def test_a_remote_decline_is_reported(tmp_path, monkeypatch):
    async def drive(session, chats):
        chats.invite_obj.answer.set_result("declined")

    ran, text = _invite_flow(tmp_path, monkeypatch, _Chats(), drive)
    assert not ran and "bob@Elsewhere declined." in text


def test_cancelling_withdraws_the_invitation(tmp_path, monkeypatch):
    async def drive(session, chats):
        session.queue_key("c")

    chats = _Chats()
    ran, text = _invite_flow(tmp_path, monkeypatch, chats, drive)
    assert not ran and "Invitation cancelled." in text and chats.withdrawn == ["cancelled"]


def test_an_unavailable_answer_gives_no_reason(tmp_path, monkeypatch):
    async def drive(session, chats):
        chats.invite_obj.answer.set_result("unavailable")

    ran, text = _invite_flow(tmp_path, monkeypatch, _Chats(), drive)
    assert not ran and "can't be reached for live chat right now" in text


def test_an_older_node_is_told_apart(tmp_path, monkeypatch):
    async def drive(session, chats):
        return None

    ran, text = _invite_flow(tmp_path, monkeypatch, _Chats(capable=False), drive)
    assert not ran and "doesn't support direct chats yet" in text


# -- the receiving side --------------------------------------------------------


@dataclass
class _Incoming:
    peer_fingerprint: str = FP
    chat_id: str = "c1"
    from_user: str = "bob"
    from_label: str = "Bob"
    to_user: str = "alice"
    withdrawn: asyncio.Event = field(default_factory=asyncio.Event)
    answers: list = field(default_factory=list)
    room: object = None

    async def accept(self):
        self.answers.append("accepted")
        return self.room

    async def refuse(self, outcome):
        self.answers.append(outcome)

    def drop(self):
        self.answers.append("dropped")


class _Registry:
    def __init__(self, sessions):
        self.sessions = sessions
        self.notified: list = []

    def sessions_for_username(self, username):
        return list(self.sessions)

    async def notify_one(self, session, text, ring=None):
        self.notified.append(text)
        return 1


def test_an_invitation_from_another_node_is_offered_and_accepted_at_the_main_menu(tmp_path):
    async def scenario():
        db = Database(tmp_path / "node.db")
        lane = DatabaseLane(db.path)
        try:
            alice = create_user(db, "alice", password="hunter2pw")
            presence = PresenceRegistry()
            presence.enter("alice")
            invites = DirectChatInvites()
            session = FakeSession()
            registry = _Registry([session])
            incoming = _Incoming(room=_Room())
            offer = link_direct.build_remote_invite_offer(
                lane=lane, presence=presence, direct_invites=invites, session_registry=registry,
            )
            offering = asyncio.create_task(offer(incoming))
            for _ in range(200):
                if invites.pending_for(session) is not None:
                    break
                await asyncio.sleep(0.01)
            assert invites.pending_for(session).inviter.username.startswith("Bob")
            assert registry.notified  # not watching the main menu: told in one line
            await incoming.room.events.put((END, "left"))
            session.queue_line("y")
            session.queue_key("y")
            session.queue_key("x")
            await asyncio.wait_for(main_menu._handle_incoming_invite(
                session, db, invites, ChatHub(), presence, alice,
            ), 5)
            await asyncio.wait_for(offering, 5)
            assert incoming.answers == ["accepted"]
            assert "wants to start a direct chat" in _text(session)
        finally:
            lane.close()
            db.close()

    asyncio.run(scenario())


def test_a_decline_or_an_offline_caller_is_answered(tmp_path):
    async def scenario():
        db = Database(tmp_path / "node.db")
        lane = DatabaseLane(db.path)
        try:
            create_user(db, "alice", password="hunter2pw")
            presence = PresenceRegistry()
            invites = DirectChatInvites()
            session = FakeSession()
            offer = link_direct.build_remote_invite_offer(
                lane=lane, presence=presence, direct_invites=invites, session_registry=_Registry([session]),
            )
            offline = _Incoming()
            await offer(offline)
            assert offline.answers == ["unavailable"]

            presence.enter("alice")
            declined = _Incoming()
            offering = asyncio.create_task(offer(declined))
            for _ in range(200):
                if invites.pending_for(session) is not None:
                    break
                await asyncio.sleep(0.01)
            invites.respond(session, accepted=False)
            await asyncio.wait_for(offering, 5)
            assert declined.answers == ["declined"]
        finally:
            lane.close()
            db.close()

    asyncio.run(scenario())
