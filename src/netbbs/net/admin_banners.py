"""SysOp console banner, masthead and theme-colour screens.

The welcome, main-menu, logoff and new-account banners, the board-list,
file-area and chat-channel-picker mastheads, and the theme colours. Split
out of `netbbs.net.admin_flow` (issue #1311).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from netbbs.rendering.menu import continue_prompt
from netbbs.auth.users import User
from netbbs.boards.boards import list_boards
from netbbs.boards.categories import list_top_level_categories as list_top_level_board_categories
from netbbs.chat.categories import list_top_level_categories as list_top_level_channel_categories
from netbbs.communities import meets_read_gate
from netbbs.files.areas import list_file_areas
from netbbs.files.categories import list_top_level_categories as list_top_level_file_categories
from netbbs.files.entries import count_listed_files
from netbbs.moderation.log import record_action
from netbbs.net.char_input import HELP_KEY, InputCancelled, help_key_label, reject_unhandled_key
from netbbs.net.confirm import prompt_yes_no
from netbbs.sysop_uploads import (
    BANNER as SYSOP_UPLOAD_BANNER,
    SysOpUploadTarget,
    destination_problem,
)
from netbbs.net.help_overlay import show_help, show_menu_help
from netbbs.net.location import crumbs, located
from netbbs.net.resource_editor import (
    inline_field,
    write_field_prompt,
    write_field_message,
    FieldSpec,
)
from netbbs.net.session import (
    Session,
    physical_terminal_width,
    write_preformatted_line,
    write_prompt,
)
from netbbs.rendering.charset import ASCII, ellipsis_for
from netbbs.net.shutdown import NodeControls
from netbbs.net.menu_description_preference import menu_description_level
from netbbs.net.redraw_preference import redraw_in_place_enabled
from netbbs.net.breadcrumb_preference import breadcrumb_collapsed_enabled
from netbbs.net.color_depth_preference import effective_truecolor
from netbbs.net.node_theme import (
    accent_color_override,
    clock_color_override,
    effective_accent_color_256,
    effective_header_color_256,
    header_color_override,
    set_accent_color_override,
    set_clock_color_override,
    set_header_color_override,
)
from netbbs.net.unicode_style_preference import unicode_style_enabled
from netbbs.net.ansi_editor import edit_ansi_art
from netbbs.net.art_pacing import (
    ART_SPEEDS,
    ART_TIME_LIMITS,
    MAIN_MENU_ART,
    WELCOME_ART,
    art_speed,
    art_time_limit,
    set_art_speed,
    set_art_time_limit,
    write_preview_art,
    write_preview_art_text,
)
from netbbs.net.banner_fields import banner_fields, count_callers_online
from netbbs.net.welcome_banner import (
    MAX_BANNER_SIZE_BYTES,
    banner_path,
    is_welcome_banner_credit_enabled,
    load_welcome_banner,
    pre_login_unicode_style,
    set_welcome_banner_credit_enabled,
    set_welcome_banner_enabled,
    welcome_banner_status,
)
from netbbs.net.banner_presets import (
    BOARD_LIST_MASTHEAD_PRESETS,
    CHAT_CHANNEL_PICKER_MASTHEAD_PRESETS,
    FILE_AREA_MASTHEAD_PRESETS,
    LOGOFF_BANNER_PRESETS,
    MAIN_MENU_BANNER_PRESETS,
    NEW_ACCOUNT_BANNER_AFTER_PRESETS,
    NEW_ACCOUNT_BANNER_BEFORE_PRESETS,
    WELCOME_BANNER_PRESETS,
    load_board_list_masthead_preset,
    load_chat_channel_picker_masthead_preset,
    load_file_area_masthead_preset,
    load_logoff_banner_preset,
    load_main_menu_banner_preset,
    load_new_account_banner_after_preset,
    load_new_account_banner_before_preset,
    load_welcome_banner_preset,
)
from netbbs.net.main_menu_banner import (
    MASTHEAD_MODE,
    MAX_MASTHEAD_SIZE_BYTES,
    SLOTS_MODE,
    is_main_menu_banner_enabled,
    load_main_menu_banner,
    main_menu_art_mode,
    main_menu_banner_path,
    main_menu_banner_status,
    set_main_menu_art_mode,
    set_main_menu_banner_enabled,
)
from netbbs.rendering.art_slots import (
    ListSlotRow,
    SlotArt,
    describe_items,
    banner_slot_notes,
    describe_slots,
    fill_field_slots,
    list_name_width,
    list_slot_fits,
    parse_slot_art,
    render_list_slot_art,
)
from netbbs.rendering.ansi import clear_screen, move_cursor
from netbbs.net.logoff_banner import (
    MAX_LOGOFF_BANNER_SIZE_BYTES,
    load_logoff_banner,
    logoff_banner_path,
    logoff_banner_status,
    set_logoff_banner_enabled,
)
from netbbs.net.new_account_banner_before import (
    MAX_NEW_ACCOUNT_BANNER_BEFORE_SIZE_BYTES,
    load_new_account_banner_before,
    new_account_banner_before_path,
    new_account_banner_before_status,
    set_new_account_banner_before_enabled,
)
from netbbs.net.new_account_banner_after import (
    MAX_NEW_ACCOUNT_BANNER_AFTER_SIZE_BYTES,
    load_new_account_banner_after,
    new_account_banner_after_path,
    new_account_banner_after_status,
    set_new_account_banner_after_enabled,
)
from netbbs.net.list_art import (
    BOARD_LIST,
    CHAT_CHANNEL_PICKER,
    FILE_AREA,
    list_art_mode,
    list_slot_fields,
    read_list_art,
    set_list_art_mode,
)
from netbbs.net.list_art import MASTHEAD_MODE as LIST_MASTHEAD_MODE
from netbbs.net.list_art import SLOTS_MODE as LIST_SLOTS_MODE
from netbbs.net.board_list_banner import (
    MAX_BOARD_LIST_BANNER_SIZE_BYTES,
    is_board_list_banner_enabled,
    board_list_banner_path,
    board_list_banner_status,
    load_board_list_banner,
    set_board_list_banner_enabled,
)
from netbbs.net.file_area_banner import (
    MAX_FILE_AREA_BANNER_SIZE_BYTES,
    is_file_area_banner_enabled,
    file_area_banner_path,
    file_area_banner_status,
    load_file_area_banner,
    set_file_area_banner_enabled,
)
from netbbs.net.chat_channel_picker_banner import (
    MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES,
    is_chat_channel_picker_banner_enabled,
    chat_channel_picker_banner_path,
    chat_channel_picker_banner_status,
    load_chat_channel_picker_banner,
    set_chat_channel_picker_banner_enabled,
)
from netbbs.rendering import (
    ACCENT_COLOR,
    AUTHOR_COLOR,
    CLOCK_COLOR,
    EMPHASIS_COLOR,
    ERROR_COLOR,
    HEADER_COLOR,
    LABEL_COLOR,
    METADATA_COLOR,
    MUTED_COLOR,
    RESET,
    SUCCESS_COLOR,
    VALUE_COLOR,
    WARNING_COLOR,
    MenuEntry,
    action_bar,
    colored,
    display_width,
    decode_banner_bytes,
    highlight_hotkeys,
    highlight_report,
    menu_key,
    nearest_256,
    sanitize_text,
    screen_title,
    status_badge,
    status_mark,
    wrap_to_width,
)
from netbbs.rendering.detail import Field, Section
from netbbs.rendering.sauce import Sauce, split_sauce
from netbbs.net import notices as _notices
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.net.admin_ui import (
    _announce,
    _announce_line,
    _choice_prompt,
    _fitted_menu,
    _format_bytes,
    _menu_row,
    _receive_sysop_upload,
    _remove_file_entry,
    _upload_unavailable_reason,
    _write_sections,
    _write_wrapped_subtitle,
    edit_resource_draft,
    pick_item,
)
from netbbs.net.admin_fields import _EDIT_HINT, _read_seeded_line, _resolve_redraw_preference


def _announce_saved(session: Session, lead: str, path: object, tail: str) -> None:
    """An outcome that names the file it saved: the success mark, `lead`
    and `tail` in the normal colours (issue #1109), the path in the value
    colour so it reads as the thing saved, and a key the tail mentions
    highlighted (issue #1083)."""
    _notices.announce_styled(
        session,
        status_mark("success") + lead + colored(str(path), fg_color=VALUE_COLOR) + highlight_hotkeys(tail),
    )


async def _write_counted(session: Session, text: str) -> int:
    """Write `text` and return the rows it leaves on screen, counting each
    line break after the last clear -- for a screen that fits its menu under
    what it has drawn (issue #662). A break before a clear is erased with it;
    counting it pushed the main-menu masthead's described menu, which fits
    80x24 to the row, into the packed one."""
    await session.write_line(text)
    return len(text.rsplit(clear_screen(), 1)[-1].split("\r\n"))


async def _write_banner_menu(
    session: Session, options: list[MenuEntry], description_level: str, *, used_rows: int, help_text: str,
) -> None:
    """A banner or masthead screen's menu and its help line, fitted under the
    `used_rows` already on screen (issue #662). Built with `_menu_row`, these
    screens budgeted the described menu against the whole terminal, so below
    72 columns, where `menu_grid` lays it out one entry per two rows, the
    screen's own title scrolled away before Choice: appeared."""
    help_text = help_text.replace("Ctrl-H", help_key_label(session))  # issue #1119
    help_rows = len(wrap_to_width(help_text, session.terminal_width)) or 1
    await session.write_line(
        "\r\n" + _fitted_menu(options, description_level, session=session, used_rows=used_rows + 1 + help_rows)
    )
    await session.write_line(colored(help_text, fg_color=MUTED_COLOR))


def _banner_status_section(
    status,
    *,
    unicode_style: bool,
    sauce: Sauce | None = None,
    credit_line: bool | None = None,
    too_wide: str = "no art",
    speed: int | None = None,
) -> Section:
    """Whether a banner or masthead is switched on, and the state of the file
    behind it -- the two facts every one of these seven menus leads with. They
    were one run-on line (`disabled -- file: x.ans (missing)`); a missing file
    behind an enabled banner is the case worth seeing, and now reads in red
    on a row of its own.

    A file with a SAUCE record (issue #929) also shows its credit, the width
    it was drawn for, and a warning when it was made for a font other than
    CP437's; the caller reads the record off the event loop
    (`_banner_sauce`). `too_wide` is what a caller whose terminal is
    narrower than the art sees instead. `credit_line` is the welcome
    banner's caller-facing credit setting, shown when given. `speed` is the
    art's playback speed (issue #929), shown when the screen has a `[S]peed`
    entry: its description alone could be packed away by a fitted menu
    (issue #662), and a SysOp who pressed it would not see what it is now.
    The `[T]ime limit` beside it needs no row here: its label, which a
    fitted menu keeps, shows its state."""
    fields = [
        Field(
            "Shown to callers",
            status_badge("ENABLED", tone="success", unicode_style=unicode_style) if status.enabled
            else status_badge("DISABLED", tone="neutral", unicode_style=unicode_style),
            styled=True,
        ),
        Field("File", status.path.name, color=METADATA_COLOR),
        Field("On disk", _format_bytes(status.size_bytes), color=VALUE_COLOR) if status.exists
        else Field("On disk", "missing", color=ERROR_COLOR if status.enabled else MUTED_COLOR),
    ]
    if sauce is not None:
        # Title and artist apart (issue #1083), each a plain field so the
        # panel wraps it: a styled field is never wrapped, and a SAUCE
        # credit can run to 80 columns (review on #1095).
        title, artist = _credit_parts(sauce)
        if title:
            fields.append(Field("Art", title, color=EMPHASIS_COLOR))
        if artist:
            fields.append(Field("By", artist, color=AUTHOR_COLOR))
        if not (title or artist):
            fields.append(Field("Art", "(no credit in its SAUCE record)", color=MUTED_COLOR))
        if sauce.width is not None:
            fields.append(Field("Drawn for", f"{sauce.width} columns; narrower terminals get {too_wide}", color=VALUE_COLOR))
        if not sauce.font_is_cp437:
            font = "".join(ch for ch in sauce.font if ch.isprintable())
            fields.append(Field("Font", f"made for {font}; shown with CP437's characters", color=WARNING_COLOR))
    if speed is not None:
        fields.append(Field("Speed", _art_speed_brief(speed), color=VALUE_COLOR if speed else MUTED_COLOR))
    if credit_line is not None:
        fields.append(Field("Credit line", "shown under the banner" if credit_line else "off", color=VALUE_COLOR if credit_line else MUTED_COLOR))
    return Section("Status", fields)


def _credit_parts(sauce: Sauce) -> tuple[str, str]:
    """A SAUCE record's title, and its author and group as one "artist"
    ("InkWell/Nib and Quill"), each printable and sanitized; either may be
    empty."""
    def clean(text: str) -> str:
        return sanitize_text("".join(ch for ch in text if ch.isprintable()))

    who = "/".join(part for part in (clean(sauce.author), clean(sauce.group)) if part)
    return clean(sauce.title), who


def _toggle_welcome_banner_credit(db: Database, actor: User) -> bool:
    """Flip whether callers see the art's credit under the welcome banner,
    audited; returns the new setting."""
    shown = not is_welcome_banner_credit_enabled(db)
    set_welcome_banner_credit_enabled(db, shown)
    record_action(db, actor=actor, action="set_welcome_banner_credit", detail="on" if shown else "off")
    return shown


def _banner_sauce(status) -> Sauce | None:
    """The SAUCE record of the file behind `status`, or `None` when there is
    no file, it is over the size limit, or it has no record."""
    if not status.exists or (status.size_bytes or 0) > _SAVED_BANNER_READ_LIMIT:
        return None
    try:
        return split_sauce(status.path.read_bytes())[1]
    except OSError:
        return None


@located("Mastheads & banners")
async def _banners_and_mastheads_menu(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None = None
) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_banners_and_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "s":
            await session.write_line("")
            await _banners_menu(session, lane, actor, node_controls=node_controls)
            await _draw_banners_and_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "m":
            await session.write_line("")
            await _mastheads_menu(session, lane, actor)
            await _draw_banners_and_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_router_help_screen(session, header_color=header_color, unicode_style=unicode_style)
            await _draw_banners_and_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_banners_and_mastheads_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    await session.write_line("\r\n" + screen_title("Mastheads & banners",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place,
            unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    await _write_wrapped_subtitle(
        session, "Every optional SysOp-authored banner and masthead, grouped by kind."
    )
    await session.write_line(
        "\r\n" + _menu_row(
            [
                MenuEntry(label=menu_key("M", "astheads"), brief="Art above the menu and pickers"),
                MenuEntry(label=menu_key("S", "ession banners"), brief="Welcome, logoff, new-account text"),
                MenuEntry(label=menu_key("B", "ack"), brief="Return to Settings"),
            ],
            description_level,
            width=session.terminal_width,
            height=session.terminal_height,
        )
    )
    await session.write_line(colored(f"({help_key_label(session)} for help placing your own .ans files)", fg_color=MUTED_COLOR))
    await _choice_prompt(session)


@located("Welcome banner")
async def _welcome_banner_menu(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None = None
) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            await _preview_welcome_banner_screen(session, lane, actor, node_controls=node_controls)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "s":
            await session.write_line("")
            await _cycle_art_speed(lane, actor, WELCOME_ART)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "t":
            await session.write_line("")
            await _cycle_art_time_limit(lane, actor, WELCOME_ART)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "e":
            await session.write_line("")
            await _enable_welcome_banner_screen(session, lane, actor)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "d":
            await session.write_line("")
            await _disable_welcome_banner_screen(session, lane, actor)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "o":
            await session.write_line("")
            await _edit_welcome_banner_screen(session, lane, actor)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "g":
            await session.write_line("")
            await _welcome_banner_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "f":
            await session.write_line("")
            await _welcome_banner_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=banner_path, label="the welcome banner", audit_action="upload_welcome_banner")
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=welcome_banner_status, set_enabled=set_welcome_banner_enabled,
                label="the welcome banner", audit_action="remove_welcome_banner",
            )
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "c":
            await session.write_line("")
            shown = await lane.run(_toggle_welcome_banner_credit, actor)
            _announce(session, "Callers now see the art's credit under your banner." if shown
                      else "The art's credit is no longer shown under your banner.")
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == HELP_KEY:
            await session.write_line("")
            header_color = await lane.run(effective_header_color_256)
            await _banner_help_screen(
                session, path=await lane.run(banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_welcome_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        else:
            await session.write(reject_unhandled_key(choice))


def _art_speed_brief(speed: int) -> str:
    """The `[S]peed` entry's description (issue #929): the speed now set."""
    return f"Plays at {speed} bps" if speed else "Drawn at once"


async def _cycle_art_speed(lane: DatabaseLane, actor: User, kind: str) -> None:
    """`[S]peed` (issue #929): the next of `ART_SPEEDS` for this art -- off,
    2400, 9600, 38400 bps, then off again. The menu shows the new speed."""
    current = await lane.run(art_speed, kind)
    following = ART_SPEEDS[(ART_SPEEDS.index(current) + 1) % len(ART_SPEEDS)]

    def apply(db: Database) -> None:
        set_art_speed(db, kind, following)
        record_action(db, actor=actor, action="set_art_speed", detail=f"{kind}={following}")

    await lane.run(apply)


def _art_time_limit_entry(limit: int) -> MenuEntry:
    """The `[T]ime limit` toggle on an art screen with a `[S]peed`: it
    names its setting and shows its state, so a fitted menu that packs the
    descriptions away (issue #662) still shows it."""
    state = f"{limit} s" if limit else "off"
    return MenuEntry(label=menu_key("T", f"ime limit: {state}"), brief="Skip the rest after a while")


async def _cycle_art_time_limit(lane: DatabaseLane, actor: User, kind: str) -> None:
    """`[T]ime limit`: the next of `ART_TIME_LIMITS` for this art -- off,
    10, 30, 60 seconds, then off again. Past it, the rest of a paced draw
    goes out at once; off, the art plays to the end."""
    current = await lane.run(art_time_limit, kind)
    following = ART_TIME_LIMITS[(ART_TIME_LIMITS.index(current) + 1) % len(ART_TIME_LIMITS)]

    def apply(db: Database) -> None:
        set_art_time_limit(db, kind, following)
        record_action(db, actor=actor, action="set_art_time_limit", detail=f"{kind}={following}")

    await lane.run(apply)


async def _draw_welcome_banner_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool,
    collapsed: bool,
) -> None:
    status = await lane.run(welcome_banner_status)
    rows = await _write_counted(session, "\r\n" + screen_title("Welcome banner",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
            header_color=await lane.run(effective_header_color_256), node_name_gradient=session.node_name_gradient))
    credit_line = await lane.run(is_welcome_banner_credit_enabled)
    speed = await lane.run(art_speed, WELCOME_ART)
    time_limit = await lane.run(art_time_limit, WELCOME_ART)
    rows += await _write_sections(
        session,
        [_banner_status_section(
            status, unicode_style=unicode_style, sauce=await asyncio.to_thread(_banner_sauce, status),
            credit_line=credit_line, too_wide="the default banner", speed=speed,
        )],
        unicode_style=unicode_style,
    )
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show the banner as callers see it"),
            MenuEntry(label=menu_key("S", "peed"), brief=_art_speed_brief(speed)),
            _art_time_limit_entry(time_limit),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the banner on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the banner off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the banner text"),
            MenuEntry(label=menu_key("C", "redit line"), brief="Credit the art under the banner"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample banner"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Session banners"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


# Every banner and masthead file has the same 256 KiB limit.
_SAVED_BANNER_READ_LIMIT = MAX_BANNER_SIZE_BYTES


def _saved_banner_art(status) -> str | None:
    """The art in a banner's saved file, whether or not it is switched on,
    or `None` when there is no readable file within the size limit."""
    if not status.exists or (status.size_bytes or 0) > _SAVED_BANNER_READ_LIMIT:
        return None
    try:
        data = status.path.read_bytes()
    except OSError:
        return None
    return decode_banner_bytes(data) + RESET


async def _write_banner_not_live(session: Session, status, *, callers_see: str) -> bool:
    """A preview's answer when callers are not seeing the SysOp's own art
    here (issue #841). A saved file that is switched off is shown anyway,
    because right after saving it the SysOp wants to see their work, not
    what callers get instead; the line under it says it is off and what
    callers see meanwhile. Returns whether saved art was shown.

    This replaces lines like "(no banner -- enabled=False, file
    exists=True)", which read as developer output."""
    art = _saved_banner_art(status)
    if art is not None and not status.enabled:
        await write_preformatted_line(session, art)
        await session.write_line(
            colored(
                f"(Saved, but switched off: callers see {callers_see}. [E]nable turns it on.)",
                fg_color=MUTED_COLOR,
            )
        )
        return True
    if status.exists and art is None:
        message = f"(The saved file is over 256 KiB or can't be read: callers see {callers_see}.)"
    elif status.enabled:
        message = f"(Switched on, but no file is saved: callers see {callers_see}.)"
    else:
        message = f"(Nothing saved yet: callers see {callers_see}.)"
    await session.write_line(colored(message, fg_color=MUTED_COLOR))
    return False


async def _preview_welcome_banner_screen(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None = None
) -> None:
    """Renders the exact banner `netbbs.net.login_flow` would show at
    login right now -- the same `load_welcome_banner` call, used as a
    smoke test of the loading path itself, not a separate rendering.

    Dogfood follow-up: this used to read `session.supports_truecolor`
    directly, ignoring the previewing SysOp's own `[C]olor depth`
    override (`netbbs.net.color_depth_preference`) -- the one screen
    that override's own help text ("force a terminal color depth")
    promised control over silently didn't honor it.
    `effective_truecolor` is the shared "override wins over negotiated"
    resolution every post-login truecolor decision should go through
    (its own docstring); this is that function's first real caller."""

    def _load(db: Database) -> tuple:
        truecolor = effective_truecolor(session, db, actor)
        # As the connecting caller on this transport gets it (issue #841).
        banner = load_welcome_banner(
            db, truecolor=truecolor, unicode_style=pre_login_unicode_style(session),
            max_width=physical_terminal_width(session),
        )
        fields = banner_fields(db, callers_online=count_callers_online(
            node_controls.session_registry if node_controls is not None else None
        ))
        return welcome_banner_status(db), banner, fields, truecolor

    status, banner_text, fields, _truecolor = await lane.run(_load)
    # As a connecting caller gets it, field slots filled with what is true
    # before sign-in (issue #1057): the raw tokens were never what callers saw.
    raw_text = banner_text
    # Width, ellipsis and fields exactly as `login_flow` draws it (review
    # of #1060): a terminal that wraps at once lays screens out a column
    # narrower than the art it is sent.
    banner_text = fill_field_slots(
        banner_text, fields, ellipsis=ellipsis_for(session), width=physical_terminal_width(session),
    )
    await session.write_line(colored("\r\nPreviewing the welcome banner callers see when they connect:", fg_color=MUTED_COLOR))
    # At the speed callers get it (issue #1083 finding 9).
    speed = await lane.run(art_speed, WELCOME_ART)
    limit = await lane.run(art_time_limit, WELCOME_ART)
    if status.enabled and status.exists and (status.size_bytes or 0) <= MAX_BANNER_SIZE_BYTES:
        await write_preview_art(session, banner_text, speed=speed, limit=limit)
        await session.write_line(colored("(Your banner, as callers see it.)", fg_color=MUTED_COLOR))
        await _write_banner_slot_notes(session, raw_text)
    elif not await _write_banner_not_live(session, status, callers_see="the default NetBBS banner"):
        # Nothing of the SysOp's own to show: show what callers do see.
        await write_preview_art(session, banner_text, speed=speed, limit=limit)
    # Dogfood report: this screen used to fall straight through to the
    # menu's own immediate redraw, which -- with redraw_in_place on
    # (the default for new accounts, issue #160's own follow-up)
    # cleared the just-printed preview off the screen before it could
    # actually be read, sometimes in well under a second. Same
    # present-then-wait shape `netbbs.net.help_overlay.show_help`
    # already uses for the identical reason.
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _write_banner_slot_notes(session: Session, banner_text: str) -> None:
    """Under a welcome or log-off banner's preview: each token callers see
    blank rather than filled (issue #1057), so a SysOp who drew a
    `{menu}` into a banner learns it here, not from a caller."""
    for note in banner_slot_notes(banner_text, width=physical_terminal_width(session)):
        await session.write_line(colored(f"Note: {note}.", fg_color=WARNING_COLOR))


async def _enable_welcome_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(welcome_banner_status)
    if not status.exists:
        _announce(session, f"No banner file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_BANNER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Banner file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_BANNER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_welcome_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_welcome_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "Welcome banner enabled. Use [P]review to verify it looks right.")


async def _disable_welcome_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = welcome_banner_status(db)
        set_welcome_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_welcome_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session,
        f"Reverted to the default banner. Your file at {status.path} was left in place."
    )


async def _edit_welcome_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Opens the WYSIWYG ANSI art editor (design doc) against the
    current banner file, if any. `edit_ansi_art`
    itself knows nothing about "welcome banner" -- this screen is
    responsible for loading the existing file, computing the draft
    path, and writing a real save back to `banner_path(db)`."""
    path = await lane.run(banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_welcome_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _preview_apply_choice(session: Session, label: str) -> bool:
    """After a gallery/filesystem preview (issue #282): `[A]pply` or
    `[B]ack` to the list, instead of a yes/no whose "no" then also needed
    a "Press any key" to get back -- two keystrokes per declined preset
    while shopping through a gallery. Returns whether to apply."""
    async def _ask() -> None:
        await session.write_line(
            "\r\n" + action_bar([menu_key("A", "pply"), menu_key("B", "ack to the list")], width=session.terminal_width, height=session.terminal_height)
        )
        await write_prompt(session, f"{label}? ")

    await _ask()
    while True:
        choice = (await session.read_key()).lower()
        if choice == HELP_KEY:
            await show_menu_help(
                session, "Preview help",
                [MenuEntry(label=menu_key("A", "pply"), brief="Use what the preview showed"),
                 MenuEntry(label=menu_key("B", "ack to the list"), brief="Leave it unchanged and pick again")],
                about="The preview above is what callers would see. Nothing changes unless you apply it.",
            )
            await _ask()
            continue
        if choice in ("a", "b"):
            # A caller who habitually types "A<Enter>" must not carry that
            # Enter into the success pause or back into the picker (where
            # Enter activates the highlighted entry) -- the same drain the
            # yes/no primitive this replaces performs (Codex review on #291).
            discard_buffered_enter = getattr(session, "discard_buffered_enter", None)
            if discard_buffered_enter is not None:
                await discard_buffered_enter()
            await session.write_line("")
            return choice == "a"
        await session.write(reject_unhandled_key(choice))


async def _welcome_banner_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Issue #169: apply one of NetBBS's own bundled sample banners with
    zero filesystem access to the node -- the samples are real installed
    package data (`netbbs.net.banner_presets`), not files a SysOp has to
    `cp` into place first (the gap this closes: a wheel install has no
    `examples/` directory to copy from at all).

    Dogfood follow-up: declining the apply prompt loops back into this
    same gallery picker instead of exiting to the welcome-banner menu --
    browsing several samples in a row used to mean re-entering
    `[G]allery` after every single one just to look at the next.

    Second dogfood follow-up: that loop-back used to always reopen the
    picker at page 1 with nothing highlighted, discarding exactly the
    browsing position a decline is supposed to return you to -- `pick_
    item`'s `start_stable_id` now re-highlights whichever sample was
    just declined."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(WELCOME_BANNER_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="Welcome banner gallery",
            empty_message="No bundled welcome-banner samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_welcome_banner_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the welcome banner"):
            continue

        def _apply(db: Database) -> Path:
            path = banner_path(db)
            path.write_bytes(data)
            set_welcome_banner_enabled(db, True)
            record_action(db, actor=actor, action="apply_welcome_banner_preset", detail=f"{preset.key} -> {path}")
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


_BANNER_FILE_SUFFIX_OF: dict[Callable[[Database], Path], Callable[[str], str]] = {
    banner_path: lambda stem: f"{stem}_welcome_banner.ans",
    main_menu_banner_path: lambda stem: f"{stem}_main_menu_banner.ans",
    logoff_banner_path: lambda stem: f"{stem}_logoff_banner.ans",
    new_account_banner_before_path: lambda stem: f"{stem}_new_account_banner_before.ans",
    new_account_banner_after_path: lambda stem: f"{stem}_new_account_banner_after.ans",
    board_list_banner_path: lambda stem: f"{stem}_board_list_banner.ans",
    file_area_banner_path: lambda stem: f"{stem}_file_area_banner.ans",
    chat_channel_picker_banner_path: lambda stem: f"{stem}_chat_channel_picker_banner.ans",
}


async def _remove_banner_file(
    session: Session, lane: DatabaseLane, actor: User, *,
    status_of, set_enabled, label: str, audit_action: str,
) -> None:
    """Delete a banner's or masthead's saved file (issue #1119): until now a
    SysOp could only switch one off, and a file that should be gone stayed
    on disk. One yes/no, then the file goes and the piece is switched off
    with it -- left on, it would read "switched on, but no file is saved".
    Its mode and speed stay: they describe how art is shown, and apply
    again to whatever file comes next."""
    status = await lane.run(status_of)
    if not status.exists:
        _announce(session, f"No file is saved for {label}.", color=MUTED_COLOR)
        return
    if not await prompt_yes_no(session, f"Delete {status.path}? It can't be brought back.", default=False):
        _announce_line(session, "Cancelled. Nothing was deleted.")
        return

    def _apply(db: Database) -> tuple[bool, bool]:
        was_enabled = status_of(db).enabled
        try:
            status.path.unlink()
        except FileNotFoundError:
            return False, was_enabled
        if was_enabled:
            set_enabled(db, False)
        record_action(db, actor=actor, action=audit_action, detail=str(status.path))
        return True, was_enabled

    try:
        removed, was_enabled = await lane.run(_apply)
    except OSError as exc:
        _announce(session, f"Could not delete {status.path}: {exc.strerror or exc}", error=True)
        return
    if not removed:
        _announce(session, f"No file is saved for {label}.", color=MUTED_COLOR)
        return
    switched = " and switched it off" if was_enabled else ""
    _announce_line(session, f"Deleted {status.path}{switched}. Callers see what they would with no file.")


async def _upload_banner_piece(
    session: Session, lane: DatabaseLane, actor: User, *,
    path_of: Callable[[Database], Path], label: str, audit_action: str,
) -> None:
    """[U]pload on a banner or masthead screen (issue #728): one `.ans` file
    from the SysOp's own computer, written to exactly this piece's file.

    Never enables the piece. Replacing an existing file is confirmed first,
    because an enabled piece shows the new file to callers at once."""
    reason = _upload_unavailable_reason(session)
    if reason is not None:
        _announce(session, reason, error=True)
        return
    def _lexical(db: Database) -> Path:
        # The piece helpers return a `.resolve()`d path, which has already
        # followed a symlink at the piece's own name -- so the refusal below
        # could never see one. Rebuild the name without following it: the
        # database's directory, resolved, plus the piece's file name.
        return db.path.parent.resolve() / _BANNER_FILE_SUFFIX_OF[path_of](db.path.stem)

    destination = await lane.run(_lexical)
    problem = destination_problem(destination)
    if problem is not None:
        _announce(session, problem, error=True)
        return
    if destination.exists() and not await prompt_yes_no(
        session,
        f"Replace the current {destination.name}? If {label} is enabled, callers see the new one at once",
        default=False,
    ):
        _announce(session, "Nothing uploaded.")
        return
    target = SysOpUploadTarget(
        kind=SYSOP_UPLOAD_BANNER, destination=destination, max_bytes=MAX_BANNER_SIZE_BYTES,
        label=label, audit_action=audit_action, replaces=destination.exists(),
    )
    await _receive_sysop_upload(
        session, lane, actor, target, then="Use [P]review, then [E]nable if it is not on yet.",
    )


def _browsable_ans_files(directory: Path, *, exclude: Path) -> list[Path]:
    """Every `.ans` file in `directory` except `exclude` (the picker's
    own current target -- loading it onto itself is a pointless option,
    not a real choice). No traversal risk here: `directory.glob` only
    ever returns real entries NetBBS itself enumerated, never a
    caller-typed path, so there's nothing for a `..`-style escape to
    reach in the first place (issue #170's own locked design)."""
    return sorted(p for p in directory.glob("*.ans") if p.is_file() and p != exclude)


async def _banner_help_screen(
    session: Session, *, path: Path, has_gallery: bool,
    header_color: int | tuple[int, int, int], unicode_style: bool,
) -> None:
    """Ctrl-H's own content for every banner/masthead customization menu
    in this section (dogfood feature request) -- one shared listing
    rather than a per-screen copy, matching `netbbs.net.picker.
    _show_picker_help`'s own precedent for reusable on-demand help
    within a single module.

    A SysOp with shell/SFTP access had no way to discover *where* a
    hand-authored `.ans` file actually has to go: this screen's own
    status line shows the current target path, but never the "why", and
    [F]rom disk (where it exists) only ever lists files already sitting
    in that same directory -- easy to try once, find nothing, and
    conclude the feature is broken (a real dogfood report) rather than
    realizing the file just isn't in the right place yet."""
    lines = [
        colored("Placing your own .ans file", fg_color=header_color, bold=True),
        "  Send it with [U]pload, or place it (e.g. via SFTP/SCP) at exactly this path, then [E]nable:",
        f"  {path}",
        "  Or save it under any other name in that same directory and pick it up "
        "with [F]rom disk instead of overwriting this one directly.",
    ]
    if has_gallery:
        lines += [
            "",
            colored("Gallery vs. From disk", fg_color=header_color, bold=True),
            "  [G]allery applies one of NetBBS's own bundled sample designs -- no "
            "filesystem access to this node needed. [F]rom disk loads a file you've "
            "already placed here yourself.",
        ]
    await show_help(session, "Banner/masthead help", lines, header_color=header_color, unicode_style=unicode_style)


async def _banner_router_help_screen(
    session: Session, *, header_color: int | tuple[int, int, int], unicode_style: bool
) -> None:
    """Ctrl-H's content one level above `_banner_help_screen`: this
    screen and its two children (Banners, Mastheads) are pure navigation
    -- none of them owns a single target file, so `_banner_help_screen`'s
    own "here's the exact path" answer doesn't apply yet. Dogfood report:
    a SysOp reasonably tries Ctrl-H at the first screen they land on, not
    only at a specific banner's own menu, and got silently nothing here
    -- worse than no hint at all, since every leaf screen one level down
    does answer Ctrl-H."""
    lines = [
        colored("Placing your own .ans file", fg_color=header_color, bold=True),
        "  Each banner/masthead below has its own file and its own exact path --",
        f"  pick one, then press {help_key_label(session)} on that screen to see where it goes.",
    ]
    await show_help(session, "Banner/masthead help", lines, header_color=header_color, unicode_style=unicode_style)


async def _welcome_banner_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Issue #170: load a SysOp's own externally-authored `.ans` file --
    drawn in a dedicated tool, or downloaded from elsewhere -- instead
    of having to shell in and place it at `banner_path(db)` by hand.
    Different problem from the bundled gallery just above (issue #169,
    sibling): nothing here was ever going to ship *with* NetBBS, so this
    enumerates real files on disk instead of a fixed preset tuple.

    **Browsable root** (locked design, see the issue's own comments):
    the node's own state directory only -- exactly `banner_path(db)`'s
    own parent, the same directory NetBBS already reads/writes banner
    state in. No new `node_config` setting, no new attack surface
    beyond what already exists.

    **Load semantics**: copy, not reference-in-place. The selected
    file's bytes are written to the existing well-known `banner_path
    (db)`, exactly as if the SysOp had `cp`'d it there by hand --
    `load_welcome_banner`/`welcome_banner_status`/backup-restore/the
    WYSIWYG editor all keep their existing one-file-one-path assumption
    completely untouched.

    **Validation timing**: the same size cap `[E]nable` already enforces
    runs *before* the copy, against the source file -- rejecting a pick
    immediately with a clear reason, not a weaker path into the same
    target than manual placement already has."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = banner_path(db).parent
        return _browsable_ans_files(directory, exclude=banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Welcome banner -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_BANNER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_BANNER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            # Same present-then-wait fix as the empty-list message above --
            # otherwise `pick_item`'s own next redraw clears this before
            # it can be read.
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the welcome banner"):
            continue

        def _apply(db: Database) -> Path:
            target = banner_path(db)
            target.write_bytes(data)
            set_welcome_banner_enabled(db, True)
            record_action(db, actor=actor, action="load_welcome_banner_from_file", detail=f"{path} -> {target}")
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("Main-menu masthead")
async def _main_menu_banner_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            await _preview_main_menu_banner_screen(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "s":
            await session.write_line("")
            await _cycle_art_speed(lane, actor, MAIN_MENU_ART)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "t":
            await session.write_line("")
            await _cycle_art_time_limit(lane, actor, MAIN_MENU_ART)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "e":
            await session.write_line("")
            await _enable_main_menu_banner_screen(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "d":
            await session.write_line("")
            await _disable_main_menu_banner_screen(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "o":
            await session.write_line("")
            await _edit_main_menu_banner_screen(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "g":
            await session.write_line("")
            await _main_menu_banner_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "f":
            await session.write_line("")
            await _main_menu_banner_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=main_menu_banner_path, label="the main-menu masthead", audit_action="upload_main_menu_banner")
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=main_menu_banner_status, set_enabled=set_main_menu_banner_enabled,
                label="the main-menu masthead", audit_action="remove_main_menu_banner",
            )
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "m":
            await session.write_line("")
            await _toggle_main_menu_art_mode(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == "c":
            await session.write_line("")
            await _check_main_menu_slot_art_screen(session, lane, actor)
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        elif choice == HELP_KEY:
            await session.write_line("")
            header_color = await lane.run(effective_header_color_256)
            await _banner_help_screen(
                session, path=await lane.run(main_menu_banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_main_menu_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_main_menu_banner_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool,
    collapsed: bool,
) -> None:
    status = await lane.run(main_menu_banner_status)
    mode = await lane.run(main_menu_art_mode)
    rows = await _write_counted(session, "\r\n" + screen_title("Main-menu masthead",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
            header_color=await lane.run(effective_header_color_256), node_name_gradient=session.node_name_gradient))
    sauce = await asyncio.to_thread(_banner_sauce, status)
    speed = await lane.run(art_speed, MAIN_MENU_ART)
    time_limit = await lane.run(art_time_limit, MAIN_MENU_ART)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce, speed=speed)], unicode_style=unicode_style)
    await session.write_line("")
    rows += 1
    rows += await _write_wrapped_subtitle(
        session,
        # One row each, so the screen with its [S]peed entry fits 80x24.
        "Mode: above the menu. The art is shown above the live main menu."
        if mode == MASTHEAD_MODE else
        "Mode: the menu itself. Callers' items and live values fill its slots.",
    )
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show it as callers see it"),
            MenuEntry(label=menu_key("S", "peed"), brief=_art_speed_brief(speed)),
            _art_time_limit_entry(time_limit),
            MenuEntry(
                label=menu_key("M", "ode"),
                brief="Make the art the menu itself" if mode == MASTHEAD_MODE else "Show the art above the menu",
            ),
            MenuEntry(label=menu_key("C", "heck"), brief="Slots, problems and menu fit"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the masthead on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the masthead off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the masthead art"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample masthead"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_main_menu_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Renders the exact masthead `netbbs.net.main_menu._draw_main_menu`
    would prepend above the main menu right now -- the same
    `load_main_menu_banner` call, a smoke test of the loading path
    itself. Unlike the welcome-banner preview, this never calls into
    `netbbs.net.login_flow` to reproduce the real menu underneath it --
    that module already imports `admin_menu` from this one, so the
    reverse import would be circular, and the masthead itself carries no
    truecolor-dependent content to showcase (shown exactly as authored,
    the same "trusted, already-composed art" tier as the welcome
    banner's own custom-file path)."""

    if await lane.run(main_menu_art_mode) == SLOTS_MODE:
        await _preview_main_menu_slot_art(session, lane, actor)
        return

    def _load(db: Database) -> tuple:
        return main_menu_banner_status(db), load_main_menu_banner(db)

    status, masthead = await lane.run(_load)
    await session.write_line(colored("\r\nPreviewing the masthead as shown above the main menu:", fg_color=MUTED_COLOR))
    if not masthead:
        await _write_banner_not_live(session, status, callers_see="no masthead")
    else:
        await write_preview_art(
            session, masthead, speed=await lane.run(art_speed, MAIN_MENU_ART), limit=await lane.run(art_time_limit, MAIN_MENU_ART),
        )
        await session.write_line(
            colored("(the main menu itself renders live, unchanged, immediately below this)", fg_color=MUTED_COLOR)
        )
    # See _preview_welcome_banner_screen's identical fix for why this
    # wait exists -- without it, redraw_in_place clears this preview
    # before it can actually be read.
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _toggle_main_menu_art_mode(session: Session, lane: DatabaseLane, actor: User) -> None:
    """[M]ode (issue #929, step 4): the art above the main menu, or the
    art as the menu itself with live slots."""

    def _apply(db: Database) -> str:
        mode = SLOTS_MODE if main_menu_art_mode(db) == MASTHEAD_MODE else MASTHEAD_MODE
        set_main_menu_art_mode(db, mode)
        record_action(db, actor=actor, action="set_main_menu_art_mode", detail=mode)
        return mode

    mode = await lane.run(_apply)
    if mode == SLOTS_MODE:
        _announce_line(session, "The art is now the main menu itself. Use [C]heck to see whether the menu fits it.")
    else:
        _announce_line(session, "The art is now shown above the main menu.")


def _read_slot_art(db: Database) -> SlotArt | None:
    path = main_menu_banner_path(db)
    try:
        if not path.exists() or path.stat().st_size > MAX_MASTHEAD_SIZE_BYTES:
            return None
        return parse_slot_art(decode_banner_bytes(path.read_bytes()))
    except OSError:
        return None


async def _check_main_menu_slot_art_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """[C]heck (issue #929, steps 4 and 5): the slots and hand-drawn items
    found in the art, what makes it unusable, and whether your own menu and
    a level-0 caller's fit on a terminal the size of yours -- which drawn
    items each of them has blanked, and which of their items go into the
    `{menu}` slot. A menu that doesn't fit isn't cut: those callers get the
    generated menu instead."""
    # Imported here: netbbs.net.main_menu imports this module.
    from netbbs.net.main_menu import MAIN_MENU_KEYS, slot_menu_preview

    art = await lane.run(_read_slot_art)
    enabled, mode = await lane.run(lambda db: (is_main_menu_banner_enabled(db), main_menu_art_mode(db)))
    await session.write_line(colored("\r\nChecking the main-menu art for slots:", fg_color=MUTED_COLOR))
    # Enabled and mode are separate switches: [D]isable leaves the mode
    # alone, so say plainly when callers won't see this art at all.
    if not enabled:
        await session.write_line(highlight_hotkeys(
            "Callers don't see this art yet: it's switched off. Use [E]nable.", color=WARNING_COLOR
        ))
    if mode != SLOTS_MODE:
        await session.write_line(highlight_hotkeys(
            "Callers see this above the menu, not as it: [M]ode changes that.",
            color=WARNING_COLOR,
        ))
    if art is None:
        await session.write_line("No usable art file. Apply a gallery sample, upload one, or draw one first.")
    else:
        await session.write_line(highlight_report(f"Art: {art.width} columns, {art.height} rows.", color=VALUE_COLOR))
        for line in describe_slots(art) or ["No slots found."]:
            await session.write_line(highlight_report(f"  {line}", color=VALUE_COLOR))
        if art.items:
            await session.write_line("Drawn items:")
            for item, line in zip(art.items, describe_items(art)):
                if any(key in MAIN_MENU_KEYS for key in item.keys):
                    await session.write_line(f"  {line}")
                else:
                    await session.write_line(colored(
                        f"  {line}: not a main-menu key, so it stays as drawn for everyone",
                        fg_color=WARNING_COLOR,
                    ))
        for note in art.notes:
            await session.write_line(colored(f"  Note: {note}", fg_color=WARNING_COLOR))
        if art.problems:
            for problem in art.problems:
                await session.write_line(colored(f"  Problem: {problem}", fg_color=ERROR_COLOR))
        else:
            for label, level in (("Your menu", None), ("A level-0 caller's menu", 0)):
                plan = await lane.run(lambda db, level=level: slot_menu_preview(session, db, actor, art, level=level))
                if plan.text is not None:
                    await session.write_line(_fits_line(label, ""))
                    for line in _drawn_item_outcome(plan):
                        await session.write_line(f"    {line}")
                else:
                    await session.write_line(
                        colored(f"  {label}: generated menu instead -- {plan.reason}.", fg_color=WARNING_COLOR)
                    )
    await session.write_line(continue_prompt())
    await session.read_any_key()


def _fits_line(label: str, details: str) -> str:
    """A check's "fits" verdict (issue #1083): the verdict in the success
    colour and the numbers after it emphasised, not one flat green line."""
    return (
        colored(f"  {label}: ", fg_color=VALUE_COLOR) + colored("fits", fg_color=SUCCESS_COLOR, bold=True)
        + highlight_report(f"{details}.", color=VALUE_COLOR)
    )


def _drawn_item_outcome(plan) -> list[str]:
    """What a fitting slot-menu plan does with hand-drawn items (#929, step
    5): which it blanks for that caller, and which of their items go into
    the `{menu}` slot because the art doesn't draw them."""
    lines = []
    if plan.hidden_keys:
        keys = ", ".join(f"[{key.upper() if key.isalpha() else key}]" for key in plan.hidden_keys)
        lines.append(f"Blanked, as this caller can't use them: {keys}")
    if plan.overflow:
        lines.append(f"In the {{menu}} slot, as the art doesn't draw them: {', '.join(plan.overflow)}")
    return lines


async def _write_slot_art_preview(
    session: Session, lane: DatabaseLane, actor: User, art: SlotArt, *, level: int | None
) -> None:
    from netbbs.net.main_menu import slot_menu_preview

    plan = await lane.run(lambda db: slot_menu_preview(session, db, actor, art, level=level))
    if plan.text is None:
        await session.write_line(
            colored(f"Callers like this get the generated menu instead: {plan.reason}.", fg_color=WARNING_COLOR)
        )
        return
    await write_preview_art_text(
        session, plan.text,
        speed=await lane.run(art_speed, MAIN_MENU_ART), limit=await lane.run(art_time_limit, MAIN_MENU_ART),
    )
    await session.write(move_cursor(art.height + 1, 1))


async def _preview_main_menu_slot_art(session: Session, lane: DatabaseLane, actor: User) -> None:
    """[P]review in slots mode: the main menu as you see it, then as a
    level-0 caller sees it."""
    art = await lane.run(_read_slot_art)
    if art is None:
        await session.write_line(colored("\r\nNo usable art file to preview.", fg_color=MUTED_COLOR))
        await session.write_line(continue_prompt())
        await session.read_any_key()
        return
    # [D]isable leaves the mode alone, so a switched-off banner still lands
    # here: say under each draw that callers get the plain menu meanwhile.
    enabled = await lane.run(is_main_menu_banner_enabled)
    for number, (intro, level) in enumerate((("as you see it", None), ("as a level-0 caller sees it", 0)), 1):
        # The art clears the screen, so what is being shown is said below it.
        await _write_slot_art_preview(session, lane, actor, art, level=level)
        if not enabled:
            await session.write_line(highlight_hotkeys(
                "Callers don't see this art yet: it's switched off. Use [E]nable.", color=WARNING_COLOR
            ))
        await session.write_line(colored(f"(the main menu {intro}, {number} of 2)  ", fg_color=MUTED_COLOR) + continue_prompt())
        await session.read_any_key()


async def _enable_main_menu_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(main_menu_banner_status)
    if not status.exists:
        _announce(session, f"No masthead file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_MASTHEAD_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Masthead file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_MASTHEAD_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_main_menu_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_main_menu_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "Main-menu masthead enabled. Use [P]review to verify it looks right.")


async def _disable_main_menu_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = main_menu_banner_status(db)
        set_main_menu_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_main_menu_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session,
        f"Masthead disabled -- the main menu reverts to showing no banner above it. "
        f"Your file at {status.path} was left in place."
    )


async def _edit_main_menu_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Same WYSIWYG ANSI art editor as `_edit_welcome_banner_screen`,
    against `main_menu_banner_path` instead."""
    path = await lane.run(main_menu_banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_main_menu_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _main_menu_banner_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same bundled-sample gallery as `_welcome_banner_gallery_screen`
    (issue #169), against `main_menu_banner_path` instead -- including
    its identical "decline loops back into the gallery, re-highlighted"
    dogfood fixes."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(MAIN_MENU_BANNER_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="Masthead gallery",
            empty_message="No bundled masthead samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_main_menu_banner_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        if preset.mode == SLOTS_MODE:
            art = parse_slot_art(decode_banner_bytes(data))
            await _write_slot_art_preview(session, lane, actor, art, level=None)
        else:
            await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        what = "as the main menu" if preset.mode == SLOTS_MODE else "as the masthead"
        if not await _preview_apply_choice(session, f"Apply {preset.name!r} {what}"):
            continue

        def _apply(db: Database) -> Path:
            path = main_menu_banner_path(db)
            path.write_bytes(data)
            set_main_menu_banner_enabled(db, True)
            set_main_menu_art_mode(db, preset.mode)
            record_action(db, actor=actor, action="apply_main_menu_banner_preset", detail=f"{preset.key} -> {path}")
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _main_menu_banner_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_welcome_banner_filesystem_screen`
    (issue #170), against `main_menu_banner_path` instead -- see that
    function's own docstring for the locked design (browsable root,
    copy-not-reference load semantics, validate-before-copy)."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = main_menu_banner_path(db).parent
        return _browsable_ans_files(directory, exclude=main_menu_banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Masthead -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_MASTHEAD_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_MASTHEAD_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the masthead"):
            continue

        def _apply(db: Database) -> Path:
            target = main_menu_banner_path(db)
            target.write_bytes(data)
            set_main_menu_banner_enabled(db, True)
            record_action(db, actor=actor, action="load_main_menu_banner_from_file", detail=f"{path} -> {target}")
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("Session banners")
async def _banners_menu(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None = None
) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "w":
            await session.write_line("")
            await _welcome_banner_menu(session, lane, actor, node_controls=node_controls)
            await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "l":
            await session.write_line("")
            await _logoff_banner_menu(session, lane, actor, node_controls=node_controls)
            await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "p":
            await session.write_line("")
            await _new_account_banner_before_menu(session, lane, actor)
            await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "a":
            await session.write_line("")
            await _new_account_banner_after_menu(session, lane, actor)
            await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_router_help_screen(session, header_color=header_color, unicode_style=unicode_style)
            await _draw_banners_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_banners_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    await session.write_line("\r\n" + screen_title("Session banners",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place,
            unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    await _write_wrapped_subtitle(
        session,
        "Optional banners shown throughout a caller's session -- first login, signing off, "
        "and starting/finishing self-service signup.",
    )
    await session.write_line(
        "\r\n" + _menu_row(
            [
                MenuEntry(label=menu_key("W", "elcome banner"), brief="Art shown when a caller connects"),
                MenuEntry(label=menu_key("L", "ogoff banner"), brief="Shown on an intentional Log off"),
                MenuEntry(label=menu_key("P", "re-signup"), brief="Shown once, before Create account"),
                MenuEntry(label=menu_key("A", "fter signup"), brief="Shown once signup succeeds"),
                MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads & banners"),
            ],
            description_level,
            width=session.terminal_width,
            height=session.terminal_height,
        )
    )
    await session.write_line(colored(f"({help_key_label(session)} for help placing your own .ans files)", fg_color=MUTED_COLOR))
    await _choice_prompt(session)


@located("Logoff banner")
async def _logoff_banner_menu(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None = None
) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            await _preview_logoff_banner_screen(session, lane, actor, node_controls=node_controls)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_logoff_banner_screen(session, lane, actor)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_logoff_banner_screen(session, lane, actor)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_logoff_banner_screen(session, lane, actor)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _logoff_banner_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _logoff_banner_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=logoff_banner_path, label="the log-off banner", audit_action="upload_logoff_banner")
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=logoff_banner_status, set_enabled=set_logoff_banner_enabled,
                label="the log-off banner", audit_action="remove_logoff_banner",
            )
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(logoff_banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_logoff_banner_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_logoff_banner_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(logoff_banner_status)
    rows = await _write_counted(session, "\r\n" + screen_title("Logoff banner",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown above the ordinary Goodbye message on an intentional Log off only -- never on an idle "
        "timeout, kick, or account revocation.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce)], unicode_style=unicode_style)
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show the banner as callers see it"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the banner on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the banner off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the banner text"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample banner"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Session banners"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_logoff_banner_screen(
    session: Session, lane: DatabaseLane, actor: User | None = None, *, node_controls: NodeControls | None = None
) -> None:
    status, banner_text, fields = await lane.run(lambda db: (
        logoff_banner_status(db), load_logoff_banner(db, max_width=physical_terminal_width(session)),
        banner_fields(db, user=actor, callers_online=count_callers_online(
            node_controls.session_registry if node_controls is not None else None
        )),
    ))
    await session.write_line(colored("\r\nPreviewing logoff banner as shown on Log off:", fg_color=MUTED_COLOR))
    if banner_text:
        # Filled as your own Log off would fill it (issue #1057).
        await write_preformatted_line(session, fill_field_slots(
            banner_text, fields, ellipsis=ellipsis_for(session), width=physical_terminal_width(session),
        ))
        await _write_banner_slot_notes(session, banner_text)
    else:
        await _write_banner_not_live(session, status, callers_see="no banner")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_logoff_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(logoff_banner_status)
    if not status.exists:
        _announce(session, f"No banner file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_LOGOFF_BANNER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Banner file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_LOGOFF_BANNER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_logoff_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_logoff_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "Logoff banner enabled. Use [P]review to verify it looks right.")


async def _disable_logoff_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = logoff_banner_status(db)
        set_logoff_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_logoff_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"Logoff banner disabled. Your file at {status.path} was left in place.")


async def _edit_logoff_banner_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(logoff_banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_logoff_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _logoff_banner_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a farewell-specific bundled banner."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(LOGOFF_BANNER_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="Logoff banner gallery",
            empty_message="No bundled banner samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_logoff_banner_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the logoff banner"):
            continue

        def _apply(db: Database) -> Path:
            path = logoff_banner_path(db)
            path.write_bytes(data)
            set_logoff_banner_enabled(db, True)
            record_action(db, actor=actor, action="apply_logoff_banner_preset", detail=f"{preset.key} -> {path}")
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _logoff_banner_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `logoff_banner_path` instead."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = logoff_banner_path(db).parent
        return _browsable_ans_files(directory, exclude=logoff_banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Logoff banner -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_LOGOFF_BANNER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_LOGOFF_BANNER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the logoff banner"):
            continue

        def _apply(db: Database) -> Path:
            target = logoff_banner_path(db)
            target.write_bytes(data)
            set_logoff_banner_enabled(db, True)
            record_action(db, actor=actor, action="load_logoff_banner_from_file", detail=f"{path} -> {target}")
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("New account banner (before)")
async def _new_account_banner_before_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            await _preview_new_account_banner_before_screen(session, lane)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_new_account_banner_before_screen(session, lane, actor)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_new_account_banner_before_screen(session, lane, actor)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_new_account_banner_before_screen(session, lane, actor)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _new_account_banner_before_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _new_account_banner_before_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=new_account_banner_before_path, label="the before-signup banner", audit_action="upload_new_account_banner_before")
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=new_account_banner_before_status, set_enabled=set_new_account_banner_before_enabled,
                label="the before-signup banner", audit_action="remove_new_account_banner_before",
            )
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(new_account_banner_before_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_new_account_banner_before_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_new_account_banner_before_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(new_account_banner_before_status)
    rows = await _write_counted(session, "\r\n" + screen_title("New account banner (before)",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown once, right when a caller starts self-service signup -- before the Create "
        "account prompts, never repeated on a fixable retry.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce)], unicode_style=unicode_style)
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show the banner as callers see it"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the banner on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the banner off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the banner text"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample banner"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Session banners"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_new_account_banner_before_screen(session: Session, lane: DatabaseLane) -> None:
    status, banner_text = await lane.run(
        lambda db: (new_account_banner_before_status(db), load_new_account_banner_before(db))
    )
    await session.write_line(
        colored("\r\nPreviewing new-account (before) banner as shown at signup:", fg_color=MUTED_COLOR)
    )
    if banner_text:
        await write_preformatted_line(session, banner_text)
    else:
        await _write_banner_not_live(session, status, callers_see="no banner")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_new_account_banner_before_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(new_account_banner_before_status)
    if not status.exists:
        _announce(session, f"No banner file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_NEW_ACCOUNT_BANNER_BEFORE_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Banner file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_NEW_ACCOUNT_BANNER_BEFORE_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_new_account_banner_before_enabled(db, True)
        record_action(db, actor=actor, action="enable_new_account_banner_before", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "New-account (before) banner enabled. Use [P]review to verify it looks right.")


async def _disable_new_account_banner_before_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = new_account_banner_before_status(db)
        set_new_account_banner_before_enabled(db, False)
        record_action(db, actor=actor, action="disable_new_account_banner_before", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"New-account (before) banner disabled. Your file at {status.path} was left in place.")


async def _edit_new_account_banner_before_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(new_account_banner_before_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_new_account_banner_before", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _new_account_banner_before_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a pre-registration bundled banner."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(NEW_ACCOUNT_BANNER_BEFORE_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="New-account (before) banner gallery",
            empty_message="No bundled banner samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_new_account_banner_before_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the new-account (before) banner"):
            continue

        def _apply(db: Database) -> Path:
            path = new_account_banner_before_path(db)
            path.write_bytes(data)
            set_new_account_banner_before_enabled(db, True)
            record_action(
                db, actor=actor, action="apply_new_account_banner_before_preset", detail=f"{preset.key} -> {path}"
            )
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _new_account_banner_before_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `new_account_banner_before_path` instead."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = new_account_banner_before_path(db).parent
        return _browsable_ans_files(directory, exclude=new_account_banner_before_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="New-account (before) banner -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_NEW_ACCOUNT_BANNER_BEFORE_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_NEW_ACCOUNT_BANNER_BEFORE_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the new-account (before) banner"):
            continue

        def _apply(db: Database) -> Path:
            target = new_account_banner_before_path(db)
            target.write_bytes(data)
            set_new_account_banner_before_enabled(db, True)
            record_action(
                db, actor=actor, action="load_new_account_banner_before_from_file", detail=f"{path} -> {target}"
            )
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("New account banner (after)")
async def _new_account_banner_after_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            await _preview_new_account_banner_after_screen(session, lane)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_new_account_banner_after_screen(session, lane, actor)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_new_account_banner_after_screen(session, lane, actor)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_new_account_banner_after_screen(session, lane, actor)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _new_account_banner_after_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _new_account_banner_after_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=new_account_banner_after_path, label="the after-signup banner", audit_action="upload_new_account_banner_after")
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=new_account_banner_after_status, set_enabled=set_new_account_banner_after_enabled,
                label="the after-signup banner", audit_action="remove_new_account_banner_after",
            )
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(new_account_banner_after_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_new_account_banner_after_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_new_account_banner_after_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(new_account_banner_after_status)
    rows = await _write_counted(session, "\r\n" + screen_title("New account banner (after)",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown once self-service signup succeeds -- covers both an immediate login and a "
        "pending-approval account, alongside the existing message either way.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce)], unicode_style=unicode_style)
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show the banner as callers see it"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the banner on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the banner off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the banner text"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample banner"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Session banners"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_new_account_banner_after_screen(session: Session, lane: DatabaseLane) -> None:
    status, banner_text = await lane.run(
        lambda db: (new_account_banner_after_status(db), load_new_account_banner_after(db))
    )
    await session.write_line(
        colored("\r\nPreviewing new-account (after) banner as shown once signup succeeds:", fg_color=MUTED_COLOR)
    )
    if banner_text:
        await write_preformatted_line(session, banner_text)
    else:
        await _write_banner_not_live(session, status, callers_see="no banner")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_new_account_banner_after_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(new_account_banner_after_status)
    if not status.exists:
        _announce(session, f"No banner file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_NEW_ACCOUNT_BANNER_AFTER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Banner file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_NEW_ACCOUNT_BANNER_AFTER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_new_account_banner_after_enabled(db, True)
        record_action(db, actor=actor, action="enable_new_account_banner_after", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "New-account (after) banner enabled. Use [P]review to verify it looks right.")


async def _disable_new_account_banner_after_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = new_account_banner_after_status(db)
        set_new_account_banner_after_enabled(db, False)
        record_action(db, actor=actor, action="disable_new_account_banner_after", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"New-account (after) banner disabled. Your file at {status.path} was left in place.")


async def _edit_new_account_banner_after_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(new_account_banner_after_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_new_account_banner_after", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _new_account_banner_after_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a post-registration bundled banner."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(NEW_ACCOUNT_BANNER_AFTER_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="New-account (after) banner gallery",
            empty_message="No bundled banner samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_new_account_banner_after_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the new-account (after) banner"):
            continue

        def _apply(db: Database) -> Path:
            path = new_account_banner_after_path(db)
            path.write_bytes(data)
            set_new_account_banner_after_enabled(db, True)
            record_action(
                db, actor=actor, action="apply_new_account_banner_after_preset", detail=f"{preset.key} -> {path}"
            )
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _new_account_banner_after_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `new_account_banner_after_path` instead."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = new_account_banner_after_path(db).parent
        return _browsable_ans_files(directory, exclude=new_account_banner_after_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="New-account (after) banner -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_NEW_ACCOUNT_BANNER_AFTER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_NEW_ACCOUNT_BANNER_AFTER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the new-account (after) banner"):
            continue

        def _apply(db: Database) -> Path:
            target = new_account_banner_after_path(db)
            target.write_bytes(data)
            set_new_account_banner_after_enabled(db, True)
            record_action(
                db, actor=actor, action="load_new_account_banner_after_from_file", detail=f"{path} -> {target}"
            )
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("Mastheads")
async def _mastheads_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "m":
            await session.write_line("")
            await _main_menu_banner_menu(session, lane, actor)
            await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _board_list_masthead_menu(session, lane, actor)
            await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _file_area_masthead_menu(session, lane, actor)
            await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "c":
            await session.write_line("")
            await _chat_channel_picker_masthead_menu(session, lane, actor)
            await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_router_help_screen(session, header_color=header_color, unicode_style=unicode_style)
            await _draw_mastheads_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_mastheads_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    await session.write_line("\r\n" + screen_title("Mastheads",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place,
            unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    await _write_wrapped_subtitle(
        session,
        "Optional mastheads for the main menu, message-board list, file-area list, and "
        "chat channel picker -- at every level of browsing (top level, a category, a "
        "Community) where applicable. Each is shown above the menu or list, or, under its "
        "[M]ode, as the menu or list itself.",
    )
    await session.write_line(
        "\r\n" + _menu_row(
            [
                MenuEntry(label=menu_key("M", "ain menu"), brief="Above the menu, or as the menu"),
                MenuEntry(label=menu_key("D", "iscussion boards"), brief="Above the list, or as the list"),
                MenuEntry(label=menu_key("F", "ile areas"), brief="Above the list, or as the list"),
                MenuEntry(label=menu_key("C", "hat channels"), brief="Above the picker, or as the list"),
                MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads & banners"),
            ],
            description_level,
            width=session.terminal_width,
            height=session.terminal_height,
        )
    )
    await session.write_line(colored(f"({help_key_label(session)} for help placing your own .ans files)", fg_color=MUTED_COLOR))
    await _choice_prompt(session)


# -- list screens as art (issue #929) --------------------------------------
#
# The Boards, file areas and Chat channels lists can use their masthead art
# as the list itself, with a page drawn into its {list WxH} region
# (`netbbs.net.list_art`, `pick_item`'s `slot_art`). These are the three
# masthead screens' [M]ode, [C]heck and slots-mode [P]review.

_LIST_ART_SCREENS = {
    BOARD_LIST: ("board list", board_list_banner_path, is_board_list_banner_enabled, MAX_BOARD_LIST_BANNER_SIZE_BYTES),
    FILE_AREA: ("file-area list", file_area_banner_path, is_file_area_banner_enabled, MAX_FILE_AREA_BANNER_SIZE_BYTES),
    CHAT_CHANNEL_PICKER: (
        "channel list", chat_channel_picker_banner_path, is_chat_channel_picker_banner_enabled,
        MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES,
    ),
}


def _list_art_mode_line(mode: str) -> str:
    if mode == LIST_MASTHEAD_MODE:
        return "Mode: above the list. The art is shown above the list, which stays live underneath it."
    return (
        "Mode: the list itself. A page of entries is drawn into the art's {list} slot, "
        "and its {title}, {page} and {count} slots are filled in."
    )


async def _toggle_list_art_mode(session: Session, lane: DatabaseLane, actor: User, kind: str) -> None:
    what = _LIST_ART_SCREENS[kind][0]

    def _apply(db: Database) -> str:
        mode = LIST_SLOTS_MODE if list_art_mode(db, kind) == LIST_MASTHEAD_MODE else LIST_MASTHEAD_MODE
        set_list_art_mode(db, kind, mode)
        record_action(db, actor=actor, action=f"set_{kind}_art_mode", detail=mode)
        return mode

    mode = await lane.run(_apply)
    if mode == LIST_SLOTS_MODE:
        _announce_line(session, f"The art is now the {what} itself. Use [C]heck to see whether the list fits it.")
    else:
        _announce_line(session, f"The art is now shown above the {what}.")


def _list_art_sample_rows(db: Database, actor: User, kind: str) -> list[tuple[str, str]]:
    """The top level of the list as `actor` sees it, name and compact
    value, for the preview. People online are only known on a running
    node, so channels show none."""
    if kind == BOARD_LIST:
        from netbbs.activity import unread_post_count

        rows = [(f"[{c.name}]", "") for c in list_top_level_board_categories(db)]
        for board in list_boards(db):
            if board.category_id is None and meets_read_gate(db, actor, board):
                count = unread_post_count(db, actor, board)
                value = "not visited yet" if count is None else ("caught up" if count == 0 else f"{count} new")
                rows.append((board.name, value))
        return rows
    if kind == FILE_AREA:
        rows = [(f"[{c.name}]", "") for c in list_top_level_file_categories(db)]
        for area in list_file_areas(db):
            if area.category_id is None and meets_read_gate(db, actor, area):
                count, _ = count_listed_files(db, area)
                rows.append((area.name, f"{count} file{'' if count == 1 else 's'}"))
        return rows
    # The channel list's own filter: hidden channels, level and age gates.
    from netbbs.net.chat_flow import list_visible_channels_for

    rows = [(f"[{c.name}]", "") for c in list_top_level_channel_categories(db)]
    rows += [(channel.name, "") for channel in list_visible_channels_for(db, actor) if channel.category_id is None]
    return rows


def _list_art_fits(session: Session, art: SlotArt, column_width: int) -> str | None:
    """Why a caller with a terminal like yours gets the generated list
    instead, or `None` when the art is used -- the same tests the list
    itself makes (`pick_item`'s slot art)."""
    if getattr(session, "output_charset", None) == ASCII:
        return "your terminal reads plain ASCII"
    physical = physical_terminal_width(session)
    if art.width > physical:
        return f"the art is {art.width} columns, your terminal {physical}"
    if not list_slot_fits(art.list, column_width):
        if art.list.height < 3:
            return f"the {{list}} slot is {art.list.height} rows; it needs at least 3"
        return f"the {{list}} slot leaves names {list_name_width(art.list, column_width)} columns; they need 12"
    # The art, a nav row, a trailer row and the prompt, above the last row.
    if art.height + 3 >= session.terminal_height:
        return f"the art needs {art.height + 4} rows, your terminal has {session.terminal_height}"
    return None


async def _check_list_slot_art_screen(session: Session, lane: DatabaseLane, actor: User, kind: str) -> None:
    """[C]heck for a list screen's art: its slots, what makes it unusable,
    and whether a list fits its `{list}` slot on a terminal the size of
    yours -- with the widest value a row can show, the gate note."""
    from netbbs.net.chat_flow import NAME_GATE_NOTE

    what, path_of, enabled_of, max_bytes = _LIST_ART_SCREENS[kind]
    art, enabled, mode, rows = await lane.run(lambda db: (
        read_list_art(path_of(db), max_bytes), enabled_of(db), list_art_mode(db, kind),
        _list_art_sample_rows(db, actor, kind),
    ))
    await session.write_line(colored(f"\r\nChecking the {what} art for slots:", fg_color=MUTED_COLOR))
    if not enabled:
        await session.write_line(highlight_hotkeys(
            "Callers don't see this art yet: it's switched off. Use [E]nable.", color=WARNING_COLOR
        ))
    if mode != LIST_SLOTS_MODE:
        await session.write_line(highlight_hotkeys(
            "Callers see this above the list, not as it: [M]ode changes that.", color=WARNING_COLOR
        ))
    if art is None:
        await session.write_line("No usable art file. Apply a gallery sample, upload one, or draw one first.")
    else:
        await session.write_line(highlight_report(f"Art: {art.width} columns, {art.height} rows.", color=VALUE_COLOR))
        for line in describe_slots(art) or ["No slots found."]:
            await session.write_line(highlight_report(f"  {line}", color=VALUE_COLOR))
        if art.problems:
            for problem in art.problems:
                await session.write_line(colored(f"  Problem: {problem}", fg_color=ERROR_COLOR))
        else:
            widest = max((display_width(value) for _, value in rows), default=0)
            for label, column_width in (
                ("Your list", widest),
                ("A list with an entry that needs verification", max(widest, display_width(NAME_GATE_NOTE))),
            ):
                reason = _list_art_fits(session, art, column_width)
                if reason is None:
                    await session.write_line(_fits_line(
                        label, f", {art.list.height} entries a page, names up to "
                        f"{list_name_width(art.list, column_width)} columns",
                    ))
                else:
                    await session.write_line(
                        colored(f"  {label}: generated list instead -- {reason}.", fg_color=WARNING_COLOR)
                    )
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _preview_list_slot_art(session: Session, lane: DatabaseLane, actor: User, kind: str) -> None:
    """[P]review in slots mode: the first page of the list's top level as
    you see it, drawn into the art."""
    what, path_of, _enabled_of, max_bytes = _LIST_ART_SCREENS[kind]
    art, rows, fields = await lane.run(lambda db: (
        read_list_art(path_of(db), max_bytes), _list_art_sample_rows(db, actor, kind),
        list_slot_fields(session, db, actor),
    ))
    if art is None or art.problems or art.list is None:
        await session.write_line(colored(
            f"\r\nThe {what} art can't be used as the list; [C]heck says why. Callers get the generated list.",
            fg_color=WARNING_COLOR,
        ))
    else:
        column_width = max((display_width(value) for _, value in rows), default=0)
        reason = _list_art_fits(session, art, column_width)
        page = rows[:art.list.height]
        pages = max(1, -(-len(rows) // art.list.height))
        fields.update(title=what.capitalize(), page=f"1/{pages}", count=str(len(rows)))
        drawn = None if reason is not None else render_list_slot_art(
            art, fields=fields,
            rows=[ListSlotRow(f"{number:02d}.", name, value) for number, (name, value) in enumerate(page, start=1)],
            column_width=column_width, ellipsis=ellipsis_for(session),
        )
        if drawn is None:
            await session.write_line(colored(
                f"\r\nCallers like you get the generated list instead: {reason or 'the list does not fit'}.",
                fg_color=WARNING_COLOR,
            ))
        else:
            await write_preview_art_text(
                session, drawn, speed=await lane.run(art_speed, kind), limit=await lane.run(art_time_limit, kind),
            )
            await session.write(move_cursor(art.height + 1, 1))
            if kind == CHAT_CHANNEL_PICKER:
                await session.write_line(colored("(people online are filled in on a running node)", fg_color=MUTED_COLOR))
    await session.write_line(continue_prompt())
    await session.read_any_key()


@located("Board list masthead")
async def _board_list_masthead_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            if await lane.run(list_art_mode, BOARD_LIST) == LIST_SLOTS_MODE:
                await _preview_list_slot_art(session, lane, actor, BOARD_LIST)
            else:
                await _preview_board_list_masthead_screen(session, lane)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_board_list_masthead_screen(session, lane, actor)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_board_list_masthead_screen(session, lane, actor)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_board_list_masthead_screen(session, lane, actor)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _board_list_masthead_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _board_list_masthead_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=board_list_banner_path, label="the message-board masthead", audit_action="upload_board_list_banner")
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=board_list_banner_status, set_enabled=set_board_list_banner_enabled,
                label="the message-board masthead", audit_action="remove_board_list_banner",
            )
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "m":
            await session.write_line("")
            await _toggle_list_art_mode(session, lane, actor, BOARD_LIST)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "s":
            await session.write_line("")
            await _cycle_art_speed(lane, actor, BOARD_LIST)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "t":
            await session.write_line("")
            await _cycle_art_time_limit(lane, actor, BOARD_LIST)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "c":
            await session.write_line("")
            await _check_list_slot_art_screen(session, lane, actor, BOARD_LIST)
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(board_list_banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_board_list_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_board_list_masthead_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(board_list_banner_status)
    mode = await lane.run(list_art_mode, BOARD_LIST)
    rows = await _write_counted(session, "\r\n" + screen_title("Board list masthead",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown above every board-browsing view -- the top level, a category, or a "
        "Community.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    speed = await lane.run(art_speed, BOARD_LIST)
    time_limit = await lane.run(art_time_limit, BOARD_LIST)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce, speed=speed)], unicode_style=unicode_style)
    await session.write_line("")
    rows += 1
    rows += await _write_wrapped_subtitle(session, _list_art_mode_line(mode))
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show it as callers see it"),
            MenuEntry(label=menu_key("S", "peed"), brief=_art_speed_brief(speed)),
            _art_time_limit_entry(time_limit),
            MenuEntry(
                label=menu_key("M", "ode"),
                brief="Make the art the list itself" if mode == LIST_MASTHEAD_MODE else "Show the art above the list",
            ),
            MenuEntry(label=menu_key("C", "heck"), brief="Slots, problems and list fit"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the masthead on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the masthead off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the masthead art"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample masthead"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_board_list_masthead_screen(session: Session, lane: DatabaseLane) -> None:
    status, masthead_text = await lane.run(lambda db: (board_list_banner_status(db), load_board_list_banner(db)))
    await session.write_line(colored("\r\nPreviewing board list masthead as shown above the board list:", fg_color=MUTED_COLOR))
    if masthead_text:
        await write_preview_art(
            session, masthead_text, speed=await lane.run(art_speed, BOARD_LIST), limit=await lane.run(art_time_limit, BOARD_LIST),
        )
    else:
        await _write_banner_not_live(session, status, callers_see="no masthead")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_board_list_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(board_list_banner_status)
    if not status.exists:
        _announce(session, f"No masthead file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_BOARD_LIST_BANNER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Masthead file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_BOARD_LIST_BANNER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_board_list_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_board_list_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "Board list masthead enabled. Use [P]review to verify it looks right.")


async def _disable_board_list_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = board_list_banner_status(db)
        set_board_list_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_board_list_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"Board list masthead disabled. Your file at {status.path} was left in place.")


async def _edit_board_list_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(board_list_banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_board_list_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _board_list_masthead_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a message-board-specific masthead."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(BOARD_LIST_MASTHEAD_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="Board list masthead gallery",
            empty_message="No bundled masthead samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_board_list_masthead_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the board list masthead"):
            continue

        def _apply(db: Database) -> Path:
            path = board_list_banner_path(db)
            path.write_bytes(data)
            set_board_list_banner_enabled(db, True)
            # A list sample is the list itself; any other sample goes above it.
            set_list_art_mode(db, BOARD_LIST, LIST_SLOTS_MODE if preset.mode == "slots" else LIST_MASTHEAD_MODE)
            record_action(db, actor=actor, action="apply_board_list_banner_preset", detail=f"{preset.key} -> {path}")
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _board_list_masthead_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `board_list_banner_path` instead -- see that
    function's own docstring for the locked design (browsable root,
    copy-not-reference load semantics, validate-before-copy)."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = board_list_banner_path(db).parent
        return _browsable_ans_files(directory, exclude=board_list_banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Board list masthead -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_BOARD_LIST_BANNER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_BOARD_LIST_BANNER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the board list masthead"):
            continue

        def _apply(db: Database) -> Path:
            target = board_list_banner_path(db)
            target.write_bytes(data)
            set_board_list_banner_enabled(db, True)
            record_action(db, actor=actor, action="load_board_list_banner_from_file", detail=f"{path} -> {target}")
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("File area masthead")
async def _file_area_masthead_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            if await lane.run(list_art_mode, FILE_AREA) == LIST_SLOTS_MODE:
                await _preview_list_slot_art(session, lane, actor, FILE_AREA)
            else:
                await _preview_file_area_masthead_screen(session, lane)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_file_area_masthead_screen(session, lane, actor)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_file_area_masthead_screen(session, lane, actor)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_file_area_masthead_screen(session, lane, actor)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _file_area_masthead_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _file_area_masthead_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=file_area_banner_path, label="the file-area masthead", audit_action="upload_file_area_banner")
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=file_area_banner_status, set_enabled=set_file_area_banner_enabled,
                label="the file-area masthead", audit_action="remove_file_area_banner",
            )
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "m":
            await session.write_line("")
            await _toggle_list_art_mode(session, lane, actor, FILE_AREA)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "s":
            await session.write_line("")
            await _cycle_art_speed(lane, actor, FILE_AREA)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "t":
            await session.write_line("")
            await _cycle_art_time_limit(lane, actor, FILE_AREA)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "c":
            await session.write_line("")
            await _check_list_slot_art_screen(session, lane, actor, FILE_AREA)
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(file_area_banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_file_area_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_file_area_masthead_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(file_area_banner_status)
    mode = await lane.run(list_art_mode, FILE_AREA)
    rows = await _write_counted(session, "\r\n" + screen_title("File area masthead",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown above every file-area-browsing view -- the top level, a category, or a "
        "Community.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    speed = await lane.run(art_speed, FILE_AREA)
    time_limit = await lane.run(art_time_limit, FILE_AREA)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce, speed=speed)], unicode_style=unicode_style)
    await session.write_line("")
    rows += 1
    rows += await _write_wrapped_subtitle(session, _list_art_mode_line(mode))
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show it as callers see it"),
            MenuEntry(label=menu_key("S", "peed"), brief=_art_speed_brief(speed)),
            _art_time_limit_entry(time_limit),
            MenuEntry(
                label=menu_key("M", "ode"),
                brief="Make the art the list itself" if mode == LIST_MASTHEAD_MODE else "Show the art above the list",
            ),
            MenuEntry(label=menu_key("C", "heck"), brief="Slots, problems and list fit"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the masthead on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the masthead off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the masthead art"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample masthead"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_file_area_masthead_screen(session: Session, lane: DatabaseLane) -> None:
    status, masthead_text = await lane.run(lambda db: (file_area_banner_status(db), load_file_area_banner(db)))
    await session.write_line(colored("\r\nPreviewing file area masthead as shown above the file-area list:", fg_color=MUTED_COLOR))
    if masthead_text:
        await write_preview_art(
            session, masthead_text, speed=await lane.run(art_speed, FILE_AREA), limit=await lane.run(art_time_limit, FILE_AREA),
        )
    else:
        await _write_banner_not_live(session, status, callers_see="no masthead")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_file_area_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(file_area_banner_status)
    if not status.exists:
        _announce(session, f"No masthead file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_FILE_AREA_BANNER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Masthead file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_FILE_AREA_BANNER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_file_area_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_file_area_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "File area masthead enabled. Use [P]review to verify it looks right.")


async def _disable_file_area_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = file_area_banner_status(db)
        set_file_area_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_file_area_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"File area masthead disabled. Your file at {status.path} was left in place.")


async def _edit_file_area_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(file_area_banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_file_area_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _file_area_masthead_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a file-archive-specific masthead."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(FILE_AREA_MASTHEAD_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="File area masthead gallery",
            empty_message="No bundled masthead samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_file_area_masthead_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the file area masthead"):
            continue

        def _apply(db: Database) -> Path:
            path = file_area_banner_path(db)
            path.write_bytes(data)
            set_file_area_banner_enabled(db, True)
            # A list sample is the list itself; any other sample goes above it.
            set_list_art_mode(db, FILE_AREA, LIST_SLOTS_MODE if preset.mode == "slots" else LIST_MASTHEAD_MODE)
            record_action(db, actor=actor, action="apply_file_area_banner_preset", detail=f"{preset.key} -> {path}")
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _file_area_masthead_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `file_area_banner_path` instead."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = file_area_banner_path(db).parent
        return _browsable_ans_files(directory, exclude=file_area_banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="File area masthead -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_FILE_AREA_BANNER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_FILE_AREA_BANNER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the file area masthead"):
            continue

        def _apply(db: Database) -> Path:
            target = file_area_banner_path(db)
            target.write_bytes(data)
            set_file_area_banner_enabled(db, True)
            record_action(db, actor=actor, action="load_file_area_banner_from_file", detail=f"{path} -> {target}")
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


@located("Chat channel picker masthead")
async def _chat_channel_picker_masthead_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "p":
            await session.write_line("")
            if await lane.run(list_art_mode, CHAT_CHANNEL_PICKER) == LIST_SLOTS_MODE:
                await _preview_list_slot_art(session, lane, actor, CHAT_CHANNEL_PICKER)
            else:
                await _preview_chat_channel_picker_masthead_screen(session, lane)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "e":
            await session.write_line("")
            await _enable_chat_channel_picker_masthead_screen(session, lane, actor)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "d":
            await session.write_line("")
            await _disable_chat_channel_picker_masthead_screen(session, lane, actor)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "o":
            await session.write_line("")
            await _edit_chat_channel_picker_masthead_screen(session, lane, actor)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _chat_channel_picker_masthead_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _chat_channel_picker_masthead_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_banner_piece(session, lane, actor, path_of=chat_channel_picker_banner_path, label="the chat-channel masthead", audit_action="upload_chat_channel_picker_banner")
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "r":
            await session.write_line("")
            await _remove_banner_file(
                session, lane, actor, status_of=chat_channel_picker_banner_status, set_enabled=set_chat_channel_picker_banner_enabled,
                label="the chat-channel masthead", audit_action="remove_chat_channel_picker_banner",
            )
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "m":
            await session.write_line("")
            await _toggle_list_art_mode(session, lane, actor, CHAT_CHANNEL_PICKER)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "s":
            await session.write_line("")
            await _cycle_art_speed(lane, actor, CHAT_CHANNEL_PICKER)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "t":
            await session.write_line("")
            await _cycle_art_time_limit(lane, actor, CHAT_CHANNEL_PICKER)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "c":
            await session.write_line("")
            await _check_list_slot_art_screen(session, lane, actor, CHAT_CHANNEL_PICKER)
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await session.write_line("")
            await _banner_help_screen(
                session, path=await lane.run(chat_channel_picker_banner_path), has_gallery=True,
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_chat_channel_picker_masthead_menu(session, lane, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


async def _draw_chat_channel_picker_masthead_menu(
    session: Session, lane: DatabaseLane, description_level: str, redraw_in_place: bool,
    unicode_style: bool, collapsed: bool, header_color: int | tuple[int, int, int],
) -> None:
    status = await lane.run(chat_channel_picker_banner_status)
    mode = await lane.run(list_art_mode, CHAT_CHANNEL_PICKER)
    rows = await _write_counted(session, "\r\n" + screen_title("Chat channel picker masthead",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width,
            clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color,
            node_name_gradient=session.node_name_gradient))
    rows += await _write_wrapped_subtitle(
        session,
        "Shown above every channel-picker view -- the top level, a category, or a "
        "Community. Never inside a live channel.",
    )
    sauce = await asyncio.to_thread(_banner_sauce, status)
    speed = await lane.run(art_speed, CHAT_CHANNEL_PICKER)
    time_limit = await lane.run(art_time_limit, CHAT_CHANNEL_PICKER)
    rows += await _write_sections(session, [_banner_status_section(status, unicode_style=unicode_style, sauce=sauce, speed=speed)], unicode_style=unicode_style)
    await session.write_line("")
    rows += 1
    rows += await _write_wrapped_subtitle(session, _list_art_mode_line(mode))
    await _write_banner_menu(
        session,
        [
            MenuEntry(label=menu_key("P", "review"), brief="Show it as callers see it"),
            MenuEntry(label=menu_key("S", "peed"), brief=_art_speed_brief(speed)),
            _art_time_limit_entry(time_limit),
            MenuEntry(
                label=menu_key("M", "ode"),
                brief="Make the art the list itself" if mode == LIST_MASTHEAD_MODE else "Show the art above the list",
            ),
            MenuEntry(label=menu_key("C", "heck"), brief="Slots, problems and list fit"),
            MenuEntry(label=menu_key("E", "nable"), brief="Turn the masthead on"),
            MenuEntry(label=menu_key("D", "isable"), brief="Turn the masthead off"),
            MenuEntry(label=menu_key("O", "pen editor"), brief="Edit the masthead art"),
            MenuEntry(label=menu_key("G", "allery"), brief="Apply a bundled sample masthead"),
            MenuEntry(label=menu_key("F", "rom disk"), brief="Load your own .ans from this node"),
            MenuEntry(label=menu_key("U", "pload"), brief="Send an .ans from your computer"),
            *_remove_file_entry(status),
            MenuEntry(label=menu_key("B", "ack"), brief="Return to Mastheads"),
        ],
        description_level,
        used_rows=rows,
        help_text="(Ctrl-H for where to place your own .ans files)",
    )
    await _choice_prompt(session)


async def _preview_chat_channel_picker_masthead_screen(session: Session, lane: DatabaseLane) -> None:
    status, masthead_text = await lane.run(
        lambda db: (chat_channel_picker_banner_status(db), load_chat_channel_picker_banner(db))
    )
    await session.write_line(
        colored("\r\nPreviewing chat channel picker masthead as shown above the channel picker:", fg_color=MUTED_COLOR)
    )
    if masthead_text:
        await write_preview_art(
            session, masthead_text, speed=await lane.run(art_speed, CHAT_CHANNEL_PICKER), limit=await lane.run(art_time_limit, CHAT_CHANNEL_PICKER),
        )
    else:
        await _write_banner_not_live(session, status, callers_see="no masthead")
    await session.write_line(continue_prompt())
    await session.read_any_key()


async def _enable_chat_channel_picker_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    status = await lane.run(chat_channel_picker_banner_status)
    if not status.exists:
        _announce(session, f"No masthead file found at {status.path}. Place a .ans file there first, then enable.", color=WARNING_COLOR)
        return
    if (status.size_bytes or 0) > MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES:
        _announce_line(session,
            colored(
                f"Masthead file at {status.path} is {status.size_bytes} bytes, over the "
                f"{MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES} byte limit -- not enabling.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    def _apply(db: Database) -> None:
        set_chat_channel_picker_banner_enabled(db, True)
        record_action(db, actor=actor, action="enable_chat_channel_picker_banner", detail=str(status.path))

    await lane.run(_apply)
    _announce_line(session, "Chat channel picker masthead enabled. Use [P]review to verify it looks right.")


async def _disable_chat_channel_picker_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    def _apply(db: Database):
        status = chat_channel_picker_banner_status(db)
        set_chat_channel_picker_banner_enabled(db, False)
        record_action(db, actor=actor, action="disable_chat_channel_picker_banner", detail=str(status.path))
        return status

    status = await lane.run(_apply)
    _announce_line(session, f"Chat channel picker masthead disabled. Your file at {status.path} was left in place.")


async def _edit_chat_channel_picker_masthead_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    path = await lane.run(chat_channel_picker_banner_path)
    initial_bytes = path.read_bytes() if path.exists() else None
    draft_path = path.parent / f"{path.name}.draft"

    result = await edit_ansi_art(
        session, initial_bytes=initial_bytes, draft_path=draft_path,
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
    )
    if result is None:
        _announce_line(session, colored("\r\nNo changes saved.", fg_color=MUTED_COLOR))
        return

    path.write_bytes(result)
    await lane.run(record_action, actor=actor, action="edit_chat_channel_picker_banner", detail=str(path))
    _announce_saved(session, "Saved ", path, ". Use [P]review to verify it looks right.")


async def _chat_channel_picker_masthead_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Preview and apply a live-chat-specific masthead."""
    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(CHAT_CHANNEL_PICKER_MASTHEAD_PRESETS, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: f"{pair[1].depth} -- {pair[1].description}",
            title="Chat channel picker masthead gallery",
            empty_message="No bundled masthead samples.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        preset = selection[1]
        data = load_chat_channel_picker_masthead_preset(preset)
        await session.write_line(colored(f"\r\nPreviewing {preset.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Apply {preset.name!r} as the chat channel picker masthead"):
            continue

        def _apply(db: Database) -> Path:
            path = chat_channel_picker_banner_path(db)
            path.write_bytes(data)
            set_chat_channel_picker_banner_enabled(db, True)
            # A list sample is the list itself; any other sample goes above it.
            set_list_art_mode(
                db, CHAT_CHANNEL_PICKER, LIST_SLOTS_MODE if preset.mode == "slots" else LIST_MASTHEAD_MODE,
            )
            record_action(
                db, actor=actor, action="apply_chat_channel_picker_banner_preset", detail=f"{preset.key} -> {path}"
            )
            return path

        path = await lane.run(_apply)
        _announce_saved(session, "Applied and enabled. Saved to ", path, ". Use [P]review to verify it looks right.")
        return


async def _chat_channel_picker_masthead_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool,
) -> None:
    """Same load-from-disk screen as `_main_menu_banner_filesystem_screen`
    (issue #170), against `chat_channel_picker_banner_path` instead."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = chat_channel_picker_banner_path(db).parent
        return _browsable_ans_files(directory, exclude=chat_channel_picker_banner_path(db)), directory

    files, directory = await lane.run(_list)
    if not files:
        # Announced, not held behind a keypress: it is on the menu this returns to.
        _announce(
            session,
            f"No other .ans files found in {directory}. Place one there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.",
            color=MUTED_COLOR,
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Chat channel picker masthead -- load from disk",
            empty_message="No other .ans files found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        path = selection[1]

        size = path.stat().st_size
        if size > MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES:
            await session.write_line(colored(
                f"{path.name} is {size} bytes, over the {MAX_CHAT_CHANNEL_PICKER_BANNER_SIZE_BYTES} byte "
                f"limit -- not loading.", fg_color=MUTED_COLOR,
            ))
            await session.write_line(continue_prompt())
            await session.read_any_key()
            continue

        data = path.read_bytes()
        await session.write_line(colored(f"\r\nPreviewing {path.name!r}:", fg_color=MUTED_COLOR))
        await write_preformatted_line(session, decode_banner_bytes(data) + RESET)

        if not await _preview_apply_choice(session, f"Load {path.name!r} as the chat channel picker masthead"):
            continue

        def _apply(db: Database) -> Path:
            target = chat_channel_picker_banner_path(db)
            target.write_bytes(data)
            set_chat_channel_picker_banner_enabled(db, True)
            record_action(
                db, actor=actor, action="load_chat_channel_picker_banner_from_file", detail=f"{path} -> {target}"
            )
            return target

        target = await lane.run(_apply)
        _announce_saved(session, "Loaded and enabled. Saved to ", target, ". Use [P]review to verify it looks right.")
        return


_THEME_SLOT_LABELS = {"accent": "Accent color", "header": "Header color", "clock": "Clock color"}


_THEME_SLOT_GETTERS = {
    "accent": accent_color_override, "header": header_color_override, "clock": clock_color_override,
}


_THEME_SLOT_SETTERS = {
    "accent": set_accent_color_override, "header": set_header_color_override, "clock": set_clock_color_override,
}


_THEME_SLOT_DEFAULTS = {"accent": ACCENT_COLOR, "header": HEADER_COLOR, "clock": CLOCK_COLOR}


def _theme_sample_text(slot: str) -> str:
    """One real rendered fragment per slot -- board name for accent,
    a section-header banner for header, a main-menu clock for clock --
    rather than an abstract swatch, so a SysOp judges the color against
    the same shape of text it will actually appear on."""
    if slot == "accent":
        return "  MetroBBS Underground"
    if slot == "header":
        return "== Node management =="
    return "14:32:07"


def _theme_sample_line(slot: str, color: int | tuple[int, int, int]) -> str:
    return colored(_theme_sample_text(slot), fg_color=color, bold=True)


def _theme_color_status_line(label: str, rgb: tuple[int, int, int] | None) -> str:
    if rgb is None:
        return colored(f"{label}: ", fg_color=LABEL_COLOR) + colored("default", fg_color=MUTED_COLOR)
    value = f"{rgb[0]},{rgb[1]},{rgb[2]}"
    swatch = colored("****", fg_color=rgb, bold=True)
    return colored(f"{label}: ", fg_color=LABEL_COLOR) + swatch + colored(f"  {value}", fg_color=METADATA_COLOR)


def _parse_rgb(raw: str) -> tuple[int, int, int] | None:
    parts = raw.split(",")
    if len(parts) != 3:
        return None
    try:
        candidate = tuple(int(part.strip()) for part in parts)
    except ValueError:
        return None
    if not all(0 <= value <= 255 for value in candidate):
        return None
    return candidate  # type: ignore[return-value]


def _theme_color_field(slot: str) -> Callable[[Session, DatabaseLane, dict], Awaitable[None]]:
    """One branding slot on the node-colors editor: an R,G,B triple,
    'default' to clear the override, blank to keep. Validation happens
    here, with the draft untouched on a bad entry; nothing is written
    until [S]ave."""
    label = _THEME_SLOT_LABELS[slot]

    @inline_field
    async def prompt(session: Session, lane: DatabaseLane, draft: dict) -> None:
        current = draft[slot]
        current_text = f"{current[0]},{current[1]},{current[2]}" if current is not None else "default"
        await write_field_prompt(
            session,
            colored(f"{label}: R,G,B (each 0-255) or 'default' ({_EDIT_HINT}):", fg_color=MUTED_COLOR),
            hint="R,G,B or default; Enter saves, Esc cancels",
        )
        try:
            raw = (await _read_seeded_line(session, initial=current_text)).strip()
        except InputCancelled:
            return
        if not raw or raw.lower() == current_text:
            return
        if raw.lower() == "default":
            draft[slot] = None
            return
        rgb = _parse_rgb(raw)
        if rgb is None:
            await write_field_message(
                session, colored("Not a valid R,G,B triple (each 0-255) -- no change.", fg_color=ERROR_COLOR),
            )
            return
        draft[slot] = rgb

    return prompt


def _theme_preview_preamble(draft: dict) -> str:
    """The editor's own live preview (issue #282): every slot's sample
    line at truecolor and 256-color depth for whatever the draft holds
    right now -- what the separate [P]review screen used to show, and
    the "preview before confirm" issue #162 required, without a second
    trip or a yes/no."""
    lines = [
        colored(
            "Branding only -- status colors (error/success/warning/etc.) always stay standard, "
            "so a caller can trust what they mean on any node.",
            fg_color=MUTED_COLOR,
        )
    ]
    for slot in ("accent", "header", "clock"):
        rgb = draft[slot]
        lines.append(colored(f"{_THEME_SLOT_LABELS[slot]}:", fg_color=LABEL_COLOR, bold=True))
        if rgb is None:
            lines.append(
                colored("  Default:   ", fg_color=MUTED_COLOR) + _theme_sample_line(slot, _THEME_SLOT_DEFAULTS[slot])
            )
        else:
            lines.append(colored("  Truecolor: ", fg_color=MUTED_COLOR) + _theme_sample_line(slot, rgb))
            lines.append(colored("  256-color: ", fg_color=MUTED_COLOR) + _theme_sample_line(slot, nearest_256(rgb)))
    return "\r\n".join(lines)


async def _theme_colors_menu(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Settings -> Node colors (issue #162, reshaped by issue #282): one
    draft editor for the three overridable branding slots, previewed
    live above the fields, applied together on [S]ave. Replaces a menu
    whose per-slot screens only showed the current value inside an
    editable prompt and then asked "Apply this ...?" -- three times over
    -- plus a separate preview screen."""
    def _load(db: Database) -> dict:
        return {
            "accent": accent_color_override(db),
            "header": header_color_override(db),
            "clock": clock_color_override(db),
        }

    draft = await lane.run(_load)
    starting = dict(draft)

    def _render(slot: str) -> Callable[[dict], str]:
        return lambda d: f"{d[slot][0]},{d[slot][1]},{d[slot][2]}" if d[slot] is not None else "default"

    fields = [
        FieldSpec(
            key="accent", label="Accent",
            render=_render("accent"), prompt=_theme_color_field("accent"),
            brief="Board/channel/user-name color",
            help="Used for board, channel, and user names and other navigable items. R,G,B or 'default'.",
        ),
        FieldSpec(
            key="header", label="Header",
            render=_render("header"), prompt=_theme_color_field("header"),
            brief="Section-header color",
            help="Used for section titles and frame borders. R,G,B or 'default'.",
        ),
        FieldSpec(
            key="clock", label="Clock",
            render=_render("clock"), prompt=_theme_color_field("clock"),
            brief="Main-menu clock color",
            help="Used for the main-menu prompt's time display. R,G,B or 'default'.",
        ),
    ]

    async def save(draft: dict) -> bool:
        def _apply(db: Database) -> list[str]:
            messages = []
            for slot in ("accent", "header", "clock"):
                if draft[slot] == starting[slot]:
                    continue
                label = _THEME_SLOT_LABELS[slot]
                _THEME_SLOT_SETTERS[slot](db, draft[slot])
                if draft[slot] is None:
                    previous = starting[slot]
                    record_action(
                        db, actor=actor, action=f"clear_{slot}_color_override",
                        detail=f"{previous[0]},{previous[1]},{previous[2]}",
                    )
                    messages.append(f"{label} reverted to the default.")
                else:
                    rgb = draft[slot]
                    record_action(db, actor=actor, action=f"set_{slot}_color_override", detail=f"{rgb[0]},{rgb[1]},{rgb[2]}")
                    messages.append(f"{label} updated.")
            return messages

        messages = await lane.run(_apply)
        for message in messages or ["No changes."]:
            await session.write_line(message)
        return True

    redraw_in_place, redraw_hint = await lane.run(_resolve_redraw_preference, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    await edit_resource_draft(
        session, lane,
        title="Node colors",
        fields=fields, draft=draft, save=save,
        save_menu_text=menu_key("S", "ave"), back_menu_text=menu_key("B", "ack"),
        description_level=await lane.run(menu_description_level, actor),
        redraw_in_place=redraw_in_place, redraw_hint=redraw_hint,
        preamble=_theme_preview_preamble,
        unicode_style=unicode_style,
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
        accent_color=await lane.run(effective_accent_color_256),
        header_color=await lane.run(effective_header_color_256),
    )
