"""SysOp console: scheduled chat announcements (issue #1380).

Content ▸ Chat channels ▸ Announcements lists every rule on the node; a rule
is a draft editor that writes nothing before Save (design doc §3.5). A Linked
approval is the last keystroke before Save, as a bot's is.
"""

from __future__ import annotations

import datetime

from netbbs.auth.users import User
from netbbs.chat.announcements import (
    MAX_RULES_PER_CHANNEL,
    MAX_TEXT_CHARS,
    MONTH_WEEK_NAMES,
    MONTH_WEEKS,
    REPEATS,
    WEEKDAY_NAMES,
    WEEKDAY_SHORT,
    Announcement,
    AnnouncementError,
    Recurrence,
    channel_is_linkable,
    delete_announcement,
    get_announcement,
    list_announcements,
    next_run,
    next_slot,
    parse_date,
    parse_time,
    save_announcement,
)
from netbbs.chat.announcements import _require_sysop
from netbbs.chat.channels import ChannelError, get_channel_by_name, list_channels
from netbbs.link.channels import linked_channel_ids
from netbbs.net.admin_ui import _announce_line, _load_chrome, edit_resource_draft, pick_item
from netbbs.net.confirm import prompt_yes_no
from netbbs.net.location import entered, located
from netbbs.net.menu_description_preference import menu_description_level
from netbbs.net.picker import ListColumn
from netbbs.net.resource_editor import FieldSpec, bool_field, bool_step, choice_field, choice_step, text_field
from netbbs.net.session import Session
from netbbs.rendering import LINKED_COLOR, MUTED_COLOR, VALUE_COLOR, MenuEntry, menu_key, sanitize_text
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import format_for_display, get_node_timezone, resolve_display_preferences

_COLUMNS = [
    ListColumn("channel", 12, VALUE_COLOR),
    ListColumn("when", 28, VALUE_COLOR),
    ListColumn("next", 17, MUTED_COLOR),
]


def _load_list(db: Database) -> list[tuple[Announcement, str]]:
    rows = []
    for announcement in list_announcements(db):
        upcoming = next_run(db, announcement)
        rows.append((announcement, format_for_display(upcoming.isoformat(), db) if upcoming else "done"))
    return rows


@located("Announcements")
async def announcements_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    await lane.run(_require_sysop, actor)
    chrome = await _load_chrome(lane, actor)
    description_level = await lane.run(menu_description_level, actor)
    next_text: dict[int, str] = {}

    async def refresh():
        rows = await lane.run(_load_list)
        next_text.clear()
        next_text.update({a.id: text for a, text in rows})
        return [a for a, _ in rows]

    async def create():
        return await announcement_editor(session, lane, actor)

    async def delete_one(announcement):
        if await prompt_yes_no(
            session, f"Delete announcement #{announcement.id} in #{sanitize_text(announcement.channel_name)} "
            f"({announcement.recurrence.describe()})?", default=False,
        ):
            try:
                await lane.run(delete_announcement, announcement.id, actor=actor)
            except AnnouncementError as exc:
                _announce_line(session, str(exc))
            else:
                _announce_line(session, f"Announcement #{announcement.id} deleted.")
        else:
            _announce_line(session, "Cancelled. Nothing was deleted.")
        return await refresh()

    def when_cell(announcement):
        text = announcement.recurrence.describe()
        return (text, LINKED_COLOR) if announcement.linked else text

    while True:
        picked = await pick_item(
            session, await refresh(),
            name_of=lambda a: a.text,
            stable_id_of=lambda a: a.id,
            columns=_COLUMNS,
            column_values_of=lambda a: ["#" + a.channel_name, when_cell(a), next_text.get(a.id, "")],
            description_of=lambda a: f"#{a.channel_name}, {a.recurrence.describe()}"
            + (", on other nodes too" if a.linked else ""),
            title="Announcements", own=True,
            empty_message="No announcements scheduled. Create one to say something in a channel on a schedule.",
            refresh=refresh, on_create=create, item_keys={"d": delete_one},
            live_nav=(MenuEntry(label=menu_key("D", "elete selected"), brief="Remove this announcement"),),
            preamble="The node says these itself, in the node's clock. A slot missed while the node was "
                     "down is skipped, never said late.",
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            collapsed=chrome.collapsed, header_color=chrome.header_color,
            accent_color=chrome.accent_color, description_level=description_level,
        )
        if picked is None:
            return
        current = await lane.run(get_announcement, picked.id)
        if current is not None:
            await announcement_editor(session, lane, actor, announcement=current)


