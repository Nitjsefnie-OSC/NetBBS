"""
Polls on Linked boards (design doc §16, issue #1299 Decision 4; built by
#1332): the poll travels in its `board_post`, its closing date in edits of
the post, and votes as signed `board_poll_vote` events -- one per author
identity, the latest winning, trust-filtered and capped per home node.

Driven through `tests/link_harness.py`'s nodes and `LinkNode.handle_events`,
with real SQLite on every node, as `tests/test_link_convergence.py` is.
"""

from __future__ import annotations

import asyncio

import pytest

from netbbs.auth.users import create_user
from netbbs.boards import polls as polls_module
from netbbs.boards import posts as posts_module
from netbbs.boards.boards import create_board
from netbbs.boards.polls import (
    PollError,
    PollSpec,
    answers_locked,
    cast_vote,
    close_poll,
    get_poll,
    poll_closed,
    poll_hidden,
    tally,
    update_poll,
)
from netbbs.boards.posts import create_post, edit_post, withdraw_post
from netbbs.link import polls as link_polls
from netbbs.link.boards import (
    _board_from_row,
    load_own_board_events,
    link_board,
    materialize_carried_board,
    materialize_carried_post,
    materialize_carried_post_edit,
    queue_board_post_edit_if_linked,
    queue_board_post_if_linked,
    set_board_posting,
)
from netbbs.link.events import BoardPostEdit, BoardPollVote, build_board_poll_vote
from netbbs.link.polls import materialize_carried_board_poll_vote, queue_board_poll_vote_if_linked
from netbbs.link.protocol import LinkNode, LinkProtocolError
from netbbs.link.store import _all_board_events, board_event_diff, load_link_node
from netbbs.link.transport import persist_accepted_events
from netbbs.link.trust import TrustDimension, TrustState, TrustSubject, register_subject, set_trust_override
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import utc_now_iso
from tests.link_harness import FakeClock, ScriptedTransport, spawn_node
from tests.test_link_convergence import _exchange_hellos

_FAR = "2099-01-01T00:00:00.000000Z"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(autouse=True)
def _distinct_times(monkeypatch):
    ticks = iter(range(1, 100_000))
    monkeypatch.setattr(posts_module, "utc_now_iso", lambda: f"2026-10-15T10:00:00.{next(ticks):06d}Z")


