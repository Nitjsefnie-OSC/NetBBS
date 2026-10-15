"""
Polls on the board's screens (issue #1299, built by #1332): `[N]ew poll` on the
post list, the poll block and `[V]ote` on the reader, `[C]lose poll`, and
editing a poll. Driven through the board reader's `FakeSession`.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import create_user
from netbbs.boards import posts as posts_module
from netbbs.boards.boards import create_board
from netbbs.boards.polls import Poll, PollSpec, PollTally, cast_vote, default_closes_at, get_poll, tally, user_vote
from netbbs.boards.posts import create_post
from netbbs.ftn.address import FtnAddress
from netbbs.ftn.networks import FtnNetwork, save_network, set_board_area
from netbbs.net import board_flow, poll_view
from netbbs.net.redraw_preference import set_redraw_in_place_enabled
from netbbs.rendering.width import display_width
from netbbs.storage.database import Database
from tests.test_board_list_and_reader import FakeSession, _SGR


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _distinct_times(monkeypatch):
    ticks = iter(range(1, 100_000))
    monkeypatch.setattr(posts_module, "utc_now_iso", lambda: f"2026-10-15T10:00:00.{next(ticks):06d}Z")


def _user(db, name, level=10):
    user = create_user(db, name, password="hunter2pw", user_level=level)
    set_redraw_in_place_enabled(db, user, True)
    return user


def _poll(db, board, author, *options, max_choices=1, results="after_vote"):
    spec = PollSpec(tuple(options or ("vi", "emacs")), max_choices, default_closes_at(7), results)
    post = create_post(db, board, author, "Best editor?", "Pick one.", poll=spec)
    return post, get_poll(db, post.root_post_id)


def _run(db, board, user, inputs, *, width=80, height=24, session=None):
    session = session or FakeSession(inputs, width=width, height=height)
    asyncio.run(board_flow._show_board(session, db, board, user))
    return session


def _reader(session) -> str:
    """The last reader screen the session drew (it has the post's byline)."""
    return [screen for screen in session.screens() if "Pick one." in screen][-1]


# -- creating -----------------------------------------------------------------


def test_poll_on_the_post_list_asks_a_poll_and_posts_it(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    # [N]ew poll; the question and two answers by number; [N]ext; the body;
    # [P]ost on review; Back.
    session = _run(db, board, alice, [
        "n", "0", "1", "Best editor?", "0", "2", "vi", "0", "3", "emacs", "n",
        "Pick one.", "/done", "p", "b",
    ])

    post_id = db.connection.execute("SELECT root_post_id FROM posts").fetchone()[0]
    poll = get_poll(db, post_id)
    assert poll is not None and poll.options == ("vi", "emacs") and poll.max_choices == 1
    review = next(screen for screen in session.screens() if "Review composition" in screen)
    assert "Poll:" in review and "vi" in review and "[Q]uestion and answers" in review
    assert "Poll posted." in session.visible()
    assert "[N]ew poll" in session.screens()[0]


def test_backing_out_of_the_poll_editor_writes_nothing(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    create_post(db, board, alice, "Hello", "x")

    session = _run(db, board, alice, ["n", "0", "1", "Unasked", "b", "y", "b"])

    assert db.connection.execute("SELECT COUNT(*) FROM board_polls").fetchone()[0] == 0
    assert db.connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert "Poll cancelled." in session.visible()


def test_a_poll_needs_two_answers_before_next(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    create_post(db, board, alice, "Hello", "x")

    session = _run(db, board, alice, ["n", "0", "1", "Q?", "0", "2", "only", "n", "b", "y", "b"])

    assert "Could not save: a poll needs at least 2 answers" in session.visible()
    assert db.connection.execute("SELECT COUNT(*) FROM board_polls").fetchone()[0] == 0


def test_no_poll_on_an_ftn_board(db):
    sysop = _user(db, "sysop", 255)
    network = save_network(db, FtnNetwork(
        name="fsxNet", domain="fsxnet", our_address=FtnAddress(21, 1, 999),
        uplink_address=FtnAddress(21, 1, 100), uplink_host="hub.example", session_password="SECRET", enabled=True,
    ))
    board = create_board(db, "general", creator=sysop)
    set_board_area(db, board, network.id, "FSX_GEN")
    create_post(db, board, sysop, "Hello", "x")

    session = _run(db, board, sysop, ["n", "b"])

    assert "[N]ew poll" not in session.screens()[0]
    assert db.connection.execute("SELECT COUNT(*) FROM board_polls").fetchone()[0] == 0


# -- voting and results ------------------------------------------------------------


def test_results_show_once_the_caller_votes(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    cast_vote(db, poll, board, alice, [0])

    # Open the poll, [V]ote, pick answer 2, [V]ote; Back twice.
    session = _run(db, board, bob, ["0", "1", "v", "0", "2", "v", "b", "b"])

    before, after = [s for s in session.screens() if "Pick one." in s][0], _reader(session)
    assert "Results show once you vote." in before and "voter" not in before
    assert "2 voters." in after and "50%" in after
    assert "Voted: emacs." in after
    assert user_vote(db, poll, bob) == (1,)


def test_no_screen_names_who_voted_what(db):
    alice, bob = _user(db, "alice"), _user(db, "zebediah")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    cast_vote(db, poll, board, bob, [1])

    session = _run(db, board, alice, ["0", "1", "v", "0", "1", "v", "b", "b"])

    assert "zebediah" not in session.visible()
    assert "2 voters." in _reader(session)


def test_a_caller_who_cannot_vote_sees_the_results_at_once(db):
    sysop, reader = _user(db, "sysop", 255), _user(db, "reader", 5)
    board = create_board(db, "members", creator=sysop, min_write_level=10)
    _post, poll = _poll(db, board, sysop)
    cast_vote(db, poll, board, sysop, [0])

    session = _run(db, board, reader, ["0", "1", "b", "b"])

    screen = _reader(session)
    assert "1 voter." in screen and "100%" in screen
    assert "[V]ote" not in screen


def test_results_only_after_close_stay_hidden_after_voting(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice, results="after_close")

    session = _run(db, board, bob, ["0", "1", "v", "0", "1", "v", "b", "b"])

    screen = _reader(session)
    assert "Results show when the poll closes" in screen and "Your vote: vi." in screen
    assert "voter" not in screen


def test_up_to_n_answers_tick_and_untick(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice, "a", "b", "c", max_choices=2)

    _run(db, board, bob, ["0", "1", "v", "0", "1", "0", "3", "0", "1", "0", "2", "v", "b", "b"])

    assert user_vote(db, poll, bob) == (1, 2)


def test_too_many_answers_keeps_the_vote_draft(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice, "a", "b", "c", max_choices=2)

    session = _run(db, board, bob, ["0", "1", "v", "0", "1", "0", "2", "0", "3", "v", "0", "3", "v", "b", "b"])

    assert "Could not save: pick at most 2 answers" in session.visible()
    assert user_vote(db, poll, bob) == (0, 1)


def test_a_guest_call_cannot_change_an_earlier_guests_vote(db):
    alice, guest = _user(db, "alice"), _user(db, "guest")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    cast_vote(db, poll, board, guest, [0])  # an earlier call's
    session = FakeSession(["0", "1", "v", "b", "b"])
    session.authenticated_without_credential = True

    _run(db, board, guest, None, session=session)

    assert "can change only what it wrote during this call" in " ".join(session.visible().split())
    assert user_vote(db, poll, guest) == (0,)


def test_a_guest_call_may_change_its_own_vote(db):
    alice, guest = _user(db, "alice"), _user(db, "guest")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    session = FakeSession(["0", "1", "v", "0", "1", "v", "v", "0", "2", "v", "b", "b"])
    session.authenticated_without_credential = True

    _run(db, board, guest, None, session=session)

    assert user_vote(db, poll, guest) == (1,)


# -- closing and editing -----------------------------------------------------------


def test_the_author_closes_the_poll(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    cast_vote(db, poll, board, bob, [1])

    session = _run(db, board, alice, ["0", "1", "c", "y", "b", "b"])

    screen = _reader(session)
    assert "Poll closed." in screen and "Closed " in screen
    assert "[V]ote" not in screen and "[C]lose poll" not in screen
    assert "1 voter." in screen
    assert get_poll(db, poll.root_post_id).closed_at is not None


def test_only_the_author_and_moderators_are_offered_close(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _poll(db, board, alice)

    session = _run(db, board, bob, ["0", "1", "b", "b"])

    assert "[C]lose poll" not in _reader(session)


def test_editing_a_voted_poll_changes_its_closing_date_and_not_its_answers(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll(db, board, alice)
    cast_vote(db, poll, board, bob, [0])

    # [E]dit: answer 1 is locked; Closes (13) to 14 days; [N]ext; keep the
    # body; [S]ave.
    session = _run(db, board, alice, ["0", "1", "e", "0", "2", "1", "3", "14", "n", "/done", "s", "b", "b"])

    assert "fixed once someone has voted" in session.visible()
    stored = get_poll(db, poll.root_post_id)
    assert stored.options == ("vi", "emacs")
    assert poll_view._days_until(stored.closes_at) == 14
    assert "Poll updated." in session.visible()
    assert tally(db, stored).voters == 1


# -- how it draws -------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), [(40, 12), (80, 24)])
def test_a_ten_answer_poll_fits_the_terminal_width(db, width, height):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    options = [f"Answer number {i} with a fairly long label to wrap" for i in range(10)]
    _post, poll = _poll(db, board, alice, *options, max_choices=3)
    cast_vote(db, poll, board, bob, [0, 4, 9])

    session = _run(db, board, alice, ["0", "1", ">", ">", ">", ">", ">", ">", "b", "b"], width=width, height=height)

    for line in "".join(session.written).replace("\r\n", "\n").split("\n"):
        assert display_width(_SGR.sub("", line).split("\x1b")[0]) <= width


def test_bars_are_one_row_wide_and_two_rows_narrow_and_ascii_without_unicode_style():
    poll = Poll(
        root_post_id="x", options=("Yes", "No"), max_choices=1, closes_at=default_closes_at(), results="after_vote",
    )
    counts = PollTally((3, 1), 4)

    wide = poll_view.result_rows(poll, counts, width=80, unicode_style=True, accent=33, mine=(0,))
    narrow = poll_view.result_rows(poll, counts, width=40, unicode_style=False, accent=33)

    plain_wide = [_SGR.sub("", row) for row in wide]
    plain_narrow = [_SGR.sub("", row) for row in narrow]
    assert len(plain_wide) == 2 and plain_wide[0].startswith("✓ Yes") and plain_wide[0].endswith("3   75%")
    assert "█" in plain_wide[0] and "░" in plain_wide[0]
    assert len(plain_narrow) == 4 and plain_narrow[0] == "  Yes"
    assert set(plain_narrow[1].split()[0]) <= {"#", "."} and plain_narrow[1].endswith("3   75%")
    assert all(display_width(row) <= 79 for row in plain_wide) and all(display_width(r) <= 39 for r in plain_narrow)
