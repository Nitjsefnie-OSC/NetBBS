"""Scheduled SysOp announcements in chat (issue #1380, design doc §16).

A SysOp schedules a short notice into one channel: once on a date, daily,
weekly on a set of weekdays, or monthly on the first to fourth of a weekday
("first Friday"). The node says it itself, with no bot, at a wall-clock time
in the node's display timezone (`netbbs.timeutil.get_node_timezone`).

- **Said as a system notice.** Recorded as the local kind `daybreak`, the
  muted, authorless line the midnight message uses, and always recorded, into
  an empty channel too: the channel was chosen on purpose, and a caller
  arriving at 19:05 finds the 19:00 notice in scrollback.
- **Linked channels.** Local only, like the midnight message, until the
  SysOp approves the channel for announcements (`chat_announcement_approvals`,
  confirmed on Save). Then a rule's line is an ordinary signed
  `channel_message` by the SysOp who saved the rule, carrying the optional
  `announcement` marker (`netbbs.net.chat_announcements`). If that account is
  deleted or no longer a usable SysOp, the rule's carriage lapses and it is
  said here only.
- **Slots.** A wall time in a spring-forward gap is said at its
  pre-transition reading, a repeated one at its first occurrence, as
  `netbbs.backup_schedule._slot_on` settles them. Unlike backups, a missed
  slot is skipped, never caught up: "tonight at 20:00" at 23:00 is wrong.
- **Limits, checked on Save.** At most `MAX_RULES_PER_CHANNEL` rules per
  channel, and no two whose times of day are less than an hour apart,
  whatever their days.

Domain functions here are synchronous and `db`-first.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from netbbs.auth.users import User, get_user_by_id, is_usable_sysop
from netbbs.chat.channels import Channel
from netbbs.moderation.log import record_action
from netbbs.rendering import sanitize_text
from netbbs.storage.database import Database
from netbbs.timeutil import get_node_timezone, utc_now_iso

REPEATS = ("once", "daily", "weekly", "monthly")
#: Which occurrence of the weekday in its month a monthly rule names: first to
#: fourth, which every month has. A fifth (or "last") is not offered.
MONTH_WEEKS = (1, 2, 3, 4)
MONTH_WEEK_NAMES = {1: "first", 2: "second", 3: "third", 4: "fourth"}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAX_RULES_PER_CHANNEL = 8
MIN_SPACING_MINUTES = 60
MAX_TEXT_CHARS = 160
#: Every announcement's body starts with this. A node that does not know the
#: marker shows the body as an ordinary line, which then still says what it is.
PREFIX = "Announcement: "
#: A once rule is for a notice, not a calendar: at most this far ahead.
MAX_ONCE_AHEAD_DAYS = 366
#: How late a slot may still be said. Past this the slot counts as missed.
GRACE = datetime.timedelta(minutes=5)


class AnnouncementError(ValueError):
    """A rule the SysOp entered cannot be saved."""


@dataclass(frozen=True)
class Recurrence:
    repeat: str = "weekly"
    hour: int = 19
    minute: int = 0
    #: Once only.
    date: datetime.date | None = None
    #: Weekly only: 0 = Monday, as `datetime.date.weekday()`.
    weekdays: frozenset[int] = frozenset()
    #: Monthly only: one of `MONTH_WEEKS`, and the weekday.
    month_week: int | None = None
    weekday: int | None = None

    @property
    def time_text(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def minute_of_day(self) -> int:
        return self.hour * 60 + self.minute

    def describe(self) -> str:
        """"weekly on Mon, Thu at 19:00" -- short enough for a table cell."""
        return f"{self.pattern_text()} at {self.time_text}"

    def pattern_text(self) -> str:
        if self.repeat == "once":
            return f"once on {self.date.isoformat()}" if self.date else "once"
        if self.repeat == "daily":
            return "daily"
        if self.repeat == "weekly":
            return "weekly on " + (", ".join(WEEKDAY_SHORT[d] for d in sorted(self.weekdays)) or "no day")
        if self.month_week not in MONTH_WEEK_NAMES or self.weekday is None:
            return "monthly"
        return f"monthly on the {MONTH_WEEK_NAMES[self.month_week]} {WEEKDAY_NAMES[self.weekday]}"

    def fires_on(self, date: datetime.date) -> bool:
        if self.repeat == "once":
            return date == self.date
        if self.repeat == "daily":
            return True
        if self.repeat == "weekly":
            return date.weekday() in self.weekdays
        return (self.month_week in MONTH_WEEKS and self.weekday is not None
                and date == nth_weekday(date.year, date.month, self.month_week, self.weekday))


@dataclass(frozen=True)
class Announcement:
    id: int
    channel_id: int
    channel_name: str
    text: str
    recurrence: Recurrence
    #: The SysOp who saved the rule; `None` once that account is deleted.
    saved_by_id: int | None
    saved_by_name: str | None
    #: Whether the rule's channel is approved for announcements on Link.
    channel_approved: bool
    handled_until: datetime.datetime

    @property
    def body(self) -> str:
        return PREFIX + self.text


# -- parsing -----------------------------------------------------------------


def parse_time(text: str) -> tuple[int, int]:
    """`HH:MM`, 24-hour, as the backup schedule takes it."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if match is None:
        raise AnnouncementError(f"Time must look like 19:00 (24-hour), not {text!r}.")
    hour, minute = int(match[1]), int(match[2])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise AnnouncementError(f"Time must be between 00:00 and 23:59, not {text!r}.")
    return hour, minute


