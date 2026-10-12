"""PgUp/PgDn/Esc in chat: the line editors hand them over without touching
the line being typed, PgUp/PgDn scroll back through the chat's kept lines
(holding still while new ones arrive), and a long command reply is paged
like the MRC hub's answers."""

from __future__ import annotations

import asyncio

import aiohttp

from netbbs.chat.channels import create_channel
from netbbs.chat.mailbox import MessageMailbox
from netbbs.mrc.bridge import MrcNotice
from netbbs.net import chat_flow
from netbbs.net.char_input import InputHistory, read_line
from netbbs.net.session import Session, SessionClosedError
from netbbs.rendering.ansi import strip_ansi
from netbbs.timeutil import utc_now_iso
from tests.eventually import eventually
from tests.test_char_input import FakeByteSource, Writer
from tests.test_chat_flow_mrc import (  # noqa: F401 -- fixtures
    _QueueSession,
    alice,
    channel,
    db,
    hub,
    lane,
    presence,
    sysop,
)
from tests.test_web_line_editing import _run_server

PGUP, PGDN, ESC = "\x1b[5~", "\x1b[6~", "\x1b"


# -- the line editors ---------------------------------------------------------


def test_the_shared_editor_hands_over_page_keys_and_a_bare_escape_keeping_the_line():
    async def scenario():
        keys: list[str] = []

        async def navigate(key: str) -> None:
            keys.append(key)

        source = FakeByteSource(f"he{PGUP}l{PGDN}lo\r".encode())
        line = await read_line(source, Writer(), navigate=navigate)
        # A bare Escape is one with nothing after it -- a fake source has no
        # timing, so it goes last, as in tests/test_char_input_prefill.py.
        escaped = FakeByteSource(f"hi{ESC}".encode())
        writer = Writer()
        try:
            await read_line(escaped, writer, navigate=navigate)
        except SessionClosedError:
            pass  # the source ran dry after the Escape, as intended
        return line, keys, writer.joined

    line, keys, echoed = asyncio.run(scenario())
    assert line == "hello"
    assert keys == ["PAGE_UP", "PAGE_DOWN", "ESCAPE"]
    assert echoed.endswith("hi"), "the Escape took nothing from the line"


def test_without_a_handler_the_keys_do_nothing_as_before():
    line = asyncio.run(read_line(FakeByteSource(f"he{PGUP}llo\r".encode()), Writer()))
    assert line == "hello"


def test_the_web_editor_hands_over_the_same_keys():
    keys: list[str] = []
    received: list[str] = []

    async def navigate(key: str) -> None:
        keys.append(key)

    async def handler(session: Session):
        received.append(await session.read_line(navigate=navigate))

    async def scenario():
        server = await _run_server(handler)
        try:
            async with aiohttp.ClientSession() as client:
                async with client.ws_connect(f"http://127.0.0.1:{server.port}/ws") as ws:
                    for data in ("he", PGUP, "l", PGDN, "l", ESC, "o\r"):
                        await ws.send_json({"type": "key", "data": data})
                        await asyncio.sleep(0.05)
                    while True:
                        msg = await ws.receive_json(timeout=2)
                        if msg["data"].endswith("\r\n"):
                            break
        finally:
            await server.stop()

    asyncio.run(scenario())
    assert received == ["hello"]
    assert keys == ["PAGE_UP", "PAGE_DOWN", "ESCAPE"]


# -- chat ---------------------------------------------------------------------


class _NavSession(_QueueSession):
    """A pinned-UI chat session whose line editor hands its `navigate`
    callback to the test, the way a real one calls it on PgUp/PgDn/Esc."""

    def __init__(self, height: int = 12) -> None:
        super().__init__()
        self.terminal_width, self.terminal_height = 80, height
        self.navigate = None

    async def read_line(self, echo=True, history=None, completer=None, *, navigate=None, **_ignored):
        self.navigate = navigate
        return await self.inputs.get()

    def text(self) -> str:
        return strip_ansi("".join(self.written))

    def since(self, mark: int) -> str:
        return strip_ansi("".join(self.written[mark:]))