class _Net:
    """An origin carrying a Linked board with one poll, and a node that
    carries it, each with its own database and LinkNode."""

    def __init__(self, tmp_path, clock, *, options=("vi", "emacs"), max_choices=1, third=False):
        self.transport = ScriptedTransport()
        self.origin, self.carrier = spawn_node(tmp_path, "origin"), spawn_node(tmp_path, "carrier")
        names = [self.origin, self.carrier]
        if third:
            self.third = spawn_node(tmp_path, "third")
            names.append(self.third)
        self.nodes = {}
        for node in names:
            self.transport.register(node)
            self.nodes[node.label] = LinkNode(identity=node.identity)
        _exchange_hellos(self.transport, self.origin, self.o, self.carrier, self.c, clock)
        if third:
            _exchange_hellos(self.transport, self.origin, self.o, self.third, self.t, clock)
            _exchange_hellos(self.transport, self.carrier, self.c, self.third, self.t, clock)

        self.alice = create_user(self.origin.db, "alice", password="hunter2pw", user_level=10)
        self.board = create_board(self.origin.db, "polls", creator=self.alice)
        self.genesis = link_board(self.origin.db, self.board, node_identity=self.origin.identity)
        self.board = _board_from_row(
            self.origin.db.connection.execute("SELECT * FROM boards WHERE id = ?", (self.board.id,)).fetchone()
        )
        spec = PollSpec(tuple(options), max_choices, polls_module.default_closes_at(7), "after_vote")
        self.post = create_post(self.origin.db, self.board, self.alice, "Best editor?", "Pick.", poll=spec)
        self.event = queue_board_post_if_linked(
            self.origin.db, self.post, self.board, node_identity=self.origin.identity,
        )
        self.poll = get_poll(self.origin.db, self.post.root_post_id)
        # As `load_link_node` does for the origin's own board.
        self.o.board_events.record_genesis(self.genesis)
        # The carrier takes the board and the poll's post.
        accepted = self.c.handle_events(self.origin.fingerprint, [self.genesis.to_dict(), self.event.to_dict()])
        assert accepted == [self.genesis.content_id, self.event.content_id]
        materialize_carried_board(self.carrier.db, self.genesis, own_fingerprint=self.carrier.fingerprint)
        materialize_carried_post(self.carrier.db, self.event, sender_fingerprint=self.origin.fingerprint)
        self.carried_board = _board_from_row(self.carrier.db.connection.execute(
            "SELECT * FROM boards WHERE board_id = ?", (self.board.board_id,)
        ).fetchone())

    @property
    def o(self):
        return self.nodes["origin"]

    @property
    def c(self):
        return self.nodes["carrier"]

    @property
    def t(self):
        return self.nodes["third"]

    def remote_vote(self, user, choices, *, created_at=None, node=None):
        node = node or self.carrier
        return build_board_poll_vote(
            signing_identity=node.identity.signing_key, home_node_fingerprint=node.fingerprint,
            local_user_id=user, board_id=self.board.board_id, root_post_id=self.event.content_id,
            choices=list(choices), created_at=created_at or utc_now_iso(),
        )

    def to_origin(self, *votes):
        accepted = self.o.handle_events(self.carrier.fingerprint, [v.to_dict() for v in votes])
        for vote in votes:
            if vote.content_id in accepted:
                materialize_carried_board_poll_vote(
                    self.origin.db, vote, sender_fingerprint=self.carrier.fingerprint,
                    own_fingerprint=self.origin.fingerprint,
                )
        return accepted

    def origin_tally(self):
        return tally(self.origin.db, get_poll(self.origin.db, self.post.root_post_id))

    def close(self):
        for node in (self.origin, self.carrier, getattr(self, "third", None)):
            if node is not None:
                node.close()


@pytest.fixture
def net(tmp_path, clock):
    network = _Net(tmp_path, clock)
    yield network
    network.close()


# -- the poll travels -------------------------------------------------------------


def test_a_linked_poll_travels_with_its_post_and_its_answers_are_fixed(net):
    assert net.event.payload["poll"]["options"] == ["vi", "emacs"]
    assert net.event.payload["poll"]["max_choices"] == 1
    assert net.poll.link_root_id == net.event.content_id and answers_locked(net.origin.db, net.poll)

    carried = get_poll(net.carrier.db, net.event.content_id)
    assert carried.options == ("vi", "emacs") and carried.link_root_id == net.event.content_id
    assert carried.closes_at == net.poll.closes_at

    changed = PollSpec(("vi", "emacs", "nano"), 1, net.poll.closes_at, "after_vote")
    with pytest.raises(PollError, match="travels to other BBSes"):
        update_poll(net.origin.db, net.poll, net.alice.id, net.board, changed, changed_by=net.alice)


def test_an_unreadable_poll_field_shows_the_post_without_a_poll(tmp_path, clock):
    network = _Net(tmp_path, clock)
    try:
        for bad in ({"options": ["only"], "max_choices": 1, "closes_at": _FAR, "results": "after_vote"},
                    {"options": ["a", "b"], "max_choices": 5, "closes_at": _FAR, "results": "after_vote"},
                    "not a dict"):
            assert link_polls.carried_poll_spec(bad) is None
        assert link_polls.carried_poll_spec(
            {"options": ["a", "b"], "max_choices": 2, "closes_at": _FAR, "results": "after_close"}
        ) == PollSpec(("a", "b"), 2, _FAR, "after_close")
    finally:
        network.close()


