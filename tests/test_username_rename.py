"""Renaming a username, the local half (design doc §16 #1111, issue #1306).

Each test starts from what a caller or a SysOp actually does -- ask for a new
name, approve or refuse it, rename an account directly -- and asks what the
node does with it: who can log in as what, which name is held for whom, where
mail and `/msg` written to the old name land, and how chat lines written
before the rename still name their author.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from netbbs.attestation import attest_name, set_attestation_link_visible
from netbbs.auth.renames import (
    ANNOUNCED_RENAME_DAYS,
    MAX_ANNOUNCED_RENAMES,
    UsernameChangeError,
    acknowledge_refusal_notices,
    announced_renames,
    approve_username_request,
    ended_session_notice,
    get_recipient_by_name,
    list_open_username_requests,
    open_username_request,
    pending_refusal_notices,
    refuse_username_request,
    rename_user,
    request_username_change,
    username_history,
    withdraw_username_request,
)
from netbbs.auth.users import (
    SYSOP_LEVEL,
    AuthError,
    StaffPermission,
    UserManagementError,
    authenticate_password,
    create_user,
    delete_user,
    get_user_by_id,
    is_username_retired,
    list_retired_usernames,
    release_retired_username,
    set_staff_permissions,
)
from netbbs.chat.channels import create_channel
from netbbs.chat.scrollback import get_scrollback, record_message
from netbbs.link.onboarding import mark_link_has_run
from netbbs.moderation.log import list_actions_for_target_user
from netbbs.net.chat_flow import _message_author_label, _resolve_message_author
from netbbs.storage.database import Database
from netbbs.timeutil import utc_now_iso


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def sysop(db):
    return create_user(db, "sysop", password="password", user_level=SYSOP_LEVEL)


@pytest.fixture
def alice(db):
    return create_user(db, "alice", password="password")


@pytest.fixture
def linked(db):
    mark_link_has_run(db)


def _actions(db, user):
    return [entry.action for entry in list_actions_for_target_user(db, user.id)]


# -- asking -------------------------------------------------------------------


def test_a_caller_asks_and_the_request_waits_for_the_sysop(db, sysop, alice):
    request = request_username_change(db, alice, "nib")

    assert open_username_request(db, alice).requested_username == "nib"
    assert [r.id for r in list_open_username_requests(db)] == [request.id]
    # Nothing changed yet: the caller still logs in as before.
    assert authenticate_password(db, "alice", "password").id == alice.id
    assert "request_username_change" in _actions(db, alice)


@pytest.mark.parametrize(
    ("wanted", "says"),
    [
        ("sysop", "reserved"),
        ("Sys0p", "reserved"),
        ("bob", "already taken"),
        ("Tintenfaß", "ASCII letters"),
        ("alice", "already your username"),
        ("ALICE", "already your username"),
    ],
)
def test_a_request_is_checked_as_a_signup_is(db, sysop, alice, wanted, says):
    create_user(db, "bob", password="password")
    with pytest.raises(UsernameChangeError, match=says):
        request_username_change(db, alice, wanted)
    assert open_username_request(db, alice) is None


def test_a_held_name_cannot_be_asked_for(db, sysop, alice, linked):
    delete_user(db, create_user(db, "carol", password="password"), deleted_by=sysop)
    with pytest.raises(UsernameChangeError, match="already taken"):
        request_username_change(db, alice, "carol")


def test_one_open_request_per_account_and_a_new_one_replaces_it(db, sysop, alice):
    request_username_change(db, alice, "nib")
    request_username_change(db, alice, "quill")

    assert [r.requested_username for r in list_open_username_requests(db)] == ["quill"]


def test_a_caller_can_withdraw_their_request(db, sysop, alice):
    request_username_change(db, alice, "nib")
    assert withdraw_username_request(db, alice)
    assert open_username_request(db, alice) is None
    assert not withdraw_username_request(db, alice)


def test_one_approved_rename_per_thirty_days(db, sysop, alice):
    request = request_username_change(db, alice, "nib")
    _before, nib = approve_username_request(db, request.id, approved_by=sysop)

    with pytest.raises(UsernameChangeError, match="less than 30 days ago"):
        request_username_change(db, nib, "quill")
    later = stamp(datetime.now(timezone.utc) + timedelta(days=31))
    assert request_username_change(db, nib, "quill", now=later).requested_username == "quill"


# -- approving ----------------------------------------------------------------


def test_approval_renames_the_account_and_keeps_everything_keyed_on_its_id(db, sysop, alice, linked):
    request = request_username_change(db, alice, "nib")

    before, after = approve_username_request(db, request.id, approved_by=sysop)

    assert (before.username, after.username) == ("alice", "nib")
    assert after.id == alice.id and after.created_at == alice.created_at
    assert authenticate_password(db, "nib", "password").id == alice.id
    with pytest.raises(AuthError):
        authenticate_password(db, "alice", "password")
    assert open_username_request(db, after) is None
    assert [(c.old_username, c.new_username) for c in username_history(db, after)] == [("alice", "nib")]
    renamed = [e for e in list_actions_for_target_user(db, alice.id) if e.action == "rename_user"]
    assert len(renamed) == 1 and "'alice'" in renamed[0].detail and "'nib'" in renamed[0].detail


def test_the_old_name_is_held_for_the_renamed_account_on_a_link_node(db, sysop, alice, linked):
    rename_user(db, alice, "nib", renamed_by=sysop)

    assert is_username_retired(db, "alice")
    assert [(e.username, e.renamed_to) for e in list_retired_usernames(db)] == [("alice", "nib")]
    with pytest.raises(AuthError):
        create_user(db, "alice", password="password")


def test_a_node_that_never_ran_link_frees_the_old_name(db, sysop, alice):
    rename_user(db, alice, "nib", renamed_by=sysop)

    assert not is_username_retired(db, "alice")
    assert create_user(db, "alice", password="password").id != alice.id


def test_approval_checks_the_name_again(db, sysop, alice):
    request = request_username_change(db, alice, "nib")
    create_user(db, "Nib", password="password")

    with pytest.raises(UserManagementError, match="can no longer be given"):
        approve_username_request(db, request.id, approved_by=sysop)
    assert get_user_by_id(db, alice.id).username == "alice"


def test_only_a_sysop_decides(db, sysop, alice):
    helper = create_user(db, "helper", password="password")
    set_staff_permissions(
        db, helper, int(StaffPermission.APPROVE_ACCOUNTS | StaffPermission.MANAGE_ACCOUNTS), changed_by=sysop
    )
    request = request_username_change(db, alice, "nib")

    with pytest.raises(UserManagementError, match="only a SysOp"):
        approve_username_request(db, request.id, approved_by=get_user_by_id(db, helper.id))
    with pytest.raises(UserManagementError, match="only a SysOp"):
        rename_user(db, alice, "quill", renamed_by=get_user_by_id(db, helper.id))
    assert get_user_by_id(db, alice.id).username == "alice"


# -- refusing -----------------------------------------------------------------


def test_a_refusal_is_told_once_at_the_next_login(db, sysop, alice):
    request = request_username_change(db, alice, "nib")

    refuse_username_request(db, request.id, refused_by=sysop, reason="Too close to a regular's name.")

    lines, ids = pending_refusal_notices(db, alice)
    assert lines == ["Your request to be renamed to 'nib' was refused: Too close to a regular's name."]
    acknowledge_refusal_notices(db, ids)
    assert pending_refusal_notices(db, alice) == ([], [])
    assert open_username_request(db, alice) is None
    assert "refuse_username_change" in _actions(db, alice)


def test_a_refusal_needs_a_reason(db, sysop, alice):
    request = request_username_change(db, alice, "nib")
    with pytest.raises(UserManagementError, match="needs a reason"):
        refuse_username_request(db, request.id, refused_by=sysop, reason="   ")
    assert open_username_request(db, alice) is not None


# -- the SysOp's own rename ---------------------------------------------------


def test_a_direct_rename_is_not_limited_and_answers_an_open_request(db, sysop, alice, linked):
    rename_user(db, alice, "nib", renamed_by=sysop)
    request_username_change_at = stamp(datetime.now(timezone.utc) + timedelta(days=40))
    request_username_change(db, get_user_by_id(db, alice.id), "quill", now=request_username_change_at)

    _before, after = rename_user(db, get_user_by_id(db, alice.id), "inkwell", renamed_by=sysop)

    assert after.username == "inkwell"
    assert open_username_request(db, after) is None
    # A chain: both old names reach the account.
    assert get_recipient_by_name(db, "alice").id == alice.id
    assert get_recipient_by_name(db, "nib").id == alice.id


def test_a_direct_rename_refuses_a_held_name(db, sysop, alice, linked):
    delete_user(db, create_user(db, "carol", password="password"), deleted_by=sysop)
    with pytest.raises(UserManagementError, match="held name"):
        rename_user(db, alice, "carol", renamed_by=sysop)


def test_renaming_back_to_an_old_name_is_refused_while_it_is_held(db, sysop, alice, linked):
    rename_user(db, alice, "nib", renamed_by=sysop)
    with pytest.raises(UserManagementError, match="held name"):
        rename_user(db, get_user_by_id(db, alice.id), "alice", renamed_by=sysop)


# -- delivery through the retired row ------------------------------------------


def test_the_old_name_reaches_the_account_until_it_is_deleted(db, sysop, alice, linked):
    rename_user(db, alice, "nib", renamed_by=sysop)
    assert get_recipient_by_name(db, "ALICE").id == alice.id

    delete_user(db, get_user_by_id(db, alice.id), deleted_by=sysop)

    with pytest.raises(AuthError):
        get_recipient_by_name(db, "alice")
    # Both names stay held; neither names an account any more.
    assert {(e.username, e.renamed_to) for e in list_retired_usernames(db)} == {("alice", None), ("nib", None)}


def test_a_released_old_name_belongs_to_whoever_registers_it(db, sysop, alice, linked):
    rename_user(db, alice, "nib", renamed_by=sysop)
    release_retired_username(db, "alice", released_by=sysop)
    newcomer = create_user(db, "alice", password="password")

    assert get_recipient_by_name(db, "alice").id == newcomer.id


def test_link_mail_to_the_old_name_lands_in_the_renamed_account(db, sysop, alice, linked):
    from netbbs.identity.encryption import encrypt_for
    from netbbs.link.events import build_link_message
    from netbbs.link.mail import deliver_link_message
    from netbbs.link.node_identity import bootstrap_node_identity

    here, there = bootstrap_node_identity("roanoke"), bootstrap_node_identity("farpoint")
    rename_user(db, alice, "nib", renamed_by=sysop)
    message = build_link_message(
        signing_identity=there.signing_key, home_node_fingerprint=there.fingerprint, local_user_id="bob",
        recipient_home_node_fingerprint=here.fingerprint, recipient_local_user_id="alice",
        confidentiality_tier="tier1_home_node_key",
        ciphertext=encrypt_for(here.signing_key.verify_key, json.dumps({"subject": "hi", "body": "x"}).encode()),
        created_at="2026-01-01T00:00:00Z",
    )

    deliver_link_message(db, message.to_dict(), node_identity=here)

    row = db.connection.execute("SELECT recipient_user_id, subject FROM mail_messages").fetchone()
    assert (row["recipient_user_id"], row["subject"]) == (alice.id, "hi")


def test_ftn_netmail_to_the_old_name_lands_in_the_renamed_account(db, sysop, alice, linked):
    from tests.test_ftn_tosser import _netmail, _packet, _toss
    from netbbs.ftn.address import FtnAddress
    from netbbs.ftn.networks import FtnNetwork, save_network
    from netbbs.mail import list_inbox

    network = save_network(db, FtnNetwork(
        name="fsxNet", domain="fsxnet", our_address=FtnAddress(21, 1, 199),
        uplink_address=FtnAddress(21, 1, 100), uplink_host="hub.example", enabled=True, origin_text="Test BBS",
    ))
    rename_user(db, alice, "nib", renamed_by=sysop)

    result = _toss(db, network, _packet(_netmail(to="alice")))

    assert result.netmail == 1
    (letter,) = list_inbox(db, get_user_by_id(db, alice.id))
    assert letter.body == "A private note"
    assert list_inbox(db, sysop) == []


def test_msg_to_the_old_name_reaches_the_renamed_account(db, sysop, alice, linked):
    from netbbs.chat.hub import ChatHub
    from netbbs.chat.mailbox import MessageMailbox
    from netbbs.chat.presence import PresenceRegistry
    from netbbs.net.session_registry import ActiveSessionRegistry
    from netbbs.storage.execution import DatabaseLane
    from tests.test_chat_flow_moderation import FakeSession
    from tests.test_chat_flow_private import _run, _written

    bob = create_user(db, "bob", password="password", user_level=10)
    channel = create_channel(db, "lobby", creator=bob)
    rename_user(db, alice, "nib", renamed_by=sysop)
    hub, presence, mailbox = ChatHub(), PresenceRegistry(), MessageMailbox()
    presence.enter("nib")
    registry = ActiveSessionRegistry()
    nib_session = FakeSession()
    lane = DatabaseLane(db.path)

    async def scenario():
        registry.enter(nib_session)
        registry.mark_authenticated(nib_session, "nib")
        return await _run(
            lane, hub, presence, mailbox, channel, bob, ["/msg alice still there?", "/quit"],
            session_registry=registry,
        )

    try:
        session = asyncio.run(scenario())
    finally:
        lane.close()
    assert "(sent to nib)" in _written(session)
    (pending,) = mailbox.flush(nib_session)
    assert "still there?" in pending[0]


# -- chat lines keep their author ----------------------------------------------


def test_a_chat_line_still_names_its_author_after_a_rename(db, sysop, alice, linked):
    channel = create_channel(db, "lobby", creator=sysop)
    line = record_message(db, channel, kind="message", author_label="alice", body="hi", author_user_id=alice.id)
    rename_user(db, alice, "nib", renamed_by=sysop)

    (stored,) = [m for m in get_scrollback(db, channel) if m.id == line.id]
    assert stored.author_label == "alice"  # history keeps the name it was written under
    assert _resolve_message_author(db, stored).id == alice.id
    assert "nib" in _message_author_label(db, channel, stored, None)


def test_a_line_never_resolves_to_a_later_holder_of_the_name(db, sysop, alice):
    """On a node that never ran Link the old name is freed, and someone else
    can take it. The line stays the renamed account's."""
    channel = create_channel(db, "lobby", creator=sysop)
    line = record_message(db, channel, kind="message", author_label="alice", body="hi", author_user_id=alice.id)
    rename_user(db, alice, "nib", renamed_by=sysop)
    create_user(db, "alice", password="password")

    (stored,) = [m for m in get_scrollback(db, channel) if m.id == line.id]
    assert _resolve_message_author(db, stored).id == alice.id


