"""Paging the SysOp (design doc §5.6 "Pages", decision 1377): the `/sysop`
chat command, the shared Pages queue, its limits, the live notices, and
where the count shows."""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace

import pytest

from netbbs.auth.users import SYSOP_LEVEL, StaffPermission, create_user, set_staff_permissions
from netbbs.chat.mailbox import MessageMailbox
from netbbs.guest import set_guest_user
from netbbs.net import chat_flow
from netbbs.net.admin_flow import staff_menu
from netbbs.net.admin_pages import pages_screen
from netbbs.net.main_menu import _draw_main_menu
from netbbs.net.notices import pending_notices
from netbbs.net.session_registry import ActiveSessionRegistry
from netbbs.staff import set_away
from netbbs.staff_pages import (
    GUEST_NOBODY_ONLINE,
    MAX_REASON_CHARS,
    PAGE_COOLDOWN,
    PageRefused,
    count_open_pages,
    create_page,
    handle_page,
    list_open_pages,
)
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from tests.test_admin_flow import FakeSession, _visible, _written_text


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def lane(db):
    database_lane = DatabaseLane(db.path)
    yield database_lane
    database_lane.close()


@pytest.fixture
def sysop(db):
    return create_user(db, "sysop", password="hunter2", user_level=SYSOP_LEVEL)


@pytest.fixture
def nib(db):
    return create_user(db, "nib", password="hunter2", user_level=10)


@pytest.fixture
def guest(db):
    user = create_user(db, "guest", password="hunter2", user_level=5)
    set_guest_user(db, user)
    return user


def _now(minutes=0):
    return datetime.datetime(2026, 10, 18, 20, 0, tzinfo=datetime.timezone.utc) + datetime.timedelta(minutes=minutes)


# -- the queue and its limits ---------------------------------------------------


def test_a_page_is_stored_with_its_place_and_counted(db, sysop, nib):
    result = create_page(db, nib, "  my upload stops at 90%  ", place="Chat › Lobby", recipient_online=False)
    assert not result.replaced
    assert result.page.reason == "my upload stops at 90%"
    assert result.page.place == "Chat › Lobby"
    assert count_open_pages(db) == 1


def test_a_second_page_after_the_cooldown_replaces_the_open_ones_reason(db, sysop, nib):
    create_page(db, nib, "first", place="Chat › Lobby", recipient_online=False, now=_now())
    later = create_page(db, nib, "second", place="Main menu", recipient_online=False, now=_now(11))
    assert later.replaced
    assert [page.reason for page in list_open_pages(db)] == ["second"]


def test_a_caller_pages_at_most_once_in_ten_minutes(db, sysop, nib):
    create_page(db, nib, "first", place="Chat › Lobby", recipient_online=False, now=_now())
    with pytest.raises(PageRefused, match="4 minutes ago and it is still open. You can page again in 6 minutes"):
        create_page(db, nib, "again", place="Chat › Lobby", recipient_online=False, now=_now(4))
    # Handled, the cooldown still holds, and the refusal no longer says open.
    handle_page(db, list_open_pages(db)[0].id, sysop, "done")
    with pytest.raises(PageRefused, match=r"^you paged 5 minutes ago\. "):
        create_page(db, nib, "again", place="Chat › Lobby", recipient_online=False, now=_now(5))
    assert not create_page(db, nib, "again", place="Chat", recipient_online=False, now=_now(10)).replaced
    assert PAGE_COOLDOWN == datetime.timedelta(minutes=10)


def test_staff_cannot_page_their_own_node(db, sysop):
    helper = set_staff_permissions(
        db, create_user(db, "helper", password="hunter2"), StaffPermission.APPROVE_ACCOUNTS, changed_by=sysop,
    )
    for user in (sysop, helper):
        with pytest.raises(PageRefused, match="you are staff here"):
            create_page(db, user, "hi", place="Chat", recipient_online=True)


@pytest.mark.parametrize("reason, problem", [
    ("x" * (MAX_REASON_CHARS + 1), "160 characters"),
    ("red |04alert", "pipe code"),
    ("two\nlines", "one line"),
])
def test_a_reason_is_one_short_line_of_plain_text(db, sysop, nib, reason, problem):
    with pytest.raises(PageRefused, match=problem):
        create_page(db, nib, reason, place="Chat", recipient_online=False)


def test_a_guest_pages_only_while_someone_is_online(db, sysop, guest):
    with pytest.raises(PageRefused) as refused:
        create_page(db, guest, "how do I sign up?", place="Chat", guest_session="s1", recipient_online=False)
    assert str(refused.value) == GUEST_NOBODY_ONLINE
    assert count_open_pages(db) == 0


