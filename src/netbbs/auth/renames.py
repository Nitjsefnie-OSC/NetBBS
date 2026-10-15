"""Renaming a username (design doc §16, issue #1111; implemented in #1306).

A caller asks for a new name from their own Profile; the request is checked
exactly as self-registration checks a name and waits beside pending
registrations until the SysOp approves or refuses it. A SysOp can also rename
an account directly from its detail screen. Either way the rename is one
transaction:

- `users.username` changes; the account id, and everything keyed on it, stays;
- the old name goes into `retired_usernames` under #594's rule (a node that
  has ever run Link), with the renamed account's id, so Link mail, FTN
  netmail and `/msg` to the old name still reach the account here;
- `username_changes` records the rename, which is the 30-day limit's
  evidence, the account's former names, and what the endpoint descriptor
  announces to peers as `user_renames`;
- the moderation log gets `rename_user` with both names.

History keeps the name it was written under (§16 Decision 3): posts, files
and sent mail store the author's name as a snapshot and are not rewritten.

Domain functions only: synchronous and `db`-first. Ending the account's live
sessions is the caller's (`netbbs.net`), and so is Link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from netbbs.auth.users import (
    AuthError,
    User,
    UserManagementError,
    _get_user_by_id,
    _require_sysop,
    _validate_username,
    deletion_retires_username,
    get_user_by_id,
    get_user_by_username,
    is_username_retired,
    self_service_username_problem,
    username_taken_message,
)
from netbbs.storage.database import Database
from netbbs.timeutil import utc_now_iso

#: §16 Decision 1: one approved rename per account per 30 days. A SysOp's
#: direct rename is not limited, but it does count as the account's latest.
RENAME_INTERVAL = timedelta(days=30)

#: The longest refusal reason a SysOp can give. One line, shown at login.
MAX_REFUSAL_REASON_LENGTH = 200


class UsernameChangeError(Exception):
    """A request the caller cannot make, in words for that caller."""


@dataclass(frozen=True)
class UsernameChangeRequest:
    id: int
    user_id: int
    username: str
    requested_username: str
    requested_at: str


@dataclass(frozen=True)
class UsernameChange:
    old_username: str
    new_username: str
    renamed_at: str


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_request(row) -> UsernameChangeRequest:
    return UsernameChangeRequest(
        id=row["id"], user_id=row["user_id"], username=row["username"],
        requested_username=row["requested_username"], requested_at=row["requested_at"],
    )


_REQUEST_SELECT = """
    SELECT r.id, r.user_id, u.username, r.requested_username, r.requested_at
      FROM username_change_requests AS r JOIN users AS u ON u.id = r.user_id
"""


def open_username_request(db: Database, user: User) -> UsernameChangeRequest | None:
    """`user`'s open request, if they have one."""
    row = db.connection.execute(
        _REQUEST_SELECT + " WHERE r.user_id = ? AND r.status = 'open'", (user.id,)
    ).fetchone()
    return None if row is None else _row_to_request(row)


def list_open_username_requests(db: Database) -> list[UsernameChangeRequest]:
    """Every open request, oldest first: the order they were asked in."""
    return [
        _row_to_request(row)
        for row in db.connection.execute(
            _REQUEST_SELECT + " WHERE r.status = 'open' ORDER BY r.requested_at, r.id"
        ).fetchall()
    ]


def count_open_username_requests(db: Database) -> int:
    return db.connection.execute(
        "SELECT COUNT(*) FROM username_change_requests WHERE status = 'open'"
    ).fetchone()[0]


def username_history(db: Database, user: User) -> list[UsernameChange]:
    """`user`'s renames, newest first."""
    return [
        UsernameChange(row["old_username"], row["new_username"], row["renamed_at"])
        for row in db.connection.execute(
            """SELECT old_username, new_username, renamed_at FROM username_changes
               WHERE user_id = ? ORDER BY renamed_at DESC, id DESC""",
            (user.id,),
        ).fetchall()
    ]


