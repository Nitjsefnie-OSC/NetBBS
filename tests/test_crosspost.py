"""
Cross-posting: one new post on several message boards (issue #1298, design
doc §6.1, §6.6, §6.8 and §9.2).

One ordinary copy per board, tied by `posts.crosspost_group`, written all or
nothing; each copy leaves the node the way a post on its board does (a Linked
copy with a `"crosspost"` field, an FTN copy with its own MSGID and a
`* Crossposted in` line); and a reader meets the group once. Real SQLite
files throughout; the Link half runs between two harness nodes.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.activity import (
    ensure_board_baseline,
    record_post_opened,
    unread_crosspost_groups,
    unread_post_count,
)
from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.boards import posts as posts_module
from netbbs.boards.boards import create_board
from netbbs.boards.posts import (
    CrosspostRefused,
    approve_post,
    create_crosspost,
    create_post,
    crosspost_group_of,
    edit_post,
    get_post,
    visible_crosspost_copies,
)
from netbbs.config import get_crosspost_board_limit, set_crosspost_board_limit
from netbbs.file_refs import post_refs, ref_for_entry
from netbbs.files.areas import create_file_area
from netbbs.files.entries import upload_file
from netbbs.ftn import queue
from netbbs.ftn.address import FtnAddress
from netbbs.ftn.message import decode_message
from netbbs.ftn.networks import FtnNetwork, save_network, set_board_area
from netbbs.ftn.packet import PacketHeader, build_packet_from_packed, parse_packet
from netbbs.ftn.scanner import export_post_if_ftn
from netbbs.link.boards import (
    close_board_if_linked,
    link_board,
    materialize_carried_board,
    materialize_carried_post,
    queue_board_post_if_linked,
)
from netbbs.link.events import BoardPost, build_board_post
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import LinkNode
from netbbs.net import board_flow
from netbbs.net.crosspost_flow import crosspost_refusal
from netbbs.net.redraw_preference import set_redraw_in_place_enabled
from netbbs.search import search_posts
from netbbs.storage.database import Database
from tests.link_harness import FakeClock, spawn_node
from tests.test_board_list_and_reader import FakeSession


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _distinct_times(monkeypatch):
    """A fresh time for every post: two posts with the same text in the same
    clock tick (15 ms on Windows) would get the same content id."""
    ticks = iter(range(1, 100_000))
    monkeypatch.setattr(posts_module, "utc_now_iso", lambda: f"2026-10-15T10:00:00.{next(ticks):06d}Z")


def _user(db, name, **kwargs):
    kwargs.setdefault("user_level", 10)
    user = create_user(db, name, password="hunter2pw", **kwargs)
    set_redraw_in_place_enabled(db, user, True)
    return user


@pytest.fixture
def sysop(db):
    return _user(db, "sysop", user_level=SYSOP_LEVEL)


@pytest.fixture
def alice(db):
    return _user(db, "alice")


def _roots(db):
    return db.connection.execute(
        "SELECT post_id, board_id, crosspost_group, status FROM posts WHERE post_id = root_post_id ORDER BY id"
    ).fetchall()


# -- the copies ---------------------------------------------------------------


def test_one_copy_per_board_tied_by_one_group_each_with_its_own_files(db, sysop, alice):
    critique = create_board(db, "Critique", creator=sysop)
    showcase = create_board(db, "Showcase", creator=sysop)
    area = create_file_area(db, "Pages", creator=alice)
    ref = ref_for_entry(upload_file(db, area, alice, "page.png", b"payload"), area)

    copies = create_crosspost(db, [critique, showcase], alice, "My page", "What do you think?", files=[ref])

    assert [copy.board_id for copy in copies] == [critique.id, showcase.id]
    assert copies[0].post_id != copies[1].post_id  # the board is part of the content id
    group = crosspost_group_of(db, copies[0])
    assert group is not None and "@" not in group
    assert crosspost_group_of(db, copies[1]) == group
    assert all(post_refs(db, copy.post_id) == [ref] for copy in copies)
    assert all(copy.status == "approved" for copy in copies)


def test_an_ordinary_post_has_no_group(db, sysop, alice):
    board = create_board(db, "Critique", creator=sysop)
    post = create_post(db, board, alice, "s", "b")
    assert crosspost_group_of(db, post) is None
    assert visible_crosspost_copies(db, alice, post) == []


@pytest.mark.parametrize("refuse", ["level", "closed", "hidden", "callback", "art"])
def test_one_refusing_board_writes_nothing_and_is_named(db, sysop, alice, refuse):
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop, allow_color=True)
    third = create_board(
        db, "Gallery", creator=sysop, allow_color=True, min_write_level=50 if refuse == "level" else 0,
    )
    callback = None
    layout = "prose"
    if refuse == "level":
        pass
    elif refuse == "closed":
        db.connection.execute("UPDATE boards SET link_closed_at = '2026-01-01T00:00:00Z' WHERE id = ?", (third.id,))
        db.connection.commit()
    elif refuse == "hidden":
        db.connection.execute("UPDATE boards SET link_hidden_at = '2026-01-01T00:00:00Z' WHERE id = ?", (third.id,))
        db.connection.commit()
    elif refuse == "callback":
        callback = lambda board: "only the origin posts here" if board.id == third.id else None  # noqa: E731
    else:
        # An art post goes only to boards with color: Critique has none.
        layout = "art"
        third = first

    boards = [second, third] if refuse == "art" else [first, second, third]
    with pytest.raises(CrosspostRefused) as raised:
        create_crosspost(db, boards, alice, "s", "b", layout=layout, refusal_of=callback)

    assert raised.value.board.id == third.id
    assert str(raised.value).startswith(f"{third.name}: ")
    assert _roots(db) == []


def test_a_moderated_board_holds_its_copy_while_the_others_go_up(db, sysop, alice):
    open_board = create_board(db, "Critique", creator=sysop)
    held_board = create_board(db, "Showcase", creator=sysop, moderated=True)

    copies = create_crosspost(db, [open_board, held_board], alice, "s", "b")

    assert [copy.status for copy in copies] == ["approved", "pending"]
    # The held copy is not shown on its board, nor named by the other's byline.
    assert visible_crosspost_copies(db, alice, copies[0]) == []
    approve_post(db, copies[1], approved_by=sysop)
    assert [board.name for board, _copy in visible_crosspost_copies(db, alice, copies[0])] == ["Showcase"]


def test_an_edit_changes_one_copy(db, sysop, alice):
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    one, two = create_crosspost(db, [first, second], alice, "s", "original")

    edit_post(db, one, first, subject="s", body="changed here", edited_by=alice)

    (_board, shown_two), = visible_crosspost_copies(db, alice, one)
    assert shown_two.body == "original"
    assert get_post(db, two.post_id).body == "original"


# -- the cap ------------------------------------------------------------------


def test_the_cap_defaults_to_five_and_is_bounded(db):
    assert get_crosspost_board_limit(db) == 5
    set_crosspost_board_limit(db, 2)
    assert get_crosspost_board_limit(db) == 2
    with pytest.raises(ValueError):
        set_crosspost_board_limit(db, 0)
    with pytest.raises(ValueError):
        set_crosspost_board_limit(db, 21)


# -- Link ---------------------------------------------------------------------


def test_a_linked_copy_carries_the_group_and_an_ordinary_post_omits_it(db, sysop, alice):
    identity = bootstrap_node_identity("roanoke")
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    link_board(db, first, node_identity=identity)
    one, _two = create_crosspost(db, [first, second], alice, "s", "b")
    plain = create_post(db, first, alice, "plain", "b")

    event = queue_board_post_if_linked(db, one, first, node_identity=identity)

    assert event.payload["crosspost"] == crosspost_group_of(db, one)
    assert "crosspost" not in queue_board_post_if_linked(db, plain, first, node_identity=identity).payload


def test_a_carrying_node_ties_the_copies_of_one_group_under_the_authors_node(tmp_path):
    home, here = spawn_node(tmp_path, "home"), spawn_node(tmp_path, "here")
    clock = FakeClock()
    sysop = create_user(home.db, "sysop", password="hunter2pw", user_level=SYSOP_LEVEL)
    author = create_user(home.db, "alice", password="hunter2pw", user_level=10)
    boards = [create_board(home.db, name, creator=sysop) for name in ("Critique", "Showcase")]
    geneses = [link_board(home.db, board, node_identity=home.identity) for board in boards]
    copies = create_crosspost(home.db, boards, author, "My page", "What do you think?")
    events = [
        queue_board_post_if_linked(home.db, copy, board, node_identity=home.identity)
        for copy, board in zip(copies, boards)
    ]
    group = crosspost_group_of(home.db, copies[0])

    # The receiving protocol layer takes the optional field as it is: no bump.
    receiver = LinkNode(identity=here.identity)
    receiver.handle_hello(LinkNode(identity=home.identity).build_hello(
        addresses=None, outgoing_only=True, created_at=clock.now_iso(),
    ))
    accepted = receiver.handle_events(home.fingerprint, [e.to_dict() for e in [*geneses, *events]])
    assert set(accepted) == {e.content_id for e in [*geneses, *events]}

    # And the node carrying both boards ties the two copies together.
    for genesis in geneses:
        materialize_carried_board(here.db, genesis)
    carried = [
        materialize_carried_post(here.db, BoardPost.from_dict(event.to_dict()), sender_fingerprint=home.fingerprint)
        for event in events
    ]
    assert {crosspost_group_of(here.db, post) for post in carried} == {f"{group}@{home.fingerprint}"}
    reader = create_user(here.db, "bob", password="hunter2pw", user_level=10)
    (board, copy), = visible_crosspost_copies(here.db, reader, carried[0])
    assert board.name == "Showcase" and copy.post_id == carried[1].post_id

    home.close()
    here.close()


def test_another_nodes_group_with_the_same_id_stays_apart_and_replies_and_bad_ids_have_none(db):
    first, second = bootstrap_node_identity("first"), bootstrap_node_identity("second")
    from netbbs.link.events import build_board_genesis

    genesis = build_board_genesis(
        signing_identity=first.signing_key, origin_fingerprint=first.fingerprint,
        board_id="board-id", name="General", created_at="2026-01-01T00:00:00Z",
    )
    materialize_carried_board(db, genesis)

    def post(author, subject, **kwargs):
        event = build_board_post(
            signing_identity=author.signing_key, home_node_fingerprint=author.fingerprint,
            local_user_id="someone", board_id="board-id", subject=subject, body="b",
            created_at="2026-01-02T00:00:00Z", **kwargs,
        )
        return materialize_carried_post(db, event, sender_fingerprint=author.fingerprint)

    mine = post(first, "one", crosspost="abc123")
    theirs = post(second, "two", crosspost="abc123")
    reply = post(second, "three", crosspost="abc123", parent_post_id=mine.post_id)
    odd = post(second, "four", crosspost="not a group id!")

    assert crosspost_group_of(db, mine) == f"abc123@{first.fingerprint}"
    assert crosspost_group_of(db, theirs) == f"abc123@{second.fingerprint}"
    assert crosspost_group_of(db, reply) is None
    assert crosspost_group_of(db, odd) is None


# -- FTN ----------------------------------------------------------------------

OURS = FtnAddress(21, 1, 199)
UPLINK = FtnAddress(21, 1, 100)


def _network(db, name, domain, ours=OURS, uplink=UPLINK):
    return save_network(db, FtnNetwork(
        name=name, domain=domain, our_address=ours, uplink_address=uplink, uplink_host="hub.example",
        enabled=True, origin_text="Test BBS",
    ))


def test_each_ftn_copy_has_its_own_msgid_and_names_only_its_networks_other_echoes(db, sysop, alice):
    fsx = _network(db, "fsxNet", "fsxnet")
    other = _network(db, "Other", "othernet", ours=FtnAddress(9, 9, 9), uplink=FtnAddress(9, 9, 1))
    general = create_board(db, "fsx general", creator=sysop)
    set_board_area(db, general, fsx.id, "FSX_GEN")
    bbs = create_board(db, "fsx bbs", creator=sysop)
    set_board_area(db, bbs, fsx.id, "FSX_BBS")
    elsewhere = create_board(db, "other general", creator=sysop)
    set_board_area(db, elsewhere, other.id, "OTH_GEN")
    local = create_board(db, "Local chatter", creator=sysop)

    boards = [general, bbs, elsewhere, local]
    copies = create_crosspost(db, boards, alice, "Hello", "First line of the post.")
    for copy, board in zip(copies, boards):
        export_post_if_ftn(db, copy, board)

    fsx_messages = {m.area: m for m in _decoded(db, fsx)}
    other_messages = {m.area: m for m in _decoded(db, other)}
    assert fsx_messages["FSX_GEN"].body == "* Crossposted in FSX_BBS\nFirst line of the post."
    assert fsx_messages["FSX_BBS"].body == "* Crossposted in FSX_GEN\nFirst line of the post."
    # Alone on its network: no line, and no local or other network's name.
    assert other_messages["OTH_GEN"].body == "First line of the post."
    msgids = {m.msgid for m in [*fsx_messages.values(), *other_messages.values()]}
    assert len(msgids) == 3


def test_an_ordinary_ftn_post_has_no_crossposted_line(db, sysop, alice):
    fsx = _network(db, "fsxNet", "fsxnet")
    general = create_board(db, "fsx general", creator=sysop)
    set_board_area(db, general, fsx.id, "FSX_GEN")
    export_post_if_ftn(db, create_post(db, general, alice, "Hello", "Just here."), general)
    (message,) = _decoded(db, fsx)
    assert message.body == "Just here."


def _decoded(db, network):
    header = PacketHeader(orig=OURS, dest=UPLINK, created=None)
    return [
        decode_message(parse_packet(build_packet_from_packed(header, [item.packed])).messages[0])
        for item in queue.pending_outbound(db, network.id)
    ]


# -- reading ------------------------------------------------------------------


def test_opening_one_copy_marks_the_copies_the_reader_can_see_read(db, sysop, alice):
    bob = _user(db, "bob")
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    closed_to_bob = create_board(db, "Staff", creator=sysop, min_read_level=50)
    for board in (first, second, closed_to_bob):
        ensure_board_baseline(db, bob, board)
    one, _two, _three = create_crosspost(db, [first, second, closed_to_bob], sysop, "s", "b")
    assert unread_post_count(db, bob, first) == 1 and unread_post_count(db, bob, second) == 1

    record_post_opened(db, bob, first, one)

    assert unread_post_count(db, bob, first) == 0
    assert unread_post_count(db, bob, second) == 0
    # Bob cannot read Staff; nothing is recorded for him there.
    assert unread_post_count(db, bob, closed_to_bob) == 1


def test_new_scan_counts_a_group_once(db, sysop, alice, tmp_path):
    from netbbs.chat.hub import ChatHub
    from netbbs.chat.mailbox import MessageMailbox
    from netbbs.chat.presence import PresenceRegistry
    from netbbs.net.char_input import InputHistory
    from netbbs.net.main_menu import _main_menu
    from netbbs.storage.execution import DatabaseLane
    from tests.test_new_scan import FakeSession as ScanSession

    bob = create_user(db, "bob", password="hunter2pw", user_level=10)
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    for board in (first, second):
        ensure_board_baseline(db, bob, board)
    create_crosspost(db, [first, second], alice, "s", "b")
    create_post(db, second, alice, "only here", "b")
    assert unread_crosspost_groups(db, bob, first) == unread_crosspost_groups(db, bob, second) != set()

    lane = DatabaseLane(db.path)
    try:
        session = ScanSession(["n", "b", "l", "y"])
        asyncio.run(_main_menu(
            session, db, ChatHub(), PresenceRegistry(), MessageMailbox(), InputHistory(), bob, lane=lane,
        ))
    finally:
        lane.close()

    text = "".join(session.written)
    # Critique counts the cross-post; Showcase only its own post.
    assert text.count("1 unread") == 2
    assert "2 unread" not in text


def test_search_lists_a_group_once_by_a_copy_the_caller_can_open(db, sysop, alice):
    bob = _user(db, "bob")
    staff = create_board(db, "Staff", creator=sysop, min_read_level=50)
    showcase = create_board(db, "Showcase", creator=sysop)
    create_crosspost(db, [staff, showcase], sysop, "Quokka sighting", "A quokka on the lawn")

    hits = search_posts(db, bob, "quokka")
    assert [hit.board.name for hit in hits] == ["Showcase"]
    assert len(search_posts(db, sysop, "quokka")) == 1


def test_the_byline_names_only_the_boards_the_reader_can_open(db, sysop, alice):
    bob = _user(db, "bob")
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    staff = create_board(db, "Staff", creator=sysop, min_read_level=50)
    create_crosspost(db, [first, second, staff], sysop, "Hello all", "Body")

    session = FakeSession(["0", "1", "b", "b"], width=120)
    asyncio.run(board_flow._show_board(session, db, first, bob))

    text = session.visible()
    assert "also in Showcase" in text
    assert "Staff" not in text


# -- [T]o boards on the review screen -----------------------------------------


def test_to_boards_posts_one_copy_on_each_ticked_board(db, sysop, alice):
    critique = create_board(db, "Critique", creator=sysop)
    create_board(db, "Showcase", creator=sysop)
    create_board(db, "Members", creator=sysop, min_write_level=50)
    # [P]ost, subject, a line, /done; [T]o boards, tick 02 (Showcase), Back;
    # [P]ost; Back out of the board.
    session = FakeSession(
        ["p", "My page", "What do you think?", "/done", "t", "0", "2", "b", "p", "b"], width=120,
    )

    asyncio.run(board_flow._show_board(session, db, critique, alice))

    text = session.visible()
    assert "[T]o boards" in text
    assert "posting needs level 50" in text  # Members is listed, muted, with why
    assert "Boards: Critique, Showcase" in text
    assert "Posted to Critique, Showcase." in text
    roots = _roots(db)
    assert len(roots) == 2 and roots[0]["crosspost_group"] == roots[1]["crosspost_group"] is not None


def test_the_picker_refuses_a_tick_past_the_cap(db, sysop, alice):
    set_crosspost_board_limit(db, 2)
    critique = create_board(db, "Critique", creator=sysop)
    create_board(db, "Showcase", creator=sysop)
    create_board(db, "Gallery", creator=sysop)
    session = FakeSession(
        ["p", "My page", "Body", "/done", "t", "0", "2", "0", "3", "b", "p", "b"], width=120,
    )

    asyncio.run(board_flow._show_board(session, db, critique, alice))

    assert "A post can go to 2 message boards at most, this one included." in session.visible()
    assert len(_roots(db)) == 2


def test_a_refused_board_at_post_is_named_and_nothing_is_posted(db, sysop, alice):
    critique = create_board(db, "Critique", creator=sysop)
    showcase = create_board(db, "Showcase", creator=sysop)
    session = FakeSession(
        ["p", "My page", "Body", "/done", "t", "0", "2", "b", "p", "b", "y", "b"], width=120,
    )
    real_create = posts_module.create_crosspost

    def close_showcase_first(db_, boards, *args, **kwargs):
        db_.connection.execute("UPDATE boards SET link_closed_at = 'x' WHERE id = ?", (showcase.id,))
        db_.connection.commit()
        return real_create(db_, boards, *args, **kwargs)

    import netbbs.net.crosspost_flow as crosspost_flow

    original = crosspost_flow.create_crosspost
    crosspost_flow.create_crosspost = close_showcase_first
    try:
        asyncio.run(board_flow._show_board(session, db, critique, alice))
    finally:
        crosspost_flow.create_crosspost = original

    assert "Nothing was posted: Showcase refused it" in session.visible()
    assert _roots(db) == []


def test_a_reply_has_no_to_boards(db, sysop, alice):
    critique = create_board(db, "Critique", creator=sysop)
    create_board(db, "Showcase", creator=sysop)
    create_post(db, critique, sysop, "Lunch?", "Anyone?")
    session = FakeSession(["0", "1", "r", "", "Count me in.", "/done", "p", "b", "b"], width=120)

    asyncio.run(board_flow._show_board(session, db, critique, alice))

    assert "Posted." in session.visible()  # the reply's review was reached
    assert "[T]o boards" not in session.visible()


def test_to_boards_is_not_offered_when_the_cap_is_one(db, sysop, alice):
    set_crosspost_board_limit(db, 1)
    critique = create_board(db, "Critique", creator=sysop)
    create_board(db, "Showcase", creator=sysop)
    session = FakeSession(["p", "My page", "Body", "/done", "p", "b"], width=120)

    asyncio.run(board_flow._show_board(session, db, critique, alice))

    assert "[T]o boards" not in session.visible()
    assert len(_roots(db)) == 1


def test_an_art_post_cannot_go_to_a_board_without_color(db, sysop, alice):
    plain = create_board(db, "Plain", creator=sysop)
    colorful = create_board(db, "Colorful", creator=sysop, allow_color=True)
    assert crosspost_refusal(db, alice, plain, layout="art", own_fingerprint=None) == "no color here, so no art posts"
    assert crosspost_refusal(db, alice, colorful, layout="art", own_fingerprint=None) is None
    assert crosspost_refusal(db, alice, plain, layout="prose", own_fingerprint=None) is None


def test_a_closed_linked_board_is_refused_in_the_picker(db, sysop, alice):
    identity = bootstrap_node_identity("roanoke")
    board = create_board(db, "Archive", creator=sysop)
    link_board(db, board, node_identity=identity)
    close_board_if_linked(db, board, node_identity=identity)
    assert crosspost_refusal(db, alice, board, layout="prose", own_fingerprint=identity.fingerprint) == (
        "closed to new posts"
    )


def test_the_sysop_sets_the_cap_beside_the_other_node_wide_limits(db, sysop):
    from netbbs.net.admin_flow import admin_menu
    from netbbs.storage.execution import DatabaseLane
    from tests.test_admin_flow import FakeSession as ConsoleSession, _visible, _written_text

    lane = DatabaseLane(db.path)
    try:
        # Settings, Limits & retention, field 07, 3, Save, back out.
        session = ConsoleSession(["s", "l", "0", "7", "3", "s", "b", "b", "b"])
        asyncio.run(admin_menu(session, lane, sysop))
    finally:
        lane.close()

    text = _visible(_written_text(session))
    assert "Cross-post boards" in text and "up to 5 boards" in text
    assert get_crosspost_board_limit(db) == 3


def test_opening_a_copy_does_not_visit_a_board_never_visited(db, sysop, alice):
    bob = _user(db, "bob")
    first = create_board(db, "Critique", creator=sysop)
    second = create_board(db, "Showcase", creator=sysop)
    create_post(db, second, sysop, "older backlog", "b")
    ensure_board_baseline(db, bob, first)
    one, _two = create_crosspost(db, [first, second], sysop, "s", "b")

    record_post_opened(db, bob, first, one)

    # Showcase stays "not visited yet": its backlog was never shown to bob.
    assert unread_post_count(db, bob, second) is None