def parse_date(text: str) -> datetime.date:
    """`YYYY-MM-DD` only: the node's display format may put day and month
    either way round, so `01.11` would be ambiguous."""
    if not re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", text or ""):
        raise AnnouncementError(f"Date must look like 2026-10-23 (year-month-day), not {text!r}.")
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError:
        raise AnnouncementError(f"{text.strip()} is not a date on the calendar.") from None


def clean_text(text: str) -> str:
    """One line of 1-160 plain characters."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise AnnouncementError("Text must not be empty.")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise AnnouncementError(f"Text must be at most {MAX_TEXT_CHARS} characters, not {len(cleaned)}.")
    if sanitize_text(cleaned) != cleaned:
        raise AnnouncementError("Text must be plain, without terminal control characters.")
    return cleaned


# -- calendar ----------------------------------------------------------------


def nth_weekday(year: int, month: int, nth: int, weekday: int) -> datetime.date:
    """The `nth` (1-4) `weekday` of the month; every month has four."""
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def slot_on(date: datetime.date, recurrence: Recurrence, tz: datetime.tzinfo) -> datetime.datetime:
    """The slot on `date` as a UTC instant -- `backup_schedule._slot_on`'s
    reading of a skipped or repeated wall time."""
    local = datetime.datetime.combine(date, datetime.time(recurrence.hour, recurrence.minute), tzinfo=tz)
    return local.astimezone(datetime.timezone.utc)


def latest_slot(recurrence: Recurrence, at: datetime.datetime, tz: datetime.tzinfo) -> datetime.datetime | None:
    """The most recent slot at or before `at`, looking back far enough for
    any rule (a monthly rule's gap is at most five weeks)."""
    today = at.astimezone(tz).date()
    for back in range(-1, 40):
        date = today - datetime.timedelta(days=back)
        if recurrence.fires_on(date):
            slot = slot_on(date, recurrence, tz)
            if slot <= at:
                return slot
    return None


def next_slot(recurrence: Recurrence, after: datetime.datetime, tz: datetime.tzinfo) -> datetime.datetime | None:
    """The first slot strictly after `after`, or `None` (a past once)."""
    if recurrence.repeat == "once":
        if recurrence.date is None:
            return None
        slot = slot_on(recurrence.date, recurrence, tz)
        return slot if slot > after else None
    today = after.astimezone(tz).date()
    for ahead in range(-1, 40):
        date = today + datetime.timedelta(days=ahead)
        if recurrence.fires_on(date):
            slot = slot_on(date, recurrence, tz)
            if slot > after:
                return slot
    return None


# -- validation --------------------------------------------------------------


def validate_recurrence(recurrence: Recurrence) -> None:
    if recurrence.repeat not in REPEATS:
        raise AnnouncementError(f"Repeat must be one of {', '.join(REPEATS)}.")
    if not (0 <= recurrence.hour <= 23 and 0 <= recurrence.minute <= 59):
        raise AnnouncementError("Time must be between 00:00 and 23:59.")
    if recurrence.repeat == "once" and recurrence.date is None:
        raise AnnouncementError("A once announcement needs a date.")
    if recurrence.repeat == "weekly":
        if not recurrence.weekdays:
            raise AnnouncementError("A weekly announcement needs at least one weekday.")
        if not all(0 <= d <= 6 for d in recurrence.weekdays):
            raise AnnouncementError("Weekdays must be Monday to Sunday.")
    if recurrence.repeat == "monthly":
        if recurrence.month_week not in MONTH_WEEKS:
            raise AnnouncementError("Week of month must be first, second, third or fourth.")
        if recurrence.weekday is None or not 0 <= recurrence.weekday <= 6:
            raise AnnouncementError("A monthly announcement needs a weekday.")


def check_spacing(candidate: Recurrence, others: list[Announcement]) -> None:
    """Refuse a rule whose time of day is less than an hour from another of
    its channel's rules, whatever their days (decision 4). Read round the
    clock, so 23:30 and 00:10 are 40 minutes apart."""
    for other in others:
        gap = abs(candidate.minute_of_day - other.recurrence.minute_of_day)
        gap = min(gap, 24 * 60 - gap)
        if gap < MIN_SPACING_MINUTES:
            raise AnnouncementError(
                f"Announcements in one channel must be at least an hour apart: {candidate.time_text} is "
                f"{gap} minutes from #{other.id} at {other.recurrence.time_text} ({other.recurrence.pattern_text()})."
            )


# -- storage -----------------------------------------------------------------


def _parse_utc(value: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.timezone.utc)


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).isoformat()


