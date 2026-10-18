"""The oneliner wall (issue #1391).

A lower section of the Recent callers frame at logon (one pause, with
`[A]dd a line` beside `[Enter] Continue`), the same panel on `[R]ecent
callers` with `[A]dd a line` and `[O]neliners` on its bar, and the full wall
behind `[O]neliners`. 60 columns, one line per call and three per account a
day; authors delete their own lines, a SysOp or "moderate everything" staff
any line, audited.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from netbbs.auth.users import SYSOP_LEVEL, StaffPermission, create_user, delete_user, set_staff_permissions
from netbbs.config import set_oneliner_min_level
from netbbs.moderation.log import list_recent_actions
from netbbs.net.char_input import HELP_KEY
from netbbs.net.oneliner_flow import oneliner_wall_screen
from netbbs.net.profile_flow import (
    _previous_callers_screen,
    _show_previous_callers_screen,
    _split_panel_rows,
)
from netbbs.oneliners import (
    DAILY_ONELINER_LIMIT,
    MAX_KEPT_ONELINERS,
    OnelinerError,
    add_oneliner,
    delete_oneliner,
    list_oneliners,
    normalize_oneliner,
    oneliner_refusal,
    set_oneliners_enabled,
)
from netbbs.session_history import (
    record_session_end,
    record_session_start,
    set_previous_callers_enabled,
    set_previous_callers_plain,
)
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import utc_iso
from tests.test_mail_flow import ESC, FakeSession
from tests.test_mail_from_meeting_places import _screens, _visible


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
def alice(db):
    return create_user(db, "alice", password="hunter2pw", user_level=10)


@pytest.fixture
def bob(db):
    return create_user(db, "bob", password="hunter2pw", user_level=10)


def _called(db, user):
    record_session_end(db, record_session_start(db, user))


def _logon_session(keys, lines=None, *, width=80, height=24):
    session = FakeSession(keys=keys, lines=lines)
    session.terminal_width, session.terminal_height = width, height

    async def read_any_key(echo=True):
        return await session.read_key()

    session.read_any_key = read_any_key
    return session


def _say(db, user, text):
    return add_oneliner(db, user, text, added_this_call=False)


# -- the domain ----------------------------------------------------------------


def test_a_line_is_sanitized_and_its_whitespace_normalized(db, alice):
    line = _say(db, alice, "  hi\tthere\x1b[31m ‮evil\n  ")

    assert line.text == "hi there[31m evil"
    assert normalize_oneliner("a\r\nb") == "a b"


def test_a_line_is_capped_at_sixty_display_columns(db, alice):
    _say(db, alice, "x" * 60)
    with pytest.raises(OnelinerError, match="1 column too long"):
        _say(db, alice, "y" * 61)
    # Wide characters count two columns each: 31 of them are 62 columns.
    with pytest.raises(OnelinerError, match="too long"):
        _say(db, alice, "漢" * 31)


def test_a_blank_line_is_refused(db, alice):
    with pytest.raises(OnelinerError):
        _say(db, alice, "   \t ")
    assert list_oneliners(db) == []


def test_one_line_per_call_and_three_per_day(db, alice):
    assert oneliner_refusal(db, alice, added_this_call=True) == "You've added a line this call."
    for number in range(DAILY_ONELINER_LIMIT):
        _say(db, alice, f"line {number}")
    with pytest.raises(OnelinerError, match="3 lines today"):
        _say(db, alice, "one too many")

    # A day later the account may write again.
    old = utc_iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1, minutes=1))
    db.connection.execute("UPDATE oneliners SET created_at = ?", (old,))
    db.connection.commit()
    assert oneliner_refusal(db, alice, added_this_call=False) is None


def test_the_level_gates_writing_not_reading(db, alice, bob):
    _say(db, bob, "hello")
    set_oneliner_min_level(db, 20)

    assert oneliner_refusal(db, alice, added_this_call=False) == "Adding a line to the wall needs level 20."
    with pytest.raises(OnelinerError):
        _say(db, alice, "let me in")
    assert [line.text for line in list_oneliners(db)] == ["hello"]


def test_the_wall_keeps_only_its_newest_lines(db):
    authors = [create_user(db, f"caller{n}", password="hunter2pw", user_level=10) for n in range(70)]
    for number in range(MAX_KEPT_ONELINERS + 5):
        _say(db, authors[number % len(authors)], f"line {number}")

    lines = list_oneliners(db)
    assert len(lines) == MAX_KEPT_ONELINERS
    assert lines[0].text == f"line {MAX_KEPT_ONELINERS + 4}"
    assert lines[-1].text == "line 5"


def test_a_line_outlives_its_authors_account(db, alice):
    sysop = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
    _say(db, alice, "still here")

    delete_user(db, alice, deleted_by=sysop)

    [line] = list_oneliners(db)
    assert line.author_user_id is None and line.author_label == "alice"


def test_an_author_deletes_their_own_line_unaudited(db, alice, bob):
    line = _say(db, alice, "oops")
    with pytest.raises(OnelinerError, match="only your own"):
        delete_oneliner(db, bob, line.id)

    delete_oneliner(db, alice, line.id)

    assert list_oneliners(db) == []
    assert not [a for a in list_recent_actions(db, limit=10) if a.action == "delete_oneliner"]


@pytest.mark.parametrize("kind", ["sysop", "staff"])
def test_a_moderator_deletes_any_line_audited_with_its_text(db, alice, kind):
    if kind == "sysop":
        moderator = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
    else:
        moderator = create_user(db, "mod", password="hunter2pw", user_level=50)
        sysop = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
        moderator = set_staff_permissions(db, moderator, StaffPermission.MODERATE_ALL, changed_by=sysop)
    line = _say(db, alice, "rude words")

    delete_oneliner(db, moderator, line.id)

    [entry] = [a for a in list_recent_actions(db, limit=10) if a.action == "delete_oneliner"]
    assert entry.detail == "alice: rude words"
    assert entry.target_user_id == alice.id


# -- the shared height budget ---------------------------------------------------


def test_the_panel_budget_shares_rows_and_keeps_one_of_each():
    assert _split_panel_rows(4, 10, 10) == (2, 1)
    assert _split_panel_rows(30, 3, 2) == (3, 2)
    assert _split_panel_rows(15, 10, 10) == (7, 7)
    # Too little room for both and a divider: the roll keeps its place.
    assert _split_panel_rows(2, 5, 5) == (2, 0)
    assert _split_panel_rows(3, 0, 5) == (0, 3)
    assert _split_panel_rows(0, 5, 5) == (0, 0)


# -- logon ----------------------------------------------------------------------


def test_logon_shows_the_wall_under_recent_callers_with_one_pause(db, alice, bob):
    _called(db, bob)
    _say(db, bob, "first call in years and this place rules")
    session = _logon_session(keys=["\r"])

    assert asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None)) is True

    text = _visible(session)
    assert "R E C E N T" in text and "O N E L I N E R S" in text
    assert "first call in years and this place rules" in text
    assert "[A]dd a line" in text and "[Enter] Continue" in text
    assert text.index("bob") < text.index("O N E L I N E R S")


def test_an_empty_wall_at_logon_is_a_non_event(db, alice):
    """No callers and no lines: no panel and no pause at all."""
    session = _logon_session(keys=[])

    assert asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None)) is False
    assert session.written == []


def test_the_wall_alone_is_shown_when_recent_callers_is_hidden(db, alice, bob):
    set_previous_callers_enabled(db, False)
    _say(db, bob, "anyone up for war dialer tonight?")
    session = _logon_session(keys=["x"])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    text = _visible(session)
    assert "O N E L I N E R S" in text and "R E C E N T" not in text
    assert "anyone up for war dialer tonight?" in text


def test_the_wall_follows_its_own_toggle_at_logon(db, alice, bob):
    _called(db, bob)
    _say(db, bob, "hidden line")
    set_oneliners_enabled(db, False)
    session = _logon_session(keys=["a"])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    text = _visible(session)
    assert "hidden line" not in text and "[A]dd a line" not in text
    assert list_oneliners(db)[0].text == "hidden line"  # "a" did nothing


def test_the_plain_style_reaches_the_wall(db, alice, bob):
    _called(db, bob)
    _say(db, bob, "quiet please")
    set_previous_callers_plain(db, True)
    session = _logon_session(keys=["\r"])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    text = _visible(session)
    assert "Oneliners" in text and "O N E L I N E R S" not in text


def test_adding_a_line_at_logon_is_carried_to_the_main_menu(db, alice, bob):
    _called(db, bob)
    session = _logon_session(keys=["a"], lines=["see you all at the meet"])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    [line] = list_oneliners(db)
    assert line.text == "see you all at the meet" and line.author_label == "alice"
    from netbbs.net.notices import take_notices
    assert any("Added to the wall." in notice for notice in take_notices(session))
    # One line per call.
    assert oneliner_refusal(db, alice, added_this_call=session.oneliner_added) == "You've added a line this call."


def test_escape_at_the_line_prompt_adds_nothing(db, alice, bob):
    _called(db, bob)
    session = _logon_session(keys=["a"], lines=[ESC])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    assert list_oneliners(db) == []


def test_a_caller_below_the_level_is_not_offered_add_at_logon(db, alice, bob):
    _called(db, bob)
    set_oneliner_min_level(db, 50)
    session = _logon_session(keys=["a"])

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    assert "[A]dd a line" not in _visible(session)
    assert list_oneliners(db) == []


@pytest.mark.parametrize("size", [(40, 12), (80, 24)])
def test_the_combined_logon_panel_fits_the_terminal(db, alice, size):
    width, height = size
    for number in range(12):
        caller = create_user(db, f"caller{number:02d}", password="hunter2pw", user_level=10)
        _called(db, caller)
        _say(db, caller, "w" * 60)
    session = _logon_session(keys=["\r"], width=width, height=height)

    asyncio.run(_show_previous_callers_screen(session, db, alice, current_history_id=None))

    text = _visible(session)
    rows = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    assert all(len(row) <= width for row in rows)
    assert len(rows) <= height
    assert "caller11" in text and "O N E L I N E R S" in text


# -- [R]ecent callers -----------------------------------------------------------


def test_recent_callers_offers_add_and_the_full_wall(db, lane, alice, bob):
    _called(db, bob)
    _say(db, bob, "the new menu ansi is gorgeous")
    session = FakeSession(keys=["o", "b", "b"])

    asyncio.run(_previous_callers_screen(session, db, alice, lane=lane))

    text = _visible(session)
    assert "[M]ail a caller" in text and "[A]dd a line" in text and "[O]neliners" in text
    assert "the new menu ansi is gorgeous" in text
    assert "Recent callers > Oneliners" in text or "Oneliners" in text


def test_recent_callers_shows_the_wall_even_with_the_logon_toggle_off(db, lane, alice, bob):
    _say(db, bob, "always here")
    set_oneliners_enabled(db, False)
    session = FakeSession(keys=["b"])

    asyncio.run(_previous_callers_screen(session, db, alice, lane=lane))

    assert "always here" in _visible(session)


def test_recent_callers_adds_a_line_and_redraws_with_it(db, lane, alice, bob):
    _called(db, bob)
    session = FakeSession(keys=["a", "b"], lines=["hello from alice"])

    asyncio.run(_previous_callers_screen(session, db, alice, lane=lane))

    text = _visible(session)
    assert [line.text for line in list_oneliners(db)] == ["hello from alice"]
    last = text[text.rindex("R E C E N T"):]
    assert "hello from alice" in last and "Added to the wall." in last


def test_recent_callers_refuses_a_second_line_this_call(db, lane, alice, bob):
    _called(db, bob)
    session = FakeSession(keys=["a", "a", "b"], lines=["one"])

    asyncio.run(_previous_callers_screen(session, db, alice, lane=lane))

    assert len(list_oneliners(db)) == 1
    assert "You've added a line this call." in _visible(session)


def test_recent_callers_with_the_wall_fits_the_40x12_floor(db, lane, alice):
    for number in range(12):
        caller = create_user(db, f"caller{number:02d}", password="hunter2pw", user_level=10)
        _called(db, caller)
        _say(db, caller, "w" * 60)
    session = FakeSession(keys=["b"])
    session.terminal_width, session.terminal_height = 40, 12
    from netbbs.net.redraw_preference import set_redraw_in_place_enabled
    set_redraw_in_place_enabled(db, alice, True)

    asyncio.run(_previous_callers_screen(session, db, alice, lane=lane))

    [screen] = _screens(session)
    rows = screen.split("\n")
    assert all(len(row.rstrip("\r")) <= 40 for row in rows)
    assert len(screen.rstrip("\n").split("\n")) <= 12


# -- the full wall --------------------------------------------------------------


def test_an_empty_wall_says_so_and_offers_add(db, lane, alice):
    session = FakeSession(keys=["b"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    text = _visible(session)
    assert "Nobody has left a line yet." in text
    assert "[A]dd a line" in text and "[B]ack" in text
    assert "[D]elete" not in text


def test_the_wall_wraps_long_lines_rather_than_cutting_them(db, lane, alice, bob):
    _say(db, bob, "alpha bravo charlie delta echo foxtrot golf hotel juliet")
    session = FakeSession(keys=["b"])
    session.terminal_width = 40

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    text = _visible(session)
    assert "juliet" in text
    assert all(len(row.rstrip("\r")) <= 40 for row in text.split("\n"))


def test_the_wall_pages_with_the_angle_keys(db, lane, alice):
    for number in range(30):
        _say(db, create_user(db, f"c{number:02d}", password="hunter2pw", user_level=10), f"line number {number:02d}")
    session = FakeSession(keys=[">", "b"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    text = _visible(session)
    assert "page 1/" in text and "page 2/" in text
    assert "line number 29" in text and "line number 00" in text


def test_the_wall_finds_by_name_or_word(db, lane, alice, bob):
    _say(db, bob, "door games tonight")
    _say(db, alice, "hello world")
    session = FakeSession(keys=["/", "b"], lines=["door"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    last = _visible(session).split("matching")[-1]
    assert "door games tonight" in last and "hello world" not in last


def test_delete_is_offered_only_for_your_own_line_and_confirmed(db, lane, alice, bob):
    _say(db, bob, "bob's line")
    session = FakeSession(keys=["d", "b"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))
    assert "[D]elete" not in _visible(session)
    assert len(list_oneliners(db)) == 1

    _say(db, alice, "alice's line")
    session = FakeSession(keys=["d", "b"], lines=["01", "y"])
    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    assert [line.text for line in list_oneliners(db)] == ["bob's line"]
    assert "Line deleted." in _visible(session)


def test_an_author_cannot_delete_someone_elses_line_by_number(db, lane, alice, bob):
    _say(db, bob, "bob's line")
    _say(db, alice, "alice's line")
    session = FakeSession(keys=["d", "b"], lines=["02"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    assert len(list_oneliners(db)) == 2
    assert "You can delete only your own lines." in _visible(session)


def test_a_sysop_deletes_any_line_from_the_wall(db, lane, alice):
    sysop = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
    _say(db, alice, "spam spam spam")
    session = FakeSession(keys=["d", "b"], lines=["1", "y"])

    asyncio.run(oneliner_wall_screen(session, db, sysop, lane=lane))

    assert list_oneliners(db) == []
    assert [a.action for a in list_recent_actions(db, limit=5)][0] == "delete_oneliner"


def test_declining_the_delete_keeps_the_line(db, lane, alice):
    _say(db, alice, "keep me")
    session = FakeSession(keys=["d", "b"], lines=["1", "n"])

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    assert len(list_oneliners(db)) == 1


def test_the_wall_has_help(db, lane, alice):
    _say(db, alice, "hi")
    # The help overlay waits for a key of its own.
    session = FakeSession(keys=[HELP_KEY, " ", "b"])
    session.read_any_key = session.read_key
    session.discard_buffered_input = lambda: asyncio.sleep(0)

    asyncio.run(oneliner_wall_screen(session, db, alice, lane=lane))

    assert "Oneliners help" in _visible(session)


# -- the console ----------------------------------------------------------------


def test_the_console_toggles_the_wall_after_login_audited(db, lane):
    from netbbs.net.admin_flow import admin_menu
    from netbbs.oneliners import oneliners_enabled
    from tests.test_admin_flow import FakeSession as ConsoleSession, _visible as console_visible, _written_text

    sysop = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
    session = ConsoleSession(["s", "w", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop))

    assert oneliners_enabled(db) is False
    assert "all of oneliners: hidden" in console_visible(_written_text(session))
    [entry] = [a for a in list_recent_actions(db, limit=10) if a.action == "set_oneliners_enabled"]
    assert entry.detail == "enabled=false" and entry.actor_user_id == sysop.id


def test_the_oneliner_level_is_a_limits_setting(db, lane):
    from netbbs.config import get_oneliner_min_level
    from netbbs.net.admin_flow import admin_menu
    from tests.test_admin_flow import FakeSession as ConsoleSession

    sysop = create_user(db, "root", password="hunter2pw", user_level=SYSOP_LEVEL)
    # s: Settings, l: Limits & retention, 08: oneliner level, 30, s: save.
    session = ConsoleSession(["s", "l", "0", "8", "30", "s", "b", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop))

    assert get_oneliner_min_level(db) == 30
    audit = [a for a in list_recent_actions(db, limit=10) if a.action == "set_limits_and_retention"]
    assert len(audit) == 1 and "oneliner_level=30" in audit[0].detail