# -- the upgrade --------------------------------------------------------------


def _upgrade_from_before_1306(tmp_path, monkeypatch, populate):
    from netbbs.storage import database as database_module
    from netbbs.storage.migrations import MIGRATIONS

    index = next(i for i, m in enumerate(MIGRATIONS) if m.description.startswith("Issue #1306"))
    monkeypatch.setattr(database_module, "MIGRATIONS", MIGRATIONS[:index])
    path = tmp_path / "pre-1306.db"
    old = Database(path)
    populate(old)
    old.connection.commit()
    old.close()
    monkeypatch.undo()
    return Database(path)


def test_the_upgrade_gives_old_chat_lines_to_their_author_only_where_that_is_safe(tmp_path, monkeypatch):
    def populate(old):
        conn = old.connection
        carol = create_user(old, "carol", password="password")
        conn.execute("UPDATE users SET created_at = '2026-03-01T00:00:00.000000Z' WHERE id = ?", (carol.id,))
        conn.commit()
        channel = create_channel(old, "lobby", creator=carol)
        rows = [
            # Written by an earlier "carol", before this account existed.
            (channel.id, "message", "carol", "before", "2026-02-01T00:00:00.000000Z", None),
            # Written by this account.
            (channel.id, "message", "Carol", "after", "2026-03-02T00:00:00.000000Z", None),
            # A Link speaker and an MRC line: no account wrote them.
            (channel.id, "message", "carol@Far BBS", "link", "2026-03-03T00:00:00.000000Z", None),
            (channel.id, "message", "carol", "mrc", "2026-03-04T00:00:00.000000Z", "mrc"),
        ]
        conn.executemany(
            "INSERT INTO channel_messages (channel_id, kind, author_label, body, created_at, external_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    upgraded = _upgrade_from_before_1306(tmp_path, monkeypatch, populate)
    try:
        by_body = {
            row["body"]: row["author_user_id"]
            for row in upgraded.connection.execute("SELECT body, author_user_id FROM channel_messages")
        }
        carol_id = upgraded.connection.execute("SELECT id FROM users WHERE username = 'carol'").fetchone()[0]
        assert by_body == {"before": 0, "after": carol_id, "link": None, "mrc": None}
        lines = {m.body: m for m in get_scrollback(upgraded, upgraded_channel(upgraded))}
        assert _resolve_message_author(upgraded, lines["before"]) is None
        assert _resolve_message_author(upgraded, lines["after"]).username == "carol"
    finally:
        upgraded.close()


def upgraded_channel(db):
    from netbbs.chat.channels import list_channels

    (channel,) = list_channels(db)
    return channel


def test_the_upgrade_holds_existing_names_for_nobody(tmp_path, monkeypatch):
    def populate(old):
        old.connection.execute(
            "INSERT INTO retired_usernames (username, retired_at) VALUES ('gone', '2026-01-01T00:00:00.000000Z')"
        )

    upgraded = _upgrade_from_before_1306(tmp_path, monkeypatch, populate)
    try:
        assert [(e.username, e.renamed_to) for e in list_retired_usernames(upgraded)] == [("gone", None)]
        with pytest.raises(AuthError):
            get_recipient_by_name(upgraded, "gone")
    finally:
        upgraded.close()


# -- live sessions --------------------------------------------------------------


def test_a_session_of_the_renamed_account_is_told_the_new_name(db, sysop, alice):
    rename_user(db, alice, "nib", renamed_by=sysop)
    assert "'nib'" in ended_session_notice(db, alice)
    assert ended_session_notice(db, get_user_by_id(db, alice.id)) == (
        "Your account is no longer active. Disconnecting."
    )


def test_the_watcher_ends_a_renamed_accounts_session_with_the_new_name(db, sysop, alice, monkeypatch):
    from netbbs.chat.hub import ChatHub
    from netbbs.chat.mailbox import MessageMailbox
    from netbbs.chat.presence import PresenceRegistry
    from netbbs.net import login_flow
    from netbbs.net.session_registry import ActiveSessionRegistry
    from tests.test_account_revocation_watcher import FakeSession, _drive, _wait_until_done, _written_text

    monkeypatch.setattr(login_flow, "_REVOCATION_CHECK_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(login_flow, "_REVOCATION_NOTICE_TIMEOUT_SECONDS", 0.5)
    hub, presence, mailbox = ChatHub(), PresenceRegistry(), MessageMailbox()
    registry = ActiveSessionRegistry()
    session = FakeSession([])

    async def scenario():
        task = asyncio.create_task(_drive(db, hub, presence, mailbox, registry, alice, session))
        await asyncio.sleep(0.05)
        rename_user(db, alice, "nib", renamed_by=sysop)
        registry.request_account_recheck("alice")
        await _wait_until_done(task)

    asyncio.run(scenario())
    assert "Your username is now 'nib'" in _written_text(session)
    assert not presence.is_online("alice")


# -- attestations ---------------------------------------------------------------


def test_attestations_about_the_account_are_reissued_under_the_new_name(db, sysop, alice):
    import nacl.signing

    from netbbs.identity.keys import Identity, IdentityKind
    from netbbs.link.remote_attestation import reconcile_issued_attestations

    identity = Identity(
        kind=IdentityKind.NODE, label="issuer", signing_key=nacl.signing.SigningKey.generate(),
        created_at=utc_now_iso(),
    )
    attest_name(db, alice, "Alice Example", verifier=sysop)
    set_attestation_link_visible(db, alice, "name", True)
    reconcile_issued_attestations(db, identity, home_node_fingerprint="home")

    rename_user(db, alice, "nib", renamed_by=sysop)
    changes = reconcile_issued_attestations(db, identity, home_node_fingerprint="home")

    assert sorted((c.action, c.reason) for c in changes) == [
        ("issued", "consent_granted"), ("revoked", "account_renamed"),
    ]
    live = db.connection.execute(
        "SELECT envelope_json FROM link_issued_remote_attestations "
        "WHERE object_type = 'remote_identity_attestation' AND revoked_at IS NULL"
    ).fetchall()
    assert [json.loads(r["envelope_json"])["payload"]["subject"]["opaque_user_id"] for r in live] == ["nib"]
    # And nothing more on the next pass.
    assert reconcile_issued_attestations(db, identity, home_node_fingerprint="home") == []


# -- what the descriptor announces ----------------------------------------------


def test_the_announcement_lists_held_renames_newest_first_within_its_bounds(db, sysop, linked):
    now = datetime.now(timezone.utc)
    accounts = [create_user(db, f"user{i}", password="password") for i in range(MAX_ANNOUNCED_RENAMES + 2)]
    for account in accounts:
        rename_user(db, account, f"{account.username}x", renamed_by=sysop)
    # One rename too old to announce.
    db.connection.execute(
        "UPDATE username_changes SET renamed_at = ? WHERE old_username = 'user0'",
        (stamp(now - timedelta(days=ANNOUNCED_RENAME_DAYS + 1)),),
    )
    db.connection.commit()

    announced = announced_renames(db)

    assert len(announced) == MAX_ANNOUNCED_RENAMES
    assert announced[0]["from"] == f"user{MAX_ANNOUNCED_RENAMES + 1}"
    assert all(entry["from"] != "user0" for entry in announced)
    assert [e["at"] for e in announced] == sorted((e["at"] for e in announced), reverse=True)
    assert set(announced[0]) == {"from", "to", "at"}


def test_a_released_or_never_held_rename_is_not_announced(db, sysop, alice):
    rename_user(db, alice, "nib", renamed_by=sysop)  # Link never ran: nothing held
    assert announced_renames(db) == []

    mark_link_has_run(db)
    rename_user(db, get_user_by_id(db, alice.id), "quill", renamed_by=sysop)
    assert [(e["from"], e["to"]) for e in announced_renames(db)] == [("nib", "quill")]
    release_retired_username(db, "nib", released_by=sysop)
    assert announced_renames(db) == []


# -- the screens ------------------------------------------------------------------


def _console(db, inputs, actor):
    from netbbs.storage.execution import DatabaseLane
    from tests.test_admin_flow import FakeSession, _run, _visible, _written_text

    session = FakeSession(inputs)
    lane = DatabaseLane(db.path)
    try:
        _run(session, lane, actor)
    finally:
        lane.close()
    return " ".join(_visible(_written_text(session)).split())


def test_a_sysop_renames_an_account_from_its_detail_screen(db, sysop, alice, linked):
    # [U]sers -> [U]ser list -> 01 alice -> [C]hange username -> nib -> [A]pply.
    text = _console(db, ["u", "u", "0", "1", "c", "nib", "a", "b", "b", "b", "b"], sysop)

    assert get_user_by_id(db, alice.id).username == "nib"
    assert "Rename alice → nib" in text
    assert "'alice' stays held for them" in text
    assert "RLogin remote door" in text
    assert "'alice' is now 'nib'" in text
    assert "Formerly: alice" in text


def test_backing_out_of_the_rename_changes_nothing(db, sysop, alice):
    text = _console(db, ["u", "u", "0", "1", "c", "nib", "b", "b", "b", "b", "b"], sysop)
    assert get_user_by_id(db, alice.id).username == "alice"
    # A node that never ran Link frees the old name, and the screen says so.
    assert "'alice' is freed" in text
    assert "stays held" not in text


def test_a_name_the_rename_would_refuse_is_refused_before_the_confirm(db, sysop, alice):
    text = _console(db, ["u", "u", "0", "1", "c", "sysop", "b", "b", "b", "b"], sysop)
    assert "Not renamed: the username 'sysop' is already taken" in text
    assert "Rename 'alice'" not in text


def test_the_sysop_approves_a_request_from_the_queue(db, sysop, alice):
    request_username_change(db, alice, "nib")
    # [U]sers shows the request waiting; [N]ame requests -> 01 -> [A]pprove -> yes.
    text = _console(db, ["u", "n", "0", "1", "a", "y", "b", "b"], sysop)

    assert "1 name request awaiting review" in text
    assert "Asks for: nib" in text
    assert "'alice' is now 'nib'" in text
    assert get_user_by_id(db, alice.id).username == "nib"


def test_the_sysop_refuses_a_request_with_a_reason(db, sysop, alice):
    request_username_change(db, alice, "nib")
    text = _console(db, ["u", "n", "0", "1", "r", "Too close to bob.", "b", "b"], sysop)

    assert "they are told at their next login" in text
    assert get_user_by_id(db, alice.id).username == "alice"
    assert pending_refusal_notices(db, alice)[0] == [
        "Your request to be renamed to 'nib' was refused: Too close to bob."
    ]


def test_the_dashboard_counts_waiting_name_requests(db, sysop, alice):
    request_username_change(db, alice, "nib")
    text = _console(db, ["b"], sysop)
    assert "Name requests 1" in text or "Name requests: 1" in text


def _profile(db, user, keys, lines=()):
    from netbbs.net.profile_flow import _edit_profile
    from netbbs.storage.execution import DatabaseLane
    from tests.test_directory_ui import FakeSession

    session = FakeSession(keys=keys, lines=list(lines))
    lane = DatabaseLane(db.path)
    try:
        asyncio.run(_edit_profile(session, lane, user))
    finally:
        lane.close()
    return session.visible_output


def _username_field_number(db, user):
    import re

    text = _profile(db, user, [">", ">", ">", ">", "b"])
    return re.findall(r"(\d\d) Username:", text)[-1]


def test_a_caller_asks_for_a_new_username_from_their_profile(db, sysop, alice):
    number = _username_field_number(db, alice)

    text = _profile(db, alice, [">", ">", ">", ">", *number, "b"], lines=["nib"])

    assert open_username_request(db, alice).requested_username == "nib"
    assert "Asked for 'nib'" in text


def test_a_refused_name_is_said_on_the_profile(db, sysop, alice):
    number = _username_field_number(db, alice)

    text = _profile(db, alice, [">", ">", ">", ">", *number, "b"], lines=["sysop"])

    assert open_username_request(db, alice) is None
    assert "Not asked: 'sysop' is reserved on this node." in text