# -- votes --------------------------------------------------------------------------


def test_a_vote_on_another_node_is_signed_carried_and_counted(net):
    bob = create_user(net.carrier.db, "bob", password="hunter2pw", user_level=10)
    poll = get_poll(net.carrier.db, net.event.content_id)
    cast_vote(net.carrier.db, poll, net.carried_board, bob, [1])
    vote = queue_board_poll_vote_if_linked(
        net.carrier.db, poll, net.carried_board, bob, node_identity=net.carrier.identity,
    )
    assert vote.payload["root_post_id"] == net.event.content_id
    assert vote.payload["author"]["local_user_id"] == "bob" and vote.payload["choices"] == [1]
    # Pushed with the carrier's own events, and served in its inventory.
    assert vote.content_id in {e.content_id for e in load_own_board_events(net.carrier.db, net.carrier.fingerprint)}
    assert vote.content_id in _all_board_events(net.carrier.db, net.board.board_id)

    assert net.to_origin(vote) == [vote.content_id]

    result = net.origin_tally()
    assert result.counts == (0, 1) and result.voters == 1 and result.not_counted == 0
    # The origin keeps the signed vote, and serves it to others.
    assert vote.content_id in _all_board_events(net.origin.db, net.board.board_id)


def test_the_latest_vote_wins_whichever_order_it_arrives_in(net):
    older = net.remote_vote("bob", [0], created_at="2026-10-15T11:00:00.000000Z")
    newer = net.remote_vote("bob", [1], created_at="2026-10-15T12:00:00.000000Z")

    net.to_origin(newer)
    net.to_origin(older)

    assert net.origin_tally().counts == (0, 1)
    kept = {row[0] for row in net.origin.db.connection.execute("SELECT content_id FROM link_events")}
    assert newer.content_id in kept and older.content_id not in kept


def test_a_changed_vote_replaces_the_earlier_one_and_its_bytes(net):
    first = net.remote_vote("bob", [0], created_at="2026-10-15T11:00:00.000000Z")
    second = net.remote_vote("bob", [1], created_at="2026-10-15T12:00:00.000000Z")
    net.to_origin(first)
    net.to_origin(second)

    assert net.origin_tally().counts == (0, 1) and net.origin_tally().voters == 1
    kept = {row[0] for row in net.origin.db.connection.execute("SELECT content_id FROM link_events")}
    assert second.content_id in kept and first.content_id not in kept


def test_a_vote_dated_after_the_close_is_not_counted(net):
    net.to_origin(net.remote_vote("bob", [0]), net.remote_vote("carol", [1], created_at=_FAR))

    result = net.origin_tally()
    assert result.counts == (1, 0) and result.voters == 1 and result.not_counted == 1


def test_votes_from_a_blocked_node_are_not_counted(net):
    net.to_origin(net.remote_vote("bob", [0]), net.remote_vote("carol", [1]))
    assert net.origin_tally().voters == 2

    subject = TrustSubject.node(net.carrier.fingerprint)
    register_subject(net.origin.db, subject, first_accepted_at="2026-08-01T00:00:00.000000Z")
    set_trust_override(
        net.origin.db, subject, TrustDimension.RESOURCE_BEHAVIOR, TrustState.BLOCKED, reason="test",
    )

    result = net.origin_tally()
    assert result.voters == 0 and result.not_counted == 2


def test_at_most_fifty_voters_per_other_node_are_counted(net):
    votes = [net.remote_vote(f"user{i}", [i % 2]) for i in range(51)]
    net.to_origin(*votes)

    result = net.origin_tally()
    assert result.voters == 50 and result.not_counted == 1
    # The origin's own callers are not capped.
    for i in range(3):
        local = create_user(net.origin.db, f"local{i}", password="hunter2pw", user_level=10)
        cast_vote(net.origin.db, get_poll(net.origin.db, net.post.root_post_id), net.board, local, [0])
    assert net.origin_tally().voters == 53


