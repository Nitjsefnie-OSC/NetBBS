"""Scheduled SysOp announcements in chat (issue #1380).

A SysOp schedules a short notice into one channel: once, daily, weekly on a
set of weekdays, or monthly on the nth weekday ("first Friday", "last
Friday"). The node says it itself, with no bot, at a wall-clock time in the
node's display timezone (`netbbs.timeutil.get_node_timezone`).

- **Local channels.** Recorded as the existing local kind `daybreak`, the
  muted, authorless system line the midnight announcement uses, so nothing
  about `channel_messages` changes. It is always recorded, into an empty
  channel too: the channel was chosen on purpose, and a caller arriving at
  19:05 finds the 19:00 notice in scrollback.
- **Linked channels.** Local only, like the midnight line, unless the rule
  carries a Linked approval the SysOp confirmed on Save. Then it is an
  ordinary chat line by that SysOp's account, signed and carried like any
  line the SysOp types (`netbbs.net.chat_announcements`).
- **Slots.** A slot is one scheduled moment. A wall time inside a
  spring-forward gap runs at its pre-transition reading and a repeated one
  at its first occurrence, as `netbbs.backup_schedule._slot_on` settles
  them. Unlike backups, a missed slot is skipped, never caught up: a notice
  saying "tonight at 20:00" said at 23:00 is wrong.
- **Limits, checked on Save.** At most `MAX_RULES_PER_CHANNEL` rules per
  channel, and no two of a channel's rules ever within an hour of each
  other by the node's clock.

Domain functions here are synchronous and `db`-first.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from netbbs.auth.users import User, get_user_by_id, is_usable_sysop
from netbbs.chat.channels import Channel, ChannelError, get_channel_by_name
from netbbs.moderation.log import record_action
from netbbs.rendering import sanitize_text
from netbbs.storage.database import Database
from netbbs.timeutil import get_node_timezone

REPEATS = ("once", "daily", "weekly", "monthly")
#: Which occurrence of the weekday in its month a monthly rule names. A fifth
#: occurrence exists only in some months, so it is not offered: "last" is the
#: rule that means "the final one, whether fourth or fifth".
MONTH_WEEKS = (1, 2, 3, 4, -1)
MONTH_WEEK_NAMES = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAX_RULES_PER_CHANNEL = 8
MIN_SPACING = datetime.timedelta(hours=1)
MAX_TEXT_CHARS = 160
#: Every announcement line starts with this, so it reads as one in a channel.
PREFIX = "Announcement: "
#: A once rule is for a notice, not a calendar: at most this far ahead.
MAX_ONCE_AHEAD_DAYS = 366
#: How late a slot may still be said. Past this the slot counts as missed.
GRACE = datetime.timedelta(minutes=5)
#: How far ahead the spacing check looks. The weekday pattern of the
#: Gregorian calendar repeats every 28 years within 1901-2099, so every
#: combination of weekly and monthly rules that can ever meet does so here.
_SPACING_HORIZON_DAYS = 28 * 366


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
        if self.month_week is None or self.weekday is None:
            return "monthly"
        return f"monthly on the {MONTH_WEEK_NAMES[self.month_week]} {WEEKDAY_NAMES[self.weekday]}"

    def fires_on(self, date: datetime.date) -> bool:
        if self.repeat == "once":
            return date == self.date
        if self.repeat == "daily":
            return True
        if self.repeat == "weekly":
            return date.weekday() in self.weekdays
        return self.month_week is not None and self.weekday is not None and date == nth_weekday(
            date.year, date.month, self.month_week, self.weekday,
        )


@dataclass(frozen=True)
class Announcement:
    id: int
    channel_id: int
    channel_name: str
    text: str
    recurrence: Recurrence
    #: The SysOp whose Linked approval lets the line cross Link, by whose
    #: account it is then said; `None` keeps it on this node.
    linked_author_id: int | None
    linked_author_name: str | None
    handled_until: datetime.datetime

    @property
    def linked(self) -> bool:
        return self.linked_author_id is not None

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


def nth_weekday(year: int, month: int, nth: int, weekday: int) -> datetime.date | None:
    """The `nth` (1-4, or -1 for the last) `weekday` of the month, or `None`
    for a fifth occurrence the month does not have."""
    if nth == -1:
        following = datetime.date(year + month // 12, month % 12 + 1, 1)
        last = following - datetime.timedelta(days=1)
        return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)
    first = datetime.date(year, month, 1)
    day = first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    return day if day.month == month else None


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
            # A fifth weekday exists only in some months; "last" is the rule
            # for the final one.
            raise AnnouncementError("Week of month must be first, second, third, fourth or last.")
        if recurrence.weekday is None or not 0 <= recurrence.weekday <= 6:
            raise AnnouncementError("A monthly announcement needs a weekday.")


def _local_slots(recurrence: Recurrence, start: datetime.date, end: datetime.date) -> list[datetime.datetime]:
    """Wall-clock slots from `start` to `end` inclusive, as naive local times:
    "an hour apart" is read on the node's clock."""
    clock = datetime.time(recurrence.hour, recurrence.minute)
    if recurrence.repeat == "once":
        dates = [recurrence.date] if recurrence.date is not None and start <= recurrence.date <= end else []
    else:
        dates = [start + datetime.timedelta(days=n) for n in range((end - start).days + 1)]
        dates = [d for d in dates if recurrence.fires_on(d)]
    return [datetime.datetime.combine(d, clock) for d in dates]


