"""The welcome letter: a letter from the SysOp in every new account's mailbox (issue #1352)."""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import SYSOP_LEVEL, approve_pending_user, create_user, get_user_by_username, list_users
from netbbs.config import RegistrationMode, get_config, set_config, set_registration_mode
from netbbs.mail import list_inbox
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.welcome_letter import (
    BODY_KEY,
    ENABLED_KEY,
    SENDER_KEY,
    SUBJECT_KEY,
    load_welcome_letter,
    send_welcome_letter,
)
from tests.test_admin_flow import FakeSession as AdminSession
from tests.test_admin_flow import _run as run_console
from tests.test_admin_flow import _written_text
from tests.test_registration import FakeSession as LoginSession
from tests.test_registration import _run_login


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


def _turn_on(db, sender, *, subject="Welcome to {node}, {user}!", body="Hi {user}, today is {date}. {keep}"):
    set_config(db, ENABLED_KEY, "1")
    set_config(db, SENDER_KEY, str(sender.id))
    set_config(db, SUBJECT_KEY, subject)
    set_config(db, BODY_KEY, body)


def test_off_by_default_no_letter(db, sysop):
    alice = create_user(db, "alice", password="hunter2pw")
    assert load_welcome_letter(db).enabled is False
    assert send_welcome_letter(db, alice) is False
    assert list_inbox(db, alice) == []


def test_a_new_account_gets_one_letter_with_its_words_filled_in(db, sysop):
    _turn_on(db, sysop)
    alice = create_user(db, "alice", password="hunter2pw")
    assert send_welcome_letter(db, alice) is True
    assert send_welcome_letter(db, alice) is False  # never twice
    [letter] = list_inbox(db, alice)
    assert letter.sender_label == "sysop"
    assert letter.subject.endswith(", alice!") and "{node}" not in letter.subject
    assert letter.body.startswith("Hi alice, today is 20") and letter.body.endswith("{keep}")
    state = load_welcome_letter(db)
    assert (state.sent, state.last_recipient) == (1, "alice")


def test_a_letter_already_sent_keeps_its_text_when_the_template_changes(db, sysop):
    _turn_on(db, sysop, body="first version")
    alice = create_user(db, "alice", password="hunter2pw")
    send_welcome_letter(db, alice)
    set_config(db, BODY_KEY, "second version")
    bob = create_user(db, "bob", password="hunter2pw")
    send_welcome_letter(db, bob)
    assert list_inbox(db, alice)[0].body == "first version"
    assert list_inbox(db, bob)[0].body == "second version"


def test_a_pending_account_waits_for_approval(db, sysop):
    _turn_on(db, sysop)
    carol = create_user(db, "carol", password="hunter2pw", pending_approval=True)
    assert send_welcome_letter(db, carol) is False
    approved = approve_pending_user(db, carol, approved_by=sysop)
    assert send_welcome_letter(db, approved) is True


def test_a_sender_that_cannot_send_leaves_the_account_and_says_why(db, sysop):
    plain = create_user(db, "plain", password="hunter2pw")
    _turn_on(db, plain)
    alice = create_user(db, "alice", password="hunter2pw")
    assert send_welcome_letter(db, alice) is False
    assert list_inbox(db, alice) == []
    assert "isn't staff" in load_welcome_letter(db).last_failure


def test_a_template_that_no_longer_validates_is_logged_not_raised(db, sysop):
    _turn_on(db, sysop, subject="x" * 500)
    alice = create_user(db, "alice", password="hunter2pw")
    assert send_welcome_letter(db, alice) is False
    assert "subject" in load_welcome_letter(db).last_failure
    assert get_config(db, "welcome_letter_sent") is None


# -- where accounts become usable ------------------------------------------------


def test_open_registration_delivers_it(db, sysop):
    _turn_on(db, sysop)
    session = LoginSession(["new", "alice", "hunter2pw", "hunter2pw", "y"], keys=["l"])
    asyncio.run(_run_login(session, db))
    assert len(list_inbox(db, get_user_by_username(db, "alice"))) == 1


def test_approval_required_registration_delivers_it_on_approval(db, lane, sysop):
    _turn_on(db, sysop)
    set_registration_mode(db, RegistrationMode.APPROVAL_REQUIRED)
    from tests.test_registration import _throttle_config

    session = LoginSession(["new", "carol", "hunter2pw", "hunter2pw", "", "", "", ""])
    asyncio.run(_run_login(session, db, _throttle_config(max_attempts_per_connection=2)))
    carol = get_user_by_username(db, "carol")
    assert list_inbox(db, carol) == []
    console = AdminSession(["u", "u", "0", "1", "a", "y", "b", "b", "b", "b"])
    run_console(console, lane, sysop)
    assert "The welcome letter is in their mailbox." in _written_text(console)
    assert len(list_inbox(db, get_user_by_username(db, "carol"))) == 1


def test_an_account_made_on_the_console_gets_it(db, lane, sysop):
    _turn_on(db, sysop)
    session = AdminSession(["u", "c", "0", "1", "alice", "0", "2", "hunter2", "hunter2", "0", "4", "10", "c", "b", "b"])
    run_console(session, lane, sysop)
    alice = next(u for u in list_users(db) if u.username == "alice")
    assert len(list_inbox(db, alice)) == 1


def test_existing_accounts_never_get_it(db, sysop):
    early = create_user(db, "early", password="hunter2pw")
    _turn_on(db, sysop)
    assert list_inbox(db, early) == []


# -- the console screen ----------------------------------------------------------


class _PreviewSession(AdminSession):
    async def read_any_key(self, echo: bool = True) -> str:
        return " "


_TO_SCREEN = ["s", "m", "s", "n"]
_OUT = ["b", "b", "b", "b", "b"]


def test_switching_it_on_makes_the_sysop_the_sender_and_is_audited(db, lane, sysop):
    from netbbs.moderation.log import list_recent_actions

    session = AdminSession([*_TO_SCREEN, "o", *_OUT])
    run_console(session, lane, sysop)
    state = load_welcome_letter(db)
    assert state.enabled and state.sender_id == sysop.id
    assert "Welcome letter on, from sysop." in _written_text(session)
    assert any(entry.action == "set_welcome_letter" for entry in list_recent_actions(db))


def test_the_screen_lists_it_beside_the_new_account_banners(db, lane, sysop):
    session = AdminSession(["s", "m", "s", *_OUT])
    run_console(session, lane, sysop)
    assert "ew-account letter" in _written_text(session)


def test_from_refuses_an_account_that_is_not_staff(db, lane, sysop):
    create_user(db, "plain", password="hunter2pw")
    session = AdminSession([*_TO_SCREEN, "f", "plain", *_OUT])
    run_console(session, lane, sysop)
    assert "isn't staff" in _written_text(session)
    assert load_welcome_letter(db).sender_id is None


def test_subject_and_preview(db, lane, sysop):
    session = _PreviewSession([*_TO_SCREEN, "s", "Hello {user} from {node}", "p", *_OUT])
    run_console(session, lane, sysop)
    assert load_welcome_letter(db).subject == "Hello {user} from {node}"
    text = _written_text(session)
    assert "Subject: Hello NewCaller from" in text and "To: NewCaller" in text