def test_the_row_cap_turns_votes_away_and_says_so(net, monkeypatch):
    monkeypatch.setattr(link_polls, "MAX_VOTER_ROWS_PER_POLL", 2)
    net.to_origin(*[net.remote_vote(f"user{i}", [0]) for i in range(3)])

    rows = net.origin.db.connection.execute("SELECT COUNT(*) FROM board_poll_votes").fetchone()[0]
    assert rows == 2
    result = net.origin_tally()
    assert result.voters == 2 and result.not_counted == 1


def test_a_vote_before_its_poll_is_kept_and_counted_when_the_poll_arrives(tmp_path, clock):
    network = _Net(tmp_path, clock, third=True)
    try:
        third = network.third
        # The third node has the board, but not the poll's post yet.
        assert network.t.handle_events(network.origin.fingerprint, [network.genesis.to_dict()])
        materialize_carried_board(third.db, network.genesis, own_fingerprint=third.fingerprint)
        vote = network.remote_vote("bob", [1])
        assert network.t.handle_events(network.carrier.fingerprint, [vote.to_dict()]) == [vote.content_id]
        assert materialize_carried_board_poll_vote(
            third.db, vote, sender_fingerprint=network.carrier.fingerprint, own_fingerprint=third.fingerprint,
        )
        assert third.db.connection.execute("SELECT COUNT(*) FROM board_poll_votes").fetchone()[0] == 0

        network.t.handle_events(network.origin.fingerprint, [network.event.to_dict()])
        materialize_carried_post(third.db, network.event, sender_fingerprint=network.origin.fingerprint)

        assert tally(third.db, get_poll(third.db, network.event.content_id)).counts == (0, 1)
    finally:
        network.close()


def test_votes_reach_a_third_node_through_inventory(tmp_path, clock):
    network = _Net(tmp_path, clock, third=True)
    try:
        votes = [network.remote_vote("bob", [0]), network.remote_vote("carol", [1])]
        network.to_origin(*votes)

        # The third node asks the origin for the board: genesis, post, votes.
        events, more = board_event_diff(network.origin.db, {network.board.board_id: []}, limit=200)
        assert not more
        ids = {BoardPollVote.from_dict(e).content_id for e in events if e["envelope"]["object_type"] == "board_poll_vote"}
        assert ids == {v.content_id for v in votes}
        lane = DatabaseLane(network.third.db.path)
        try:
            accepted = network.t.handle_events(network.origin.fingerprint, events)
            asyncio.run(persist_accepted_events(
                lane, network.t, accepted, sender_fingerprint=network.origin.fingerprint, max_carried_boards=None,
            ))
        finally:
            lane.close()
        # The board is offered, not yet carried, on the third node; accepting
        # it and replaying is the board's own flow -- what counts here is that
        # each vote arrived and verified against its own home node.
        assert {v.content_id for v in votes} <= set(accepted)
    finally:
        network.close()


def test_a_vote_survives_a_restart_as_known(net):
    vote = net.remote_vote("bob", [0])
    net.to_origin(vote)

    restarted = load_link_node(net.origin.db, net.origin.identity)
    restarted.peers[net.carrier.fingerprint] = net.o.peers[net.carrier.fingerprint]

    assert vote.content_id in restarted.known_event_ids
    # Sent again after the restart, it is a known event, not a second vote.
    assert restarted.handle_events(net.carrier.fingerprint, [vote.to_dict()]) == []
    assert net.origin_tally().voters == 1


def test_a_received_vote_is_persisted_through_the_shared_path(net):
    vote = net.remote_vote("bob", [1])
    accepted = net.o.handle_events(net.carrier.fingerprint, [vote.to_dict()])
    lane = DatabaseLane(net.origin.db.path)
    try:
        asyncio.run(persist_accepted_events(
            lane, net.o, accepted, sender_fingerprint=net.carrier.fingerprint, max_carried_boards=None,
        ))
    finally:
        lane.close()

    assert net.origin_tally().counts == (0, 1)