def check_spacing(
    candidate: Recurrence, others: list[Announcement], *, now: datetime.datetime, tz: datetime.tzinfo,
) -> None:
    """Refuse a rule that would ever come within `MIN_SPACING` of another of
    its channel's rules still to come. Past slots (a once already said)
    never count."""
    local_now = now.astimezone(tz).replace(tzinfo=None)
    start = local_now.date()
    end = start + datetime.timedelta(days=_SPACING_HORIZON_DAYS)
    timeline: list[tuple[datetime.datetime, Announcement | None]] = [
        (slot, None) for slot in _local_slots(candidate, start, end) if slot > local_now
    ]
    if not timeline:
        return
    for other in others:
        timeline.extend((slot, other) for slot in _local_slots(other.recurrence, start, end) if slot > local_now)
    timeline.sort(key=lambda entry: entry[0])
    # A rule fires at most once a day, so two of its own slots are never
    # close, and a slot of this rule close to another rule's has a close
    # neighbour of another rule. The other rules already passed this check.
    for (first, first_rule), (second, second_rule) in zip(timeline, timeline[1:]):
        if second - first >= MIN_SPACING or (first_rule is None) == (second_rule is None):
            continue
        other = first_rule or second_rule
        mine = second if first_rule is not None else first
        minutes = int((second - first).total_seconds() // 60)
        raise AnnouncementError(
            f"Announcements in one channel must be at least an hour apart: on {mine:%a %Y-%m-%d} this one "
            f"at {mine:%H:%M} comes {minutes} minutes from #{other.id} ({other.recurrence.describe()})."
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
        linked_author_id=row["linked_author_id"], linked_author_name=row["linked_author_name"],
        handled_until=_parse_utc(row["handled_until"]),
    )


_SELECT = """
    SELECT a.*, c.name AS channel_name, u.username AS linked_author_name
      FROM chat_announcements AS a
      JOIN channels AS c ON c.id = a.channel_id
      LEFT JOIN users AS u ON u.id = a.linked_author_id
"""


def list_announcements(db: Database, *, channel_id: int | None = None) -> list[Announcement]:
    """Every rule, by channel then time of day. A channel hidden from Link
    (issue #683) keeps its rules, which wait with it."""
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
    and not bridged to MRC (whose rooms are never Link-able)."""
    from netbbs.link.channels import is_channel_linked

    row = db.connection.execute("SELECT mrc_room FROM channels WHERE id = ?", (channel.id,)).fetchone()
    return is_channel_linked(db, channel) and not (row is not None and row["mrc_room"])


def save_announcement(
    db: Database,
    *,
    actor: User,
    channel_name: str,
    text: str,
    recurrence: Recurrence,
    linked: bool = False,
    announcement_id: int | None = None,
    now: datetime.datetime | None = None,
) -> Announcement:
    """Create or replace a rule. Saving counts from `now`: a rule never fires
    for a slot already past when it was saved. A Linked approval is recorded
    under `actor`, whose account the line is then said by on other nodes."""
    sysop = _require_sysop(db, actor)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    body = clean_text(text)
    validate_recurrence(recurrence)
    try:
        channel = get_channel_by_name(db, channel_name)
    except ChannelError:
        raise AnnouncementError("Choose a channel.") from None
    if linked and not channel_is_linkable(db, channel):
        raise AnnouncementError(
            f"#{channel.name} is not a Linked channel, so there is nothing to approve. Turn Linked approval off."
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
    check_spacing(recurrence, others, now=now, tz=tz)
    weekdays = sum(1 << d for d in recurrence.weekdays) if recurrence.repeat == "weekly" else 0
    values = (
        channel.id, body, recurrence.repeat, recurrence.time_text,
        recurrence.date.isoformat() if recurrence.repeat == "once" else None,
        weekdays,
        recurrence.month_week if recurrence.repeat == "monthly" else None,
        recurrence.weekday if recurrence.repeat == "monthly" else None,
        sysop.id if linked else None,
        _iso(now),
    )
    with db.connection:
        if announcement_id is None:
            cursor = db.connection.execute(
                """INSERT INTO chat_announcements
                       (channel_id, body, repeat, time, once_date, weekdays, month_week, month_weekday,
                        linked_author_id, handled_until, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, sysop.id, _iso(now)),
            )
            announcement_id = cursor.lastrowid
        else:
            cursor = db.connection.execute(
                """UPDATE chat_announcements
                      SET channel_id = ?, body = ?, repeat = ?, time = ?, once_date = ?, weekdays = ?,
                          month_week = ?, month_weekday = ?, linked_author_id = ?, handled_until = ?
                    WHERE id = ?""",
                (*values, announcement_id),
            )
            if cursor.rowcount == 0:
                raise AnnouncementError("That announcement no longer exists.")
    saved = get_announcement(db, announcement_id)
    assert saved is not None
    record_action(
        db, actor=sysop, action="save_chat_announcement", object_type="channel", object_id=channel.id,
        detail=f"announcement #{saved.id} in #{channel.name}: {recurrence.describe()}; "
               f"Linked approval={'yes' if linked else 'no'}; text={body!r}",
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