def _draft_recurrence(draft: dict) -> Recurrence:
    """The rule the draft describes; raises `AnnouncementError` on Save."""
    hour, minute = parse_time(draft["time"])
    repeat = draft["repeat"]
    return Recurrence(
        repeat=repeat, hour=hour, minute=minute,
        date=parse_date(draft["date"]) if repeat == "once" else None,
        weekdays=frozenset(draft["weekdays"]) if repeat == "weekly" else frozenset(),
        month_week=draft["month_week"] if repeat == "monthly" else None,
        weekday=draft["weekday"] if repeat == "monthly" else None,
    )


async def announcement_editor(
    session: Session, lane: DatabaseLane, actor: User, *, announcement: Announcement | None = None,
) -> Announcement | None:
    with entered(session, "Edit announcement" if announcement else "Create announcement"):
        chrome = await _load_chrome(lane, actor)
        description_level = await lane.run(menu_description_level, actor)

        def _context(db: Database):
            channels = [c.name for c in list_channels(db)]
            linked_ids = linked_channel_ids(db)
            linked = {c.name for c in list_channels(db) if c.id in linked_ids}
            fmt, tz_name = resolve_display_preferences(db)
            return channels, linked, fmt, tz_name, get_node_timezone(db)

        channel_names, linked_names, fmt, tz_name, tz = await lane.run(_context)
        recurrence = announcement.recurrence if announcement else Recurrence()
        draft = {
            "channel": announcement.channel_name if announcement else "",
            "text": announcement.text if announcement else "",
            "repeat": recurrence.repeat,
            "date": recurrence.date.isoformat() if recurrence.date else "",
            "weekdays": sorted(recurrence.weekdays) or [4],
            "month_week": recurrence.month_week if recurrence.month_week is not None else 1,
            "weekday": recurrence.weekday if recurrence.weekday is not None else 4,
            "time": recurrence.time_text,
            "linked": announcement.linked if announcement else False,
        }

        def preamble(values: dict) -> str:
            # Pure: no I/O, the zone and format were read above.
            try:
                upcoming = next_slot(_draft_recurrence(values), datetime.datetime.now(datetime.timezone.utc), tz)
            except AnnouncementError:
                upcoming = None
            when = (format_for_display(upcoming.isoformat(), override_format=fmt, override_timezone=tz_name)
                    if upcoming else "--")
            where = "here and on other nodes" if values["linked"] else "on this node only"
            return (f"Next: {when} ({tz_name}). Said {where}, into the channel's scrollback even when no "
                    f"one is there. At most {MAX_RULES_PER_CHANNEL} per channel, an hour apart.")

        async def pick_channel(session, lane, values):
            options = sorted(channel_names, key=str.casefold)
            picked = await pick_item(
                session, list(enumerate(options)), name_of=lambda row: row[1],
                stable_id_of=lambda row: row[0], title="Channel",
                linked_of=lambda row: row[1] in linked_names,
                empty_message="No channels yet. Create one under Chat channels first.",
                redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
                collapsed=chrome.collapsed, header_color=chrome.header_color,
                accent_color=chrome.accent_color, description_level=description_level,
            )
            if picked is not None:
                values["channel"] = picked[1]
                if picked[1] not in linked_names:
                    values["linked"] = False

        async def pick_weekdays(session, lane, values):
            async def toggle(day):
                selected = set(values["weekdays"])
                selected.symmetric_difference_update({day})
                values["weekdays"] = sorted(selected)

            await pick_item(
                session, list(range(7)), name_of=lambda d: WEEKDAY_NAMES[d], stable_id_of=lambda d: d,
                name_segments_of=lambda d: (
                    ("[x] " if d in values["weekdays"] else "[ ] ", MUTED_COLOR),
                    (WEEKDAY_NAMES[d], chrome.accent_color),
                ),
                title="Weekdays", empty_message="",
                preamble="Select rows to toggle; [B]ack returns to the draft.",
                live_label=lambda: f"{len(values['weekdays'])} selected", on_toggle=toggle,
                redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
                collapsed=chrome.collapsed, header_color=chrome.header_color,
                accent_color=chrome.accent_color, description_level=description_level,
            )

        def only(repeat, render):
            return lambda d: render(d) if d["repeat"] == repeat else f"(only for {repeat})"

        section_when = "When"
        fields = [
            FieldSpec("channel", "Channel", lambda d: sanitize_text(d["channel"]) or "(choose one)", pick_channel,
                      brief="Where it is said", section="Announcement",
                      help="The channel the announcement goes into. Any channel, MRC-bridged ones too: "
                           "the line is never relayed to MRC."),
            FieldSpec("text", "Text", lambda d: sanitize_text(d["text"]) or "(none)", text_field("text"),
                      brief=f"1-{MAX_TEXT_CHARS} characters", section="Announcement",
                      help=f"One line of up to {MAX_TEXT_CHARS} characters. It is shown as "
                           "\"Announcement: \" and your text."),
            FieldSpec("repeat", "Repeat", lambda d: d["repeat"], choice_field("repeat", list(REPEATS)),
                      step=choice_step("repeat", list(REPEATS)), brief="Once, daily, weekly or monthly",
                      section=section_when,
                      help="Once on a date; daily; weekly on the weekdays you choose; or monthly on the "
                           "first, second, third, fourth or last of one weekday."),
            FieldSpec("date", "Date (once)", only("once", lambda d: d["date"] or "(none)"), text_field("date"),
                      brief="YYYY-MM-DD", section=section_when,
                      help="The day a once announcement is said, as year-month-day, at most a year ahead."),
            FieldSpec("weekdays", "Weekdays (weekly)",
                      only("weekly", lambda d: ", ".join(WEEKDAY_SHORT[x] for x in d["weekdays"]) or "(none)"),
                      pick_weekdays, brief="One or more days", section=section_when),
            FieldSpec("month_week", "Week of month (monthly)",
                      only("monthly", lambda d: MONTH_WEEK_NAMES[d["month_week"]]),
                      choice_field("month_week", list(MONTH_WEEKS)), step=choice_step("month_week", list(MONTH_WEEKS)),
                      brief="First to fourth, or last", section=section_when,
                      help="Which of the month's weekdays: first to fourth, or last. A fifth exists only in "
                           "some months, so it is not offered; last is the final one, fourth or fifth."),
            FieldSpec("weekday", "Weekday (monthly)", only("monthly", lambda d: WEEKDAY_NAMES[d["weekday"]]),
                      choice_field("weekday", list(range(7))), step=choice_step("weekday", list(range(7))),
                      brief="The day of the week", section=section_when),
            FieldSpec("time", "Time", lambda d: d["time"], text_field("time"),
                      brief="24-hour, node's timezone", section=section_when,
                      help="HH:MM on a 24-hour clock, in the node's display timezone (Settings, Timestamp "
                           "format). A time the clocks skip is said at its reading after the change; a "
                           "repeated time, the first time round. A node not running then skips it."),
            FieldSpec("linked", "Linked approval",
                      lambda d: ("yes" if d["linked"] else "no") if d["channel"] in linked_names
                      else "(Linked channels only)",
                      bool_field("linked"), step=bool_step("linked"), brief="Also say it on other nodes",
                      section="Link",
                      help="In a Linked channel, say it on other nodes too, as a line by your account, "
                           "confirmed on Save. Without it the announcement stays on this node."),
        ]

        async def save(values):
            if not values["channel"]:
                raise AnnouncementError("Choose a channel.")
            candidate = _draft_recurrence(values)

            def _needs_approval(db: Database) -> bool:
                try:
                    channel = get_channel_by_name(db, values["channel"])
                except ChannelError:
                    raise AnnouncementError(f"#{values['channel']} no longer exists. Choose a channel.") from None
                return values["linked"] and channel_is_linkable(db, channel)

            if await lane.run(_needs_approval) and not (
                announcement is not None and announcement.linked_author_id == actor.id
                and announcement.channel_name == values["channel"]
            ):
                if not await prompt_yes_no(
                    session, f"Say this announcement on other nodes in #{sanitize_text(values['channel'])} too, "
                    f"as a line by {sanitize_text(actor.username)}? Grant Linked approval?", default=False,
                ):
                    raise AnnouncementError("Linked approval was not granted. Your draft has been kept.")
            return await lane.run(
                save_announcement, actor=actor, channel_name=values["channel"], text=values["text"],
                recurrence=candidate, linked=values["linked"],
                announcement_id=announcement.id if announcement else None,
            )

        saved = await edit_resource_draft(
            session, lane, title="Edit announcement" if announcement else "Create announcement", own=True,
            fields=fields, draft=draft, save=save, error_type=AnnouncementError,
            save_menu_text=menu_key("S", "ave"), back_menu_text=menu_key("B", "ack"), preamble=preamble,
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            collapsed=chrome.collapsed, header_color=chrome.header_color,
            accent_color=chrome.accent_color, description_level=description_level,
        )
        if saved is not None:
            _announce_line(session, f"Announcement #{saved.id} saved: #{saved.channel_name}, "
                                    f"{saved.recurrence.describe()}.")
        return saved