async def _in_chat(lane, hub, presence, channel, user, session):
    task = asyncio.create_task(chat_flow._chat_loop(
        session, lane, hub, presence, MessageMailbox(), InputHistory(), channel, user,
    ))
    assert await eventually(lambda: session.navigate is not None)
    [participant] = hub.participants_for_username(channel.name, user.username)
    return task, participant


async def _leave(session, task):
    session.inputs.put_nowait("/quit")
    await asyncio.wait_for(task, timeout=4)


def test_pgup_scrolls_back_and_the_view_holds_still_while_lines_arrive(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=12)  # nine rows of chat area
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            for n in range(30):
                await hub.send_to(channel.name, participant, MrcNotice(f"line {n:02}", utc_now_iso()))
            assert await eventually(lambda: "line 29" in session.text())

            mark = len(session.written)
            await session.navigate("PAGE_UP")
            shown = session.since(mark)
            assert "lines back" in shown and "PgDn newer" in shown and "Esc live" in shown
            assert "line 22" in shown and "line 29" not in shown

            # A line arriving now is kept and counted, not drawn over the view.
            mark = len(session.written)
            await hub.send_to(channel.name, participant, MrcNotice("fresh news", utc_now_iso()))
            assert await eventually(lambda: "1 new" in session.since(mark))
            assert "fresh news" not in session.since(mark)

            # Further back, then Esc: live again, with what arrived meanwhile.
            await session.navigate("PAGE_UP")
            assert "line 15" in session.since(mark)
            mark = len(session.written)
            await session.navigate("ESCAPE")
            live = session.since(mark)
            assert "fresh news" in live and "line 29" in live and "lines back" not in live
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_pgdn_past_the_newest_returns_to_live_and_a_typed_line_does_too(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=12)
        task, participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            for n in range(30):
                await hub.send_to(channel.name, participant, MrcNotice(f"line {n:02}", utc_now_iso()))
            assert await eventually(lambda: "line 29" in session.text())
            await session.navigate("PAGE_UP")
            await session.navigate("PAGE_UP")
            mark = len(session.written)
            await session.navigate("PAGE_DOWN")
            assert "lines back" in session.since(mark)
            await session.navigate("PAGE_DOWN")
            assert "line 29" in session.since(mark) and "lines back" not in session.since(mark).rpartition("line 29")[2]
            await session.navigate("PAGE_UP")
            mark = len(session.written)
            session.inputs.put_nowait("/away lunch")
            assert await eventually(lambda: "You are now marked away" in session.since(mark))
            assert "line 29" in session.since(mark)  # back to live before the answer
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_a_long_command_reply_is_paged_and_esc_drops_the_rest(db, lane, hub, presence, channel, alice, sysop):
    for n in range(20):
        create_channel(db, f"room-{n:02}", creator=sysop)

    async def scenario():
        session = _NavSession(height=12)  # pages of six rows
        task, _participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            session.inputs.put_nowait("/list")
            assert await eventually(lambda: "PgDn more · PgUp back · Esc done" in session.text())
            text = session.text()
            assert "Channels" in text and "page 1 of" in text and "room-19" not in text
            mark = len(session.written)
            await session.navigate("PAGE_DOWN")
            assert "2/" in session.since(mark) and "page 2 of" in session.since(mark)
            mark = len(session.written)
            await session.navigate("ESCAPE")
            assert "the rest of that answer is skipped" in session.since(mark)
            mark = len(session.written)
            await session.navigate("PAGE_DOWN")
            assert "room-" not in session.since(mark)
        finally:
            await _leave(session, task)

    asyncio.run(scenario())


def test_a_short_command_reply_is_not_paged(lane, hub, presence, channel, alice):
    async def scenario():
        session = _NavSession(height=24)
        task, _participant = await _in_chat(lane, hub, presence, channel, alice, session)
        try:
            session.inputs.put_nowait("/list")
            assert await eventually(lambda: "Channels" in session.text())
            await asyncio.sleep(0.2)
            assert "page 1 of" not in session.text()
        finally:
            await _leave(session, task)

    asyncio.run(scenario())