@pytest.mark.parametrize("choices", [[], [0, 0], [-1], ["a"]])
def test_a_malformed_vote_is_refused(net, choices):
    vote = net.remote_vote("bob", [0])
    raw = vote.to_dict()
    raw["envelope"]["payload"]["choices"] = choices
    with pytest.raises(LinkProtocolError):
        net.o.handle_events(net.carrier.fingerprint, [raw])


def test_a_forged_vote_does_not_verify(net):
    vote = net.remote_vote("bob", [0])
    raw = vote.to_dict()
    raw["envelope"]["payload"]["author"]["local_user_id"] = "mallory"
    with pytest.raises(LinkProtocolError, match="does not verify"):
        net.o.handle_events(net.carrier.fingerprint, [raw])


def test_on_an_origin_only_board_other_nodes_votes_are_not_taken(net):
    set_board_posting(net.origin.db, net.board, "origin_only", node_identity=net.origin.identity)

    net.to_origin(net.remote_vote("bob", [0]))

    assert net.origin_tally().voters == 0
    assert net.origin.db.connection.execute("SELECT COUNT(*) FROM board_poll_votes").fetchone()[0] == 0


# -- closing ------------------------------------------------------------------------


def _carry_edit(net, edit):
    accepted = net.c.handle_events(net.origin.fingerprint, [edit.to_dict()])
    assert accepted == [edit.content_id]
    materialize_carried_post_edit(net.carrier.db, BoardPostEdit.from_dict(edit.to_dict()),
                                  sender_fingerprint=net.origin.fingerprint)


def test_closing_a_linked_poll_travels_as_an_edit(net):
    close_poll(net.origin.db, net.poll, net.alice.id, net.board, closed_by=net.alice)
    revision = edit_post(
        net.origin.db, net.post, net.board, subject=net.post.subject, body=net.post.body, edited_by=net.alice,
        poll_change=True,
    )
    edit = queue_board_post_edit_if_linked(
        net.origin.db, revision, net.board, node_identity=net.origin.identity, edited_by=net.alice,
    )
    closed_at = get_poll(net.origin.db, net.post.root_post_id).closes_at
    assert edit.payload["poll"] == {"closes_at": closed_at}

    _carry_edit(net, edit)

    carried = get_poll(net.carrier.db, net.event.content_id)
    assert carried.closes_at == closed_at and poll_closed(net.carrier.db, carried)


def test_a_carried_withdrawal_closes_the_poll_and_hides_its_totals(net):
    withdrawn = withdraw_post(net.origin.db, net.post, net.board, withdrawn_by=net.alice)
    edit = queue_board_post_edit_if_linked(
        net.origin.db, withdrawn, net.board, node_identity=net.origin.identity, edited_by=net.alice,
    )

    _carry_edit(net, edit)

    carried = get_poll(net.carrier.db, net.event.content_id)
    assert carried.closed_at is not None and poll_hidden(net.carrier.db, carried)


# -- the screens ----------------------------------------------------------------------


def test_a_linked_poll_tells_the_caller_their_vote_is_shared(net):
    from netbbs.net import board_flow
    from netbbs.net.redraw_preference import set_redraw_in_place_enabled
    from tests.test_board_list_and_reader import FakeSession

    bob = create_user(net.carrier.db, "bob", password="hunter2pw", user_level=10)
    set_redraw_in_place_enabled(net.carrier.db, bob, True)
    session = FakeSession(["0", "1", "b", "b"])

    asyncio.run(board_flow._show_board(session, net.carrier.db, net.carried_board, bob))

    assert "Your vote is shared with every BBS that carries this board." in " ".join(session.visible().split())
    assert "[N]ew poll" in session.screens()[0]