def next_request_allowed_at(db: Database, user: User, *, now: str | None = None) -> str | None:
    """When `user` may ask for a new name again, or `None` if they may now."""
    row = db.connection.execute(
        "SELECT MAX(renamed_at) AS latest FROM username_changes WHERE user_id = ?", (user.id,)
    ).fetchone()
    if row is None or row["latest"] is None:
        return None
    allowed = _parse(row["latest"]) + RENAME_INTERVAL
    if _parse(now or utc_now_iso()) >= allowed:
        return None
    return _stamp(allowed)


def _same_name(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def request_username_change(
    db: Database, user: User, new_username: str, *, now: str | None = None
) -> UsernameChangeRequest:
    """Ask the SysOp to rename `user` to `new_username` (§16 Decision 1).

    Checked as self-registration checks a name. Replaces the account's open
    request, if any: one open request per account. Raises
    `UsernameChangeError` with a sentence for the caller.
    """
    from netbbs.moderation.log import record_action_without_commit

    new_username = new_username.strip()
    now_value = now or utc_now_iso()
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        current = get_user_by_id(db, user.id)
        if current is None or current.created_at != user.created_at or current.disabled_at is not None:
            raise UsernameChangeError("this account can no longer ask for a new username")
        if current.pending_approval:
            raise UsernameChangeError("this account is still awaiting approval")
        if _same_name(new_username, current.username):
            raise UsernameChangeError(f"{current.username!r} is already your username")
        problem = self_service_username_problem(db, new_username)
        if problem is not None:
            raise UsernameChangeError(problem)
        allowed_at = next_request_allowed_at(db, current, now=now_value)
        if allowed_at is not None:
            raise UsernameChangeError(
                f"your username changed less than 30 days ago; you can ask again from {allowed_at[:10]}"
            )
        db.connection.execute(
            "DELETE FROM username_change_requests WHERE user_id = ? AND status = 'open'", (current.id,)
        )
        cursor = db.connection.execute(
            """INSERT INTO username_change_requests (user_id, requested_username, requested_at)
               VALUES (?, ?, ?)""",
            (current.id, new_username, now_value),
        )
        record_action_without_commit(
            db, actor=current, action="request_username_change", target_user_id=current.id,
            detail=f"asked to be renamed from {current.username!r} to {new_username!r}",
        )
        request_id = cursor.lastrowid
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()
    return UsernameChangeRequest(request_id, user.id, user.username, new_username, now_value)


def withdraw_username_request(db: Database, user: User) -> bool:
    """Withdraw `user`'s open request. `False` if there was none (it was
    decided meanwhile)."""
    from netbbs.moderation.log import record_action_without_commit

    db.connection.execute("BEGIN IMMEDIATE")
    try:
        row = db.connection.execute(
            "SELECT requested_username FROM username_change_requests WHERE user_id = ? AND status = 'open'",
            (user.id,),
        ).fetchone()
        if row is None:
            db.connection.rollback()
            return False
        db.connection.execute(
            "DELETE FROM username_change_requests WHERE user_id = ? AND status = 'open'", (user.id,)
        )
        record_action_without_commit(
            db, actor=user, action="withdraw_username_change", target_user_id=user.id,
            detail=f"withdrew the request to be renamed to {row['requested_username']!r}",
        )
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()
    return True


def _open_request_row(db: Database, request_id: int):
    row = db.connection.execute(
        "SELECT * FROM username_change_requests WHERE id = ? AND status = 'open'", (request_id,)
    ).fetchone()
    if row is None:
        raise UserManagementError("that request was already decided or withdrawn")
    return row


def refuse_username_request(db: Database, request_id: int, *, refused_by: User, reason: str) -> None:
    """Turn a request down. The caller is told `reason` at their next login."""
    from netbbs.moderation.log import record_action_without_commit

    reason = " ".join(reason.split())
    if not reason:
        raise UserManagementError("a refusal needs a reason; the caller is shown it")
    if len(reason) > MAX_REFUSAL_REASON_LENGTH:
        raise UserManagementError(f"keep the reason to {MAX_REFUSAL_REASON_LENGTH} characters")
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        _require_sysop(db, refused_by)
        row = _open_request_row(db, request_id)
        account = _get_user_by_id(db, row["user_id"])
        db.connection.execute(
            """UPDATE username_change_requests SET status = 'refused', reason = ?, decided_at = ?
               WHERE id = ?""",
            (reason, utc_now_iso(), request_id),
        )
        record_action_without_commit(
            db, actor=refused_by, action="refuse_username_change", target_user_id=account.id,
            detail=(
                f"refused renaming {account.username!r} to {row['requested_username']!r}: {reason}"
            ),
        )
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()


def approve_username_request(db: Database, request_id: int, *, approved_by: User) -> tuple[User, User]:
    """Rename the account as it asked. Returns the account before and after.

    The name is checked again as a self-registration would check it: it may
    have been taken, or retired, since it was asked for.
    """
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        _require_sysop(db, approved_by)
        row = _open_request_row(db, request_id)
        current = _get_user_by_id(db, row["user_id"])
        wanted = row["requested_username"]
        problem = self_service_username_problem(db, wanted)
        if problem is not None:
            raise UserManagementError(f"{wanted!r} can no longer be given: {problem}")
        renamed = _rename_without_commit(db, current, wanted, actor=approved_by, requested=True)
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()
    return current, renamed


def rename_user(db: Database, target: User, new_username: str, *, renamed_by: User) -> tuple[User, User]:
    """A SysOp renames an account directly (§16 Decision 1): a name chosen to
    offend, or a support request by mail. Checked as a SysOp's account
    creation is: the grammar, and a name nobody holds or held. Not limited
    by the 30-day rule. Returns the account before and after."""
    new_username = new_username.strip()
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        _require_sysop(db, renamed_by)
        current = _get_user_by_id(db, target.id)
        if current.created_at != target.created_at:
            raise UserManagementError("that account no longer exists")
        renamed = _rename_without_commit(db, current, new_username, actor=renamed_by, requested=False)
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()
    return current, renamed


def sysop_rename_problem(db: Database, target: User, new_username: str) -> str | None:
    """Why a SysOp may not rename `target` to `new_username`, or `None`.

    What a SysOp's account creation checks: the grammar, and a name nobody
    holds and nobody is held for. Staff titles and look-alikes are the SysOp's
    own choice here, as they are when creating an account by hand. Reads only,
    so a screen can ask before its confirmation and the rename asks again."""
    if _same_name(new_username, target.username):
        return f"{target.username!r} is already this account's username"
    try:
        _validate_username(new_username)
    except AuthError as exc:
        return str(exc)
    taken = db.connection.execute(
        "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (new_username,)
    ).fetchone()
    if taken is not None:
        return username_taken_message(new_username)
    if is_username_retired(db, new_username):
        return (
            f"{new_username!r} is a held name: it belonged to a deleted account or one renamed "
            "away from it. Release it first under Users -> Held names if that is what you intend."
        )
    return None


def _rename_without_commit(
    db: Database, current: User, new_username: str, *, actor: User, requested: bool
) -> User:
    from netbbs.moderation.log import record_action_without_commit

    problem = sysop_rename_problem(db, current, new_username)
    if problem is not None:
        raise UserManagementError(problem)
    now_value = utc_now_iso()
    old_username = current.username
    db.connection.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, current.id))
    # §16 Decision 2, under #594's rule. Asked of the fresh row, inside this
    # transaction, exactly as a deletion asks it.
    if deletion_retires_username(db, current):
        db.connection.execute(
            "INSERT OR REPLACE INTO retired_usernames (username, retired_at, user_id) VALUES (?, ?, ?)",
            (old_username, now_value, current.id),
        )
    db.connection.execute(
        """INSERT INTO username_changes (user_id, old_username, new_username, renamed_at, requested)
           VALUES (?, ?, ?, ?, ?)""",
        (current.id, old_username, new_username, now_value, int(requested)),
    )
    # One open request per account, and a rename answers it: a SysOp who
    # renamed the account directly has decided what its name is.
    db.connection.execute(
        "DELETE FROM username_change_requests WHERE user_id = ? AND status = 'open'", (current.id,)
    )
    record_action_without_commit(
        db, actor=actor, action="rename_user", target_user_id=current.id,
        detail=f"renamed {old_username!r} to {new_username!r}",
    )
    return _get_user_by_id(db, current.id)