def test_guests_limits_hold_per_session_not_for_the_shared_account(db, sysop, guest):
    create_page(db, guest, "one", place="Chat", guest_session="s1", recipient_online=True, now=_now())
    # Another guest, on another session, is not held up by the first.
    second = create_page(db, guest, "two", place="Chat", guest_session="s2", recipient_online=True, now=_now(1))
    assert not second.replaced
    with pytest.raises(PageRefused, match="You can page again"):
        create_page(db, guest, "one more", place="Chat", guest_session="s1", recipient_online=True, now=_now(2))
    assert count_open_pages(db) == 2


def test_handling_a_page_closes_it_for_everyone(db, sysop, nib):
    other = create_user(db, "cosysop", password="hunter2", user_level=SYSOP_LEVEL)
    page = create_page(db, nib, "help", place="Chat", recipient_online=False).page
    handled = handle_page(db, page.id, other, "done")
    assert handled.handled_by_label == "cosysop" and handled.outcome == "done"
    assert count_open_pages(db) == 0
    with pytest.raises(PageRefused, match="cosysop already handled that page"):
        handle_page(db, page.id, sysop, "chat")


def test_a_caller_cannot_handle_a_page(db, sysop, nib):
    page = create_page(db, nib, "help", place="Chat", recipient_online=False).page
    with pytest.raises(PageRefused, match="Only a SysOp or a staff member"):
        handle_page(db, page.id, nib, "done")


# -- /sysop in chat ---------------------------------------------------------------


class _Hub:
    """A page must never reach the channel: any use of the hub fails."""

    def __getattr__(self, name):
        raise AssertionError(f"/sysop touched the chat hub ({name})")


def _ctx(session, lane, user, registry):
    session.activity = ("Chat", "Lobby")
    return SimpleNamespace(
        session=session, lane=lane, user=user, session_registry=registry, hub_reply=None, hub=_Hub(),
        channel=SimpleNamespace(name="Lobby"),
    )


def _staff_session(registry, username, *, hook=True, door=False):
    staff_session = FakeSession()
    registry.enter(staff_session)
    registry.mark_authenticated(staff_session, username)
    seen, bells = [], []
    if hook:
        async def deliver(text):
            seen.append(text)

        async def bell(sender):
            bells.append(sender)

        staff_session.pinned_notice_hook = deliver
        staff_session.chat_bell_hook = bell
    staff_session.door_active = door
    return staff_session, seen, bells


def test_sysop_command_answers_privately_and_tells_the_sysop_in_chat(db, lane, sysop, nib):
    async def scenario():
        registry = ActiveSessionRegistry()
        _sysop_session, seen, bells = _staff_session(registry, "sysop")
        caller = FakeSession()
        await chat_flow._handle_sysop(_ctx(caller, lane, nib, registry), "my upload stops at 90%")
        return caller, seen, bells, registry

    caller, seen, bells, registry = asyncio.run(scenario())
    answer = _visible(_written_text(caller))
    assert "Paged the SysOp" in answer
    assert "sysop is online and was told just now" in answer
    assert "my upload stops at 90%" in answer
    assert "nothing was said here" in answer
    assert len(seen) == 1 and "! Page from nib (Chat › Lobby): my upload stops at 90%" in _visible(seen[0])
    assert "SysOp → Pages" in _visible(seen[0])
    assert bells == ["page:nib"]
    # The Monitor and the node log name the place, never the reason.
    events = [event.text for event in registry.recent_events()]
    assert "page: nib (Chat › Lobby)" in events
    assert not any("upload" in text for text in events)


def test_a_sysop_in_a_door_gets_nothing_written_into_it(db, lane, sysop, nib):
    async def scenario():
        registry = ActiveSessionRegistry()
        door_session, seen, _bells = _staff_session(registry, "sysop", door=True)
        caller = FakeSession()
        await chat_flow._handle_sysop(_ctx(caller, lane, nib, registry), "help please")
        return caller, door_session, seen

    caller, door_session, seen = asyncio.run(scenario())
    assert seen == [] and door_session.written == []
    assert any("Page from nib" in _visible(line) for line in pending_notices(door_session))
    assert "sysop is in a door and sees it on leaving" in _visible(_written_text(caller))


