"""
Paging the SysOp (design doc §5.6 "Pages", decision 1377): a caller's
`/sysop <reason>` in chat lands in one shared queue that every SysOp and
staff member works from.

- The reason never enters a channel, its scrollback, Link or MRC. It is
  stored here and shown only to the people a page reaches
  (`is_page_recipient`): usable SysOps and every active account holding a
  staff permission. The node log and the Monitor name the caller and the
  place, never the reason.
- One open page per caller. A new `/sysop` while one is open replaces its
  reason rather than adding a second; and a caller pages at most once every
  `PAGE_COOLDOWN`. A member's limits are per account, across all their
  sessions. The guest account is shared, so a guest's are per session
  (`guest_session`), and a guest may page only while a recipient is online:
  a stored guest page has no mailbox to answer to.
- The queue is shared: one person handling a page (`[D]one`, a break-in
  chat, a mail reply) closes it for everyone.

Domain functions only: synchronous and `db`-first. Who is online, and the
live notices, are the chat command's business (`netbbs.net.chat_flow`).
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass

from netbbs.auth.users import User, current_account, list_users
from netbbs.moderation.log import record_action
from netbbs.staff import AwayNotice, _PIPE_CODE, away_notice, may_be_away, node_today
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_iso

#: The longest reason a page takes: one line, as long as Baud's reminders.
MAX_REASON_CHARS = 160

#: At most one page in this long, per account (per session for a guest).
PAGE_COOLDOWN = datetime.timedelta(minutes=10)

#: Handled pages are kept this long, then dropped; open ones until handled.
HANDLED_RETENTION = datetime.timedelta(days=30)

#: The most handled pages kept, however recent.
MAX_HANDLED_KEPT = 500

#: The most open guest pages at once. Each guest session may hold one, and a
#: new connection is a new session, so this is what bounds them.
MAX_OPEN_GUEST_PAGES = 20

OUTCOMES = ("done", "chat", "mail")

GUEST_NOBODY_ONLINE = (
    "No staff are online, and a guest's page can't be answered later. Try again later, "
    "or sign up and page from your account."
)


class PageRefused(Exception):
    """A page that was not sent or not handled; the message is a plain
    sentence for the caller or the handler."""


@dataclass(frozen=True)
class StaffPage:
    id: int
    user_id: int | None
    caller_label: str
    guest_session: str | None
    place: str
    channel: str | None
    reason: str
    created_at: str
    paged_at: str
    handled_by_label: str | None
    handled_at: str | None
    outcome: str | None

    @property
    def from_guest(self) -> bool:
        return self.guest_session is not None


@dataclass(frozen=True)
class PageResult:
    page: StaffPage
    #: An open page of this caller's whose reason was replaced.
    replaced: bool


def is_page_recipient(user: User) -> bool:
    """Who a page reaches (decision 1377 Q4): a usable SysOp, or an active
    account holding any staff permission -- whoever may mark themselves
    away (§5.6). Moderators are not: they are scoped to resources, and a
    page is usually about the node."""
    return may_be_away(user)


def list_recipients(db: Database) -> list[User]:
    """Every account a page reaches now. Resolved at delivery and on each
    screen, so a demoted or disabled account simply stops being one."""
    return [user for user in list_users(db) if is_page_recipient(user)]


def reason_problem(reason: str) -> str | None:
    """Why `reason` can't be a page's reason, or `None`."""
    if not reason:
        return "say what you need: /sysop <reason>"
    if len(reason) > MAX_REASON_CHARS:
        return f"keep the reason to {MAX_REASON_CHARS} characters (it has {len(reason)})"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in reason):
        return "the reason must be one line of plain text"
    if _PIPE_CODE.search(reason):
        return "the reason is plain text -- a pipe code such as |07 would show as typed"
    return None


def _row_to_page(row) -> StaffPage:
    return StaffPage(
        id=row["id"], user_id=row["user_id"], caller_label=row["caller_label"],
        guest_session=row["guest_session"], place=row["place"], channel=row["channel"], reason=row["reason"],
        created_at=row["created_at"], paged_at=row["paged_at"], handled_by_label=row["handled_by_label"],
        handled_at=row["handled_at"], outcome=row["outcome"],
    )


def _caller_clause(user: User, guest_session: str | None) -> tuple[str, tuple]:
    if guest_session is not None:
        return "guest_session = ?", (guest_session,)
    return "user_id = ? AND guest_session IS NULL", (user.id,)


def _minutes(delta: datetime.timedelta) -> str:
    count = max(1, math.ceil(delta.total_seconds() / 60))
    return f"{count} minute{'' if count == 1 else 's'}"


def create_page(
    db: Database, user: User, reason: str, *, place: str, channel: str | None = None,
    guest_session: str | None = None, recipient_online: bool, now: datetime.datetime | None = None,
) -> PageResult:
    """Page this node's SysOps and staff for `user` (decision 1377).

    `guest_session` identifies the session a guest pages from, and must be
    given for the guest account; `recipient_online` says whether any
    recipient has a live session, which only a guest's page depends on.
    Raises `PageRefused` with the sentence to show when the page is not
    sent: a bad reason, a caller who is staff here, the cooldown, a guest
    with nobody online, or a full guest queue."""
    from netbbs.guest import is_guest_account  # netbbs.guest imports netbbs.auth; local as staff.py does

    now = now or datetime.datetime.now(datetime.timezone.utc)
    reason = reason.strip()
    fresh = current_account(db, user)
    if fresh is None:
        raise PageRefused("your account no longer exists")
    if is_page_recipient(fresh):
        raise PageRefused("you are staff here")
    problem = reason_problem(reason)
    if problem is not None:
        raise PageRefused(problem)
    guest = is_guest_account(db, fresh)
    if guest and guest_session is None:
        raise ValueError("a guest's page needs the session it comes from")
    if not guest:
        guest_session = None
    if not list_recipients(db):
        raise PageRefused("this node has no SysOp or staff account to page")
    if guest and not recipient_online:
        raise PageRefused(GUEST_NOBODY_ONLINE)

    where, args = _caller_clause(fresh, guest_session)
    last = db.connection.execute(
        f"SELECT * FROM staff_pages WHERE {where} ORDER BY paged_at DESC LIMIT 1", args,
    ).fetchone()
    if last is not None:
        since = now - parse_utc_iso(last["paged_at"])
        if since < PAGE_COOLDOWN:
            still = " and it is still open" if last["handled_at"] is None else ""
            raise PageRefused(
                f"you paged {_minutes(since)} ago{still}. You can page again in {_minutes(PAGE_COOLDOWN - since)}"
            )
    stamp = utc_iso(now)
    open_row = db.connection.execute(
        f"SELECT id FROM staff_pages WHERE {where} AND handled_at IS NULL ORDER BY id LIMIT 1", args,
    ).fetchone()
    if open_row is not None:
        db.connection.execute(
            "UPDATE staff_pages SET reason = ?, place = ?, channel = ?, paged_at = ?, caller_label = ? WHERE id = ?",
            (reason, place, channel, stamp, fresh.username, open_row["id"]),
        )
        page_id, replaced = open_row["id"], True
    else:
        if guest:
            waiting = db.connection.execute(
                "SELECT COUNT(*) FROM staff_pages WHERE handled_at IS NULL AND guest_session IS NOT NULL"
            ).fetchone()[0]
            if waiting >= MAX_OPEN_GUEST_PAGES:
                raise PageRefused("too many guests' pages are already waiting. Try again in a little while")
        cursor = db.connection.execute(
            "INSERT INTO staff_pages "
            "(user_id, caller_label, guest_session, place, channel, reason, created_at, paged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fresh.id, fresh.username, guest_session, place, channel, reason, stamp, stamp),
        )
        page_id, replaced = cursor.lastrowid, False
    _prune(db, now)
    db.connection.commit()
    page = get_page(db, page_id)
    assert page is not None
    return PageResult(page, replaced)


def _prune(db: Database, now: datetime.datetime) -> None:
    """Drop handled pages past `HANDLED_RETENTION`, and beyond the newest
    `MAX_HANDLED_KEPT`. Open pages stay until someone handles them."""
    db.connection.execute(
        "DELETE FROM staff_pages WHERE handled_at IS NOT NULL AND handled_at < ?",
        (utc_iso(now - HANDLED_RETENTION),),
    )
    db.connection.execute(
        "DELETE FROM staff_pages WHERE handled_at IS NOT NULL AND id NOT IN ("
        "SELECT id FROM staff_pages WHERE handled_at IS NOT NULL ORDER BY handled_at DESC LIMIT ?)",
        (MAX_HANDLED_KEPT,),
    )


def get_page(db: Database, page_id: int) -> StaffPage | None:
    row = db.connection.execute("SELECT * FROM staff_pages WHERE id = ?", (page_id,)).fetchone()
    return _row_to_page(row) if row is not None else None


def list_open_pages(db: Database) -> list[StaffPage]:
    """Every open page, the longest-waiting first."""
    rows = db.connection.execute(
        "SELECT * FROM staff_pages WHERE handled_at IS NULL ORDER BY paged_at, id"
    ).fetchall()
    return [_row_to_page(row) for row in rows]


def count_open_pages(db: Database) -> int:
    return db.connection.execute("SELECT COUNT(*) FROM staff_pages WHERE handled_at IS NULL").fetchone()[0]


def handle_page(db: Database, page_id: int, actor: User, outcome: str) -> StaffPage:
    """Close page `page_id` for everyone, as handled by `actor`: `done`, or
    answered by a break-in `chat` or a `mail` reply. Audited, without the
    reason. Raises `PageRefused` when it was already handled (by whom) or
    is gone, or when `actor` no longer receives pages."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown page outcome {outcome!r}")
    fresh = current_account(db, actor)
    if fresh is None or not is_page_recipient(fresh):
        raise PageRefused("Only a SysOp or a staff member can handle a page.")
    cursor = db.connection.execute(
        "UPDATE staff_pages SET handled_at = ?, handled_by_user_id = ?, handled_by_label = ?, outcome = ? "
        "WHERE id = ? AND handled_at IS NULL",
        (utc_iso(datetime.datetime.now(datetime.timezone.utc)), fresh.id, fresh.username, outcome, page_id),
    )
    db.connection.commit()
    page = get_page(db, page_id)
    if page is None:
        raise PageRefused("That page is no longer in the queue.")
    if not cursor.rowcount:
        raise PageRefused(f"{page.handled_by_label or 'Someone'} already handled that page.")
    record_action(
        db, actor=fresh, action="handle_page", object_type="staff_page", object_id=page.id,
        target_user_id=page.user_id, detail=f"{outcome}; from {page.caller_label}",
    )
    return page


def first_back(db: Database, recipients: list[User]) -> tuple[User, AwayNotice] | None:
    """When every recipient is away (§5.6), the one expected back first --
    `staff.approvers_away_line`'s order: a dated return before an undated
    one, then the earliest. `None` when anyone is not away."""
    today = node_today(db)
    notices: list[tuple[User, AwayNotice]] = []
    for user in recipients:
        notice = away_notice(db, user, today=today)
        if notice is None:
            return None
        notices.append((user, notice))
    if not notices:
        return None
    return min(notices, key=lambda pair: (pair[1].until is None, pair[1].until or datetime.date.max, pair[1].since))