# -- the caller's side after a decision ----------------------------------------


def pending_refusal_notices(db: Database, user: User) -> tuple[list[str], list[int]]:
    """What the caller is told at login about refused requests, and the ids
    to acknowledge once it has been shown."""
    rows = db.connection.execute(
        """SELECT id, requested_username, reason FROM username_change_requests
           WHERE user_id = ? AND status = 'refused' ORDER BY decided_at, id""",
        (user.id,),
    ).fetchall()
    lines = [
        f"Your request to be renamed to {row['requested_username']!r} was refused: {row['reason']}"
        for row in rows
    ]
    return lines, [row["id"] for row in rows]


def acknowledge_refusal_notices(db: Database, ids: list[int]) -> None:
    if not ids:
        return
    db.connection.executemany(
        "DELETE FROM username_change_requests WHERE id = ? AND status = 'refused'", [(i,) for i in ids]
    )
    db.connection.commit()


# -- delivery through the retired row (§16 Decision 2) --------------------------


def account_formerly_named(db: Database, name: str) -> User | None:
    """The account that was renamed away from `name`, if this node holds the
    name for it. A name held for a deleted account names nobody."""
    row = db.connection.execute(
        "SELECT user_id FROM retired_usernames WHERE username = ? COLLATE NOCASE AND user_id IS NOT NULL",
        (name,),
    ).fetchone()
    return None if row is None else get_user_by_id(db, row["user_id"])