def _row_to_announcement(row) -> Announcement:
    weekdays = frozenset(d for d in range(7) if row["weekdays"] & (1 << d))
    return Announcement(
        id=row["id"], channel_id=row["channel_id"], channel_name=row["channel_name"], text=row["body"],
        recurrence=Recurrence(
            repeat=row["repeat"], hour=int(row["time"][:2]), minute=int(row["time"][3:5]),
            date=datetime.date.fromisoformat(row["once_date"]) if row["once_date"] else None,
            weekdays=weekdays, month_week=row["month_week"], weekday=row["month_weekday"],
        ),
        saved_by_id=row["saved_by"], saved_by_name=row["saved_by_name"],
        channel_approved=bool(row["approved"]),
        handled_until=_parse_utc(row["handled_until"]),
    )


_SELECT = """
    SELECT a.*, c.name AS channel_name, u.username AS saved_by_name,
           (p.channel_id IS NOT NULL) AS approved
      FROM chat_announcements AS a
      JOIN channels AS c ON c.id = a.channel_id
      LEFT JOIN users AS u ON u.id = a.saved_by
      LEFT JOIN chat_announcement_approvals AS p ON p.channel_id = a.channel_id
"""


def list_announcements(db: Database, *, channel_id: int | None = None) -> list[Announcement]:
    """Every rule, or a channel's, by channel then time of day."""
    where, params = ("WHERE a.channel_id = ?", (channel_id,)) if channel_id is not None else ("", ())
    rows = db.connection.execute(
        f"{_SELECT} {where} ORDER BY c.name COLLATE NOCASE, a.time, a.id", params,
    ).fetchall()
    return [_row_to_announcement(row) for row in rows]


def get_announcement(db: Database, announcement_id: int) -> Announcement | None:
    row = db.connection.execute(f"{_SELECT} WHERE a.id = ?", (announcement_id,)).fetchone()
    return _row_to_announcement(row) if row is not None else None


def _require_sysop(db: Database, actor: User) -> User:
    fresh = get_user_by_id(db, actor.id)
    if fresh is None or not is_usable_sysop(fresh):
        raise AnnouncementError("Only an active SysOp may schedule announcements.")
    return fresh


def channel_is_linkable(db: Database, channel: Channel) -> bool:
    """Whether a Linked approval means anything for `channel`: it is Linked
    and not bridged to MRC, whose rooms take announcements locally only."""
    from netbbs.link.channels import is_channel_linked

    row = db.connection.execute("SELECT mrc_room FROM channels WHERE id = ?", (channel.id,)).fetchone()
    return is_channel_linked(db, channel) and not (row is not None and row["mrc_room"])


def channel_approved(db: Database, channel_id: int) -> bool:
    return db.connection.execute(
        "SELECT 1 FROM chat_announcement_approvals WHERE channel_id = ?", (channel_id,),
    ).fetchone() is not None


def carrying_author(db: Database, announcement: Announcement, channel: Channel) -> User | None:
    """The account an announcement is carried on Link by, or `None` when it
    is said on this node only: the channel is not a Linked one approved for
    announcements, or the SysOp who saved the rule is gone or no longer a
    usable SysOp (decision 3: the carriage lapses)."""
    if not channel_approved(db, channel.id) or not channel_is_linkable(db, channel):
        return None
    author = get_user_by_id(db, announcement.saved_by_id) if announcement.saved_by_id is not None else None
    return author if author is not None and is_usable_sysop(author) else None


