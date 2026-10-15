"""A peer learns a node's user renames (design doc §16 #1111, issue #1306).

The home node announces `user_renames` in its signed endpoint descriptor. A
peer keeps what it learns and from then on treats `new@node` as `old@node`:
trust, mail blocks and replies follow the new name, and content newly signed
as the old one is refused. Two renames are refused outright, because they
would grant the home node something it does not already have.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest

from netbbs.auth.renames import announced_renames, rename_user
from netbbs.auth.users import SYSOP_LEVEL, create_user
from netbbs.link.enforcement import (
    REASON_RETIRED_IDENTITY,
    decide_event_authorship,
    decide_user_authorship,
)
from netbbs.link.events import build_board_post, build_endpoint_descriptor
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.onboarding import mark_link_has_run
from netbbs.link.protocol import LinkNode
from netbbs.link.transport import dial_hello
from netbbs.link.trust import (
    TrustDimension,
    TrustState,
    TrustSubject,
    get_effective_trust_state,
    list_trust_subjects,
    record_activity,
    record_vouch,
    register_subject,
    set_trust_override,
)
from netbbs.link.user_renames import (
    MAX_DESCRIPTOR_RENAMES,
    current_user_name,
    descriptor_user_renames,
    former_user_names,
    learn_user_renames,
)
from netbbs.storage.database import Database
from tests.test_link_transport import _NodeDb, _run_server

HOME = "h" * 32


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


NOW = datetime.now(timezone.utc)


def entry(old: str, new: str, at: datetime) -> dict:
    return {"from": old, "to": new, "at": stamp(at)}


def announce(db, *entries, node=HOME):
    return learn_user_renames(db, node, {"user_renames": list(entries)})


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "peer.db")
    yield database
    database.close()


def _register(db, name, *, days_ago, node=HOME):
    subject = TrustSubject.user(node, name)
    register_subject(db, subject, first_accepted_at=stamp(NOW - timedelta(days=days_ago)))
    return subject


def _state(db, subject, dimension=TrustDimension.CONTENT_CONDUCT):
    return get_effective_trust_state(db, subject, dimension)


# -- reading the descriptor -------------------------------------------------------


def test_the_reader_keeps_valid_entries_and_skips_the_rest():
    entries = [
        entry("alice", "nib", NOW - timedelta(days=2)),
        {"from": "bob", "to": "quill", "at": stamp(NOW - timedelta(days=1)), "extra": 1},
        entry("carol", "carol", NOW - timedelta(days=1)),
        entry("dave", "has space", NOW - timedelta(days=1)),
        entry("erin", "ink", NOW + timedelta(days=1)),
        entry("fay", "fern", NOW - timedelta(days=401)),
        {"from": "gus", "to": "gill", "at": "yesterday"},
        "not an entry",
        entry("hal", "hob", NOW - timedelta(days=3)),
    ]
    renames = descriptor_user_renames({"user_renames": entries})
    assert [(r.old, r.new) for r in renames] == [("hal", "hob"), ("alice", "nib")]  # oldest first


@pytest.mark.parametrize("value", [None, "alice->nib", {"from": "a"}, 42])
def test_a_list_that_is_not_a_list_is_nothing(value):
    assert descriptor_user_renames({"user_renames": value}) == []


def test_the_reader_reads_at_most_64_entries():
    entries = [entry(f"u{i}", f"v{i}", NOW - timedelta(minutes=i)) for i in range(MAX_DESCRIPTOR_RENAMES + 5)]
    assert len(descriptor_user_renames({"user_renames": entries})) == MAX_DESCRIPTOR_RENAMES


def test_a_node_announces_what_it_holds_in_its_signed_descriptor(tmp_path):
    home = Database(tmp_path / "home.db")
    try:
        mark_link_has_run(home)
        sysop = create_user(home, "sysop", password="password", user_level=SYSOP_LEVEL)
        rename_user(home, create_user(home, "alice", password="password"), "nib", renamed_by=sysop)
        node = LinkNode(identity=bootstrap_node_identity("home"))
        hello = node.build_hello(
            addresses=None, outgoing_only=True, created_at=stamp(NOW), user_renames=announced_renames(home),
        )
        (rename,) = hello.descriptor.payload["user_renames"]
        assert (rename["from"], rename["to"]) == ("alice", "nib")
        assert descriptor_user_renames(hello.descriptor.payload)[0].new == "nib"
    finally:
        home.close()


# -- learning, over a real hello -----------------------------------------------------


def test_a_peer_learns_the_rename_from_a_hello_and_the_block_follows(tmp_path):
    """The renamed user's home node dials a peer that had blocked them. After
    the hello the peer knows the new name, and the block stands under it."""
    home_db = _NodeDb(tmp_path, "home")
    peer_db = _NodeDb(tmp_path, "peer")
    home_identity = bootstrap_node_identity("home")
    home_node = LinkNode(identity=home_identity)
    peer_node = LinkNode(identity=bootstrap_node_identity("peer"))
    old = TrustSubject.user(home_identity.fingerprint, "alice")
    register_subject(peer_db.db, old, first_accepted_at=stamp(NOW - timedelta(days=60)))
    for dimension in TrustDimension:
        set_trust_override(peer_db.db, old, dimension, TrustState.BLOCKED, reason="spam")
    mark_link_has_run(home_db.db)
    sysop = create_user(home_db.db, "sysop", password="password", user_level=SYSOP_LEVEL)
    rename_user(home_db.db, create_user(home_db.db, "alice", password="password"), "nib", renamed_by=sysop)

    def own_hello(node, db):
        return lambda: node.build_hello(
            addresses=None, outgoing_only=True, created_at=stamp(datetime.now(timezone.utc)),
            user_renames=announced_renames(db),
        )

    async def scenario():
        server = await _run_server(peer_node, own_hello(peer_node, peer_db.db), peer_db.lane)
        try:
            async with aiohttp.ClientSession() as session:
                await dial_hello(
                    home_node, session, f"http://127.0.0.1:{server.port}",
                    own_hello(home_node, home_db.db)(), home_db.lane,
                )
        finally:
            await server.stop()

    try:
        asyncio.run(scenario())
        assert current_user_name(peer_db.db, home_identity.fingerprint, "alice") == "nib"
        decision = decide_user_authorship(peer_db.db, home_identity.fingerprint, "nib")
        assert (decision.allowed, decision.state) == (False, TrustState.BLOCKED)
    finally:
        home_db.close()
        peer_db.close()


def test_the_new_name_takes_the_old_ones_probation_age_activity_and_vouches(db):
    from tests.test_link_trust import configure_reporter

    old = _register(db, "alice", days_ago=60)
    configure_reporter(db, "reporter-a", "domain-a", user_vouch=True)
    record_vouch(
        db, content_id="vouch-1", issuer_fingerprint="reporter-a", subject=old,
        issued_at=stamp(NOW - timedelta(days=1)), expires_at=stamp(NOW + timedelta(days=30)),
    )
    for days in (3, 2, 1):
        record_activity(db, old, activity_date=(NOW - timedelta(days=days)).date().isoformat(), direct=False)
    assert _state(db, old).state == TrustState.ESTABLISHED

    assert announce(db, entry("alice", "nib", NOW - timedelta(hours=1)))

    new = TrustSubject.user(HOME, "nib")
    assert _state(db, new).state == TrustState.ESTABLISHED
    # Asked by the old name, the answer is the new name's.
    assert _state(db, old).state == TrustState.ESTABLISHED
    assert [s.opaque_user_id for s in list_trust_subjects(db) if s.kind == "user"] == ["nib"]


def test_what_is_recorded_about_the_old_name_afterwards_lands_on_the_new_one(db):
    _register(db, "alice", days_ago=60)
    announce(db, entry("alice", "nib", NOW - timedelta(hours=1)))

    set_trust_override(db, TrustSubject.user(HOME, "alice"), TrustDimension.CONTENT_CONDUCT, "quarantined", reason="x")

    assert _state(db, TrustSubject.user(HOME, "nib")).state == TrustState.QUARANTINED


def test_when_both_names_carry_state_the_stricter_override_stays(db):
    old = _register(db, "alice", days_ago=60)
    set_trust_override(db, old, TrustDimension.CONTENT_CONDUCT, "blocked", reason="abuse")
    # The renamed user's own content reached this node before the notice.
    new = _register(db, "nib", days_ago=0)
    set_trust_override(db, new, TrustDimension.CONTENT_CONDUCT, "established", reason="looks fine")

    assert announce(db, entry("alice", "nib", NOW - timedelta(days=1)))

    assert _state(db, new).state == TrustState.BLOCKED


def test_a_quarantine_in_its_recovery_hold_is_not_lifted_by_a_rename(db):
    old = _register(db, "alice", days_ago=60)
    db.connection.execute(
        """UPDATE link_trust_effective_states SET state = 'quarantined', reason_code = 'recovery_hold',
               recovery_started_at = ? WHERE subject_id = ?""",
        (stamp(NOW - timedelta(hours=1)), old.subject_id),
    )
    db.connection.commit()

    announce(db, entry("alice", "nib", NOW - timedelta(hours=2)))

    assert _state(db, TrustSubject.user(HOME, "nib")).state == TrustState.QUARANTINED


def test_chains_resolve_and_list_the_former_names(db):
    _register(db, "alice", days_ago=60)
    announce(db, entry("alice", "nib", NOW - timedelta(days=2)), entry("nib", "quill", NOW - timedelta(days=1)))

    assert current_user_name(db, HOME, "alice") == "quill"
    assert former_user_names(db, HOME, "quill") == ["nib", "alice"]
    assert [s.opaque_user_id for s in list_trust_subjects(db) if s.kind == "user"] == ["quill"]


def test_an_alias_is_kept_after_the_descriptor_drops_it(db):
    announce(db, entry("alice", "nib", NOW - timedelta(days=2)))
    announce(db)  # a later descriptor with no renames at all
    assert current_user_name(db, HOME, "alice") == "nib"


def test_another_nodes_user_of_the_same_name_is_not_touched(db):
    announce(db, entry("alice", "nib", NOW - timedelta(days=2)))
    assert current_user_name(db, "o" * 32, "alice") == "alice"


# -- what a peer refuses -------------------------------------------------------------


def test_a_rename_that_would_merge_two_users_is_refused_and_logged(db, caplog):
    old = _register(db, "alice", days_ago=60)
    set_trust_override(db, old, TrustDimension.CONTENT_CONDUCT, "blocked", reason="abuse")
    other = _register(db, "nib", days_ago=30)  # history from before the rename

    with caplog.at_level(logging.WARNING, logger="netbbs.link"):
        assert announce(db, entry("alice", "nib", NOW - timedelta(days=1))) == []

    assert "would merge two users" in caplog.text
    assert current_user_name(db, HOME, "alice") == "alice"
    assert _state(db, other).state != TrustState.BLOCKED


def test_a_rename_back_to_a_name_renamed_away_from_is_refused(db, caplog):
    announce(db, entry("alice", "nib", NOW - timedelta(days=2)))
    with caplog.at_level(logging.WARNING, logger="netbbs.link"):
        assert announce(db, entry("nib", "alice", NOW - timedelta(days=1))) == []
    assert "renames back" in caplog.text
    assert current_user_name(db, HOME, "alice") == "nib"
    assert current_user_name(db, HOME, "nib") == "nib"


def test_content_newly_signed_as_the_old_name_is_refused_and_older_content_is_not(db, caplog):
    home = bootstrap_node_identity("home")
    learn_user_renames(db, home.fingerprint, {"user_renames": [entry("alice", "nib", NOW - timedelta(days=1))]})

    def post(created):
        return build_board_post(
            signing_identity=home.signing_key, home_node_fingerprint=home.fingerprint, local_user_id="alice",
            board_id="b-1", subject="Hi", body="text", created_at=stamp(created),
        ).to_dict()

    with caplog.at_level(logging.WARNING, logger="netbbs.link"):
        late = decide_event_authorship(db, post(NOW), transport_peer_fingerprint=home.fingerprint)
    assert (late.allowed, late.reason_code) == (False, REASON_RETIRED_IDENTITY)
    assert "renamed away from" in caplog.text
    early = decide_event_authorship(db, post(NOW - timedelta(days=3)), transport_peer_fingerprint=home.fingerprint)
    assert early.reason_code != REASON_RETIRED_IDENTITY


def test_a_bad_descriptor_never_breaks_learning(db):
    assert learn_user_renames(db, HOME, {"user_renames": [{"from": None, "to": [], "at": {}}]}) == []
    assert learn_user_renames(db, HOME, "not a payload") == []


# -- mail ---------------------------------------------------------------------------


def test_a_block_on_the_old_address_covers_the_new_one(db):
    from netbbs.mail import block_link_sender, mail_sender_refusal

    alice_here = create_user(db, "carol", password="password")
    block_link_sender(db, alice_here, f"alice@{HOME}")
    announce(db, entry("alice", "nib", NOW - timedelta(days=1)))

    assert mail_sender_refusal(db, alice_here, sender_address=f"nib@{HOME}") is not None


def test_a_letter_to_the_old_address_goes_to_the_new_one(db):
    from netbbs.link.mail import compose_link_message
    from tests.test_link_mail import _seed_peer

    here, there = bootstrap_node_identity("here"), bootstrap_node_identity("there")
    _seed_peer(db, there)
    learn_user_renames(db, there.fingerprint, {"user_renames": [entry("alice", "nib", NOW - timedelta(days=1))]})
    carol = create_user(db, "carol", password="password")

    message = compose_link_message(db, carol, f"alice@{there.fingerprint}", "Re: hi", "x", node_identity=here)

    assert message.payload["recipient"]["local_user_id"] == "nib"


# -- display --------------------------------------------------------------------------


def test_the_trust_screen_and_the_block_list_show_the_current_name_and_the_former_one(db):
    from netbbs.mail import block_link_sender
    from netbbs.net.admin_trust import _former_names, _trust_subject_name
    from netbbs.net.mail_flow import _load_blocked_rows

    _register(db, "alice", days_ago=60)
    carol = create_user(db, "carol", password="password")
    block_link_sender(db, carol, f"alice@{HOME}")
    announce(db, entry("alice", "nib", NOW - timedelta(days=1)))

    (subject,) = [s for s in list_trust_subjects(db) if s.kind == "user"]
    assert _trust_subject_name(subject, "Far BBS", _former_names(db, subject)) == "user:nib@Far BBS (formerly alice)"
    (row,) = _load_blocked_rows(db, carol)
    assert row.name.startswith("nib") and row.name.endswith("(formerly alice)")


def test_the_descriptor_still_verifies_with_renames_in_it():
    from netbbs.link.events import verify_endpoint_descriptor

    identity = bootstrap_node_identity("home")
    descriptor = build_endpoint_descriptor(
        signing_identity=identity.signing_key, subject_fingerprint=identity.fingerprint, addresses=None,
        outgoing_only=True, created_at=stamp(NOW), user_renames=[entry("alice", "nib", NOW)],
    )
    assert verify_endpoint_descriptor(descriptor, identity.signing_key.verify_key)