def get_recipient_by_name(db: Database, name: str) -> User:
    """The account `name` reaches: the one that holds it now, or the one
    renamed away from it. Raises `AuthError` like `get_user_by_username`."""
    try:
        return get_user_by_username(db, name)
    except AuthError:
        former = account_formerly_named(db, name)
        if former is None:
            raise
        return former


# -- a session that outlived its name (§16 Decision 6) --------------------------


def renamed_account(db: Database, user: User) -> User | None:
    """`user`'s account under its new name, if a rename is why `user` (a
    session's copy) no longer reads as current. Id and creation time must
    match, and the account must still be usable."""
    current = get_user_by_id(db, user.id)
    if (
        current is None
        or current.created_at != user.created_at
        or current.username == user.username
        or current.disabled_at is not None
    ):
        return None
    return current


def ended_session_notice(db: Database, user: User) -> str:
    """What a session is told as it ends because its account changed under it."""
    renamed = renamed_account(db, user)
    if renamed is not None:
        return (
            f"Your username is now {renamed.username!r}. Log in again with it. Disconnecting."
        )
    return "Your account is no longer active. Disconnecting."


# -- what the endpoint descriptor announces (§16 Decision 4) --------------------

#: At most this many entries in a descriptor's `user_renames`.
MAX_ANNOUNCED_RENAMES = 64
#: None older than this.
ANNOUNCED_RENAME_DAYS = 400


def announced_renames(db: Database, *, now: str | None = None) -> list[dict[str, str]]:
    """The `user_renames` this node's descriptor carries: `{from, to, at}`,
    newest first, at most `MAX_ANNOUNCED_RENAMES`, none older than
    `ANNOUNCED_RENAME_DAYS`.

    Only a rename whose old name is still held for the renamed account is
    announced. A node that had never run Link when it renamed held nothing,
    and no peer can know the old name; a name the SysOp has since released
    may be someone else's now, and must not be announced as this account's.
    """
    since = _stamp(_parse(now or utc_now_iso()) - timedelta(days=ANNOUNCED_RENAME_DAYS))
    rows = db.connection.execute(
        """SELECT c.old_username, c.new_username, c.renamed_at
             FROM username_changes AS c
             JOIN retired_usernames AS r
               ON r.username = c.old_username COLLATE NOCASE AND r.user_id = c.user_id
            WHERE c.renamed_at >= ?
            ORDER BY c.renamed_at DESC, c.id DESC
            LIMIT ?""",
        (since, MAX_ANNOUNCED_RENAMES),
    ).fetchall()
    return [{"from": row["old_username"], "to": row["new_username"], "at": row["renamed_at"]} for row in rows]