def save_announcement(
    db: Database,
    *,
    actor: User,
    channel: Channel,
    text: str,
    recurrence: Recurrence,
    approve_linked: bool | None = None,
    announcement_id: int | None = None,
    now: datetime.datetime | None = None,
) -> Announcement:
    """Create or replace a rule, recording `actor` as the SysOp who saved it.
    Saving counts from `now`: a rule never fires for a slot already past when
    it was saved. `approve_linked` sets the channel's announcement approval
    on Link (`None` leaves it as it is); approving needs a Linked channel."""
    sysop = _require_sysop(db, actor)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    body = clean_text(text)
    validate_recurrence(recurrence)
    if approve_linked and not channel_is_linkable(db, channel):
        raise AnnouncementError(
            f"#{channel.name} is not a Linked channel, so there is nothing to approve. Turn Other nodes off."
        )
    tz = get_node_timezone(db)
    if recurrence.repeat == "once":
        if next_slot(recurrence, now, tz) is None:
            raise AnnouncementError("That date and time have already passed.")
        if recurrence.date > now.astimezone(tz).date() + datetime.timedelta(days=MAX_ONCE_AHEAD_DAYS):
            raise AnnouncementError("A once announcement can be at most a year ahead.")
    others = [a for a in list_announcements(db, channel_id=channel.id) if a.id != announcement_id]
    if len(others) >= MAX_RULES_PER_CHANNEL:
        raise AnnouncementError(
            f"#{channel.name} already has {MAX_RULES_PER_CHANNEL} announcements, the most a channel may have. "
            "Delete one first."
        )
    # A once rule already said never comes round again; it does not hold a time.
    check_spacing(recurrence, [a for a in others if next_slot(a.recurrence, max(now, a.handled_until), tz)])
    weekdays = sum(1 << d for d in recurrence.weekdays) if recurrence.repeat == "weekly" else 0
    values = (
        channel.id, body, recurrence.repeat, recurrence.time_text,
        recurrence.date.isoformat() if recurrence.repeat == "once" else None,
        weekdays,
        recurrence.month_week if recurrence.repeat == "monthly" else None,
        recurrence.weekday if recurrence.repeat == "monthly" else None,
        sysop.id, _iso(now),
    )
    with db.connection:
        if announcement_id is None:
            cursor = db.connection.execute(
                """INSERT INTO chat_announcements
                       (channel_id, body, repeat, time, once_date, weekdays, month_week, month_weekday,
                        saved_by, handled_until, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, utc_now_iso()),
            )
            announcement_id = cursor.lastrowid
        else:
            cursor = db.connection.execute(
                """UPDATE chat_announcements
                      SET channel_id = ?, body = ?, repeat = ?, time = ?, once_date = ?, weekdays = ?,
                          month_week = ?, month_weekday = ?, saved_by = ?, handled_until = ?
                    WHERE id = ?""",
                (*values, announcement_id),
            )
            if cursor.rowcount == 0:
                raise AnnouncementError("That announcement no longer exists.")
        if approve_linked is True:
            db.connection.execute(
                "INSERT OR IGNORE INTO chat_announcement_approvals (channel_id, approved_by, approved_at) "
                "VALUES (?, ?, ?)", (channel.id, sysop.id, utc_now_iso()),
            )
        elif approve_linked is False:
            db.connection.execute("DELETE FROM chat_announcement_approvals WHERE channel_id = ?", (channel.id,))
    saved = get_announcement(db, announcement_id)
    assert saved is not None
    record_action(
        db, actor=sysop, action="save_chat_announcement", object_type="channel", object_id=channel.id,
        detail=f"announcement #{saved.id} in #{channel.name}: {recurrence.describe()}; "
               f"other nodes={'yes' if saved.channel_approved else 'no'}; text={body!r}",
    )
    return saved


def delete_announcement(db: Database, announcement_id: int, *, actor: User) -> None:
    sysop = _require_sysop(db, actor)
    existing = get_announcement(db, announcement_id)
    if existing is None:
        raise AnnouncementError("That announcement no longer exists.")
    with db.connection:
        db.connection.execute("DELETE FROM chat_announcements WHERE id = ?", (announcement_id,))
    record_action(
        db, actor=sysop, action="delete_chat_announcement", object_type="channel", object_id=existing.channel_id,
        detail=f"announcement #{existing.id} in #{existing.channel_name}: {existing.recurrence.describe()}",
    )


def next_run(db: Database, announcement: Announcement, now: datetime.datetime | None = None) -> datetime.datetime | None:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return next_slot(announcement.recurrence, max(now, announcement.handled_until), get_node_timezone(db))


def take_due(db: Database, now: datetime.datetime) -> list[Announcement]:
    """The rules whose slot has come round since they were last handled,
    marked handled first (at most once, as a backup slot is). A slot more
    than `GRACE` old -- the node was down, or busy -- is marked and skipped,
    never said late."""
    tz = get_node_timezone(db)
    due = []
    for announcement in list_announcements(db):
        slot = latest_slot(announcement.recurrence, now, tz)
        if slot is None or slot <= announcement.handled_until:
            continue
        with db.connection:
            db.connection.execute(
                "UPDATE chat_announcements SET handled_until = ? WHERE id = ?", (_iso(now), announcement.id),
            )
        if now - slot <= GRACE:
            due.append(announcement)
    return due


def earliest_next_slot(db: Database, now: datetime.datetime) -> datetime.datetime | None:
    tz = get_node_timezone(db)
    slots = [
        slot for a in list_announcements(db)
        if (slot := next_slot(a.recurrence, max(now, a.handled_until), tz)) is not None
    ]
    return min(slots, default=None)