def test_with_nobody_online_the_page_waits_and_names_who_is_back_first(db, lane, sysop, nib):
    set_away(db, sysop, "At a pen show", datetime.date.today() + datetime.timedelta(days=2))

    async def scenario():
        caller = FakeSession()
        await chat_flow._handle_sysop(_ctx(caller, lane, nib, ActiveSessionRegistry()), "re-open my thread")
        return caller

    answer = _visible(_written_text(asyncio.run(scenario())))
    assert "Nobody is online; it waits in Pages" in answer
    assert "sysop, back " in answer and "At a pen show" in answer
    assert count_open_pages(db) == 1


def test_a_bare_sysop_shows_usage_and_pages_nobody(db, lane, sysop, nib):
    async def scenario():
        caller = FakeSession()
        await chat_flow._handle_sysop(_ctx(caller, lane, nib, ActiveSessionRegistry()), "   ")
        return caller

    assert "Usage: /sysop <reason>" in _visible(_written_text(asyncio.run(scenario())))
    assert count_open_pages(db) == 0


def test_a_refused_page_is_said_in_a_block(db, lane, sysop, guest):
    async def scenario():
        caller = FakeSession()
        await chat_flow._handle_sysop(_ctx(caller, lane, guest, ActiveSessionRegistry()), "how do I sign up?")
        return caller

    answer = _visible(_written_text(asyncio.run(scenario())))
    assert "Page not sent" in answer and "a guest's page can't be answered later" in answer


def test_help_lists_sysop_for_callers_but_not_for_staff(db, sysop, nib):
    assert chat_flow._may_page(db, None, nib)
    assert not chat_flow._may_page(db, None, sysop)
    assert chat_flow._COMMAND_INFO["sysop"][0] == "/sysop <reason>"


# -- where the queue shows ------------------------------------------------------


def test_the_main_menu_tells_the_sysop_a_page_waits(db, sysop, nib):
    create_page(db, nib, "help", place="Chat", recipient_online=False)
    session = FakeSession()
    asyncio.run(_draw_main_menu(session, db, MessageMailbox(), sysop))
    assert "1 page waiting: SysOp → Pages." in _visible(_written_text(session))
    caller = FakeSession()
    asyncio.run(_draw_main_menu(caller, db, MessageMailbox(), nib))
    assert "page waiting" not in _visible(_written_text(caller))


def test_the_staff_console_counts_and_closes_pages(db, lane, sysop, nib):
    helper = set_staff_permissions(
        db, create_user(db, "helper", password="hunter2"), StaffPermission.APPROVE_ACCOUNTS, changed_by=sysop,
    )
    create_page(db, nib, "my thread is locked", place="Chat › Lobby", recipient_online=False)
    # p: Pages; 01: nib's page; d: done; b: console.
    session = FakeSession(["p", "0", "1", "d", "b"])
    asyncio.run(staff_menu(session, lane, helper))
    text = _visible(_written_text(session))
    assert "Pages waiting" in text and "[P]ages" in text
    assert "my thread is locked" in text
    assert count_open_pages(db) == 0
    assert list_open_pages(db) == []


def test_a_page_handled_elsewhere_says_who_handled_it(db, lane, sysop, nib):
    other = create_user(db, "cosysop", password="hunter2", user_level=SYSOP_LEVEL)
    page = create_page(db, nib, "help", place="Chat", recipient_online=False).page
    session = FakeSession(["0", "1", "d"])

    async def scenario():
        # Closed by someone else between the list and the key.
        original = handle_page

        def racing(db_, page_id, actor, outcome):
            original(db_, page_id, other, "done")
            return original(db_, page_id, actor, outcome)

        import netbbs.net.admin_pages as admin_pages
        admin_pages.handle_page = racing
        try:
            await pages_screen(session, lane, sysop, None)
        finally:
            admin_pages.handle_page = original

    asyncio.run(scenario())
    assert count_open_pages(db) == 0
    assert any("cosysop already handled that page." in _visible(line) for line in pending_notices(session))
    assert page.id


# -- Baud ------------------------------------------------------------------------


def test_baud_points_to_sysop_and_lists_it_in_help():
    from netbbs.bots.baud import Baud

    sent = []
    baud = Baud({}, sent.append)
    event = {"type": "message", "request": "a", "caller": {"id": 7, "name": "nib"}, "channel": "lobby"}
    baud.handle({**event, "text": "!sysop can't log in on SSH"})
    assert sent[-1]["action"] == "reply"
    assert "Type /sysop <reason>" in sent[-1]["text"] and "this line was public" in sent[-1]["text"]
    baud.handle({**event, "text": "help"})
    assert "/sysop <reason>" in sent[-1]["text"]
