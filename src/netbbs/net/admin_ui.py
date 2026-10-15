"""Shared screen machinery of the SysOp console.

Announcements, choice prompts, pickers, draft editors, detail views, the
panel writers and the upload helpers that every `admin_*` module builds its
screens from. Split out of `netbbs.net.admin_flow` (issue #1311).
"""

from __future__ import annotations

import re
import weakref
from dataclasses import dataclass
from typing import Any, Sequence

from netbbs.auth.users import User
from netbbs.files.storage import new_incoming_temp_path
from netbbs.net.file_transfer import UPLOAD, install_and_record
from netbbs.sysop_uploads import SysOpUploadError, SysOpUploadTarget
from netbbs.net.location import crumbs
from netbbs.net.picker import pick_item as _pick_item
from netbbs.net.resource_editor import edit_resource_draft as _edit_resource_draft
from netbbs.net.session import Session, write_prompt
from netbbs.net.session_activity import records_activity
from netbbs.net.redraw_preference import redraw_in_place_enabled
from netbbs.net.breadcrumb_preference import breadcrumb_collapsed_enabled
from netbbs.net.node_theme import effective_accent_color_256, effective_header_color_256
from netbbs.net.unicode_style_preference import unicode_style_enabled
from netbbs.net.detail_view import show_detail as _show_detail_view
from netbbs.rendering import (
    MUTED_COLOR,
    MenuEntry,
    action_bar,
    colored,
    counts_row,
    double_frame,
    menu_grid,
    menu_key,
    sanitize_text,
    screen_title,
    status_result,
    visible_width,
    wrap_to_width,
)
from netbbs.rendering.detail import Section, render_sections
from netbbs.net import notices as _notices
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane


def _menu_row(
    entries: list[MenuEntry], description_level: str, *, width: int, height: int, degraded: bool = False
) -> str:
    """Shared off/on branch for every hotkey-row menu in this module
    (issue #160's rollout, commit 1b1da33's own established pattern):
    `menu_grid` always renders one entry per line, even with
    descriptions off, so it isn't a byte-for-byte substitute for
    `action_bar`'s packed row at that level -- keep `action_bar` there
    so no screen's height changes for a caller who hasn't opted into
    descriptions, and only switch to `menu_grid` once they have.

    `degraded=True` means the caller (`_degrade_description_level`)
    already forced `description_level` to `"off"` before it ever reached
    here, because its own telemetry panel ate into the available height
    -- a real degrade of the SysOp's actual preference, not the level
    they asked for. `menu_grid` has its own notice for exactly this
    situation (`descriptions_collapsed`), but only when *it* detects the
    squeeze; since `description_level == "off"` takes the `action_bar`
    branch instead and never reaches `menu_grid` at all, that notice is
    unreachable here and the degrade would otherwise happen silently
    (PR #197 review). Append the equivalent notice ourselves, styled the
    same way `menu_grid` styles its own."""
    if description_level == "off":
        result = action_bar([e.label for e in entries], width=width, height=height)
        if degraded:
            notice_lines = [
                colored(wrapped, fg_color=MUTED_COLOR)
                for wrapped in wrap_to_width("Descriptions hidden -- terminal too short to show them.", width)
            ]
            result = f"{result}\r\n\r\n" + "\r\n".join(notice_lines)
        return result
    return menu_grid([("", entries)], width=width, height=height, description_level=description_level)


def _wrap_counts_panel(label: str, pairs: Sequence[tuple[str, int]], *, width: int, continuation: str = "  ") -> list[str]:
    """Split a `label` + `counts_row(pairs)` telemetry summary across as
    many lines as the frame's real `width` needs. `counts_row` renders
    every pair onto a single unconditional line, and `double_frame`
    never truncates or wraps its input -- callers own pre-fitting their
    own content (see that function's own docstring) -- so a compact
    sub-console panel with several fields silently ran the box's right
    border open on real narrow-but-supported terminals until this
    existed. `continuation` prefixes every line after the first (the
    label itself only ever appears once)."""
    segments = [counts_row([pair]) for pair in pairs]
    if not segments:
        return [label]
    lines: list[str] = []
    prefix = label
    current: list[str] = []
    current_width = visible_width(prefix)
    for segment in segments:
        seg_width = visible_width(segment)
        sep_width = 2 if current else 0
        # No `current and ...` guard here: the label alone can already
        # be wide enough (a status badge, e.g.) that even the *first*
        # segment on an otherwise-empty line doesn't fit -- omitting
        # this check for that case was the original bug, since it let a
        # single wide-labeled line through unwrapped every time.
        if current_width + sep_width + seg_width > width:
            lines.append(prefix + "  ".join(current))
            prefix = continuation
            current = []
            current_width = visible_width(prefix)
            sep_width = 0
        current.append(segment)
        current_width += sep_width + seg_width
    lines.append(prefix + "  ".join(current))
    return lines


def _wrap_panel_sentence(text: str, *, prefix: str, width: int, unicode_style: bool) -> list[str]:
    """Word-wrap a plain-text panel sentence (not a `counts_row` pair --
    see `_wrap_counts_panel` for those, and `_fit` above for the one
    case -- an arbitrary-length backup/update-check message -- where a
    hard cut is the right call instead) across as many lines as a boxed
    panel's real `width` can hold, `prefix` (e.g. two leading spaces)
    budgeted on every line so a multi-line sentence stays indented
    consistently with its own first line. Wrapping, not cutting,
    because these are short, fixed, known-in-advance phrases -- chopping
    one off mid-word to fit a 40-column terminal would lose real
    information for no reason a cut on unpredictable external text
    doesn't have. An unboxed ASCII-fallback panel has no width
    constraint to wrap against, so `text` passes through as a single
    line unchanged there."""
    if not unicode_style:
        return [text]
    budget = max(1, width - visible_width(prefix))
    return wrap_to_width(text, budget) or [text]


async def _write_panel(
    session: Session, panel: list[str], *, unicode_style: bool, header_color: int | tuple[int, int, int]
) -> None:
    """Box `panel` in a `double_frame` when the terminal is wide enough
    to hold one, else print its lines flat and unboxed -- the same
    fallback already used when `unicode_style` is off. `double_frame`
    itself raises below width 4, but `netbbs.net.session.
    clamp_terminal_size` only floors a client-reported `terminal_width`
    at 1, so a client legitimately reporting width 1-3 must not reach
    that call at all."""
    frame_width = min(session.terminal_width, 78)
    if unicode_style and frame_width >= 4:
        await session.write_line(double_frame(panel, width=frame_width, header_color=header_color))
    else:
        for line in panel:
            await session.write_line(line)


def _announce(session: Session, text: str, *, error: bool = False, color: int | None = None) -> None:
    """Queue one outcome line for the next console screen drawn on `session`."""
    _notices.announce(session, text, tone="error" if error else "success", color=color)


_CLEAR_SEQUENCE = "\x1b[2J"


_LEADING_BREAK = re.compile(r"^((?:\x1b\[[0-9;]*m)*)(?:\r\n)+")


def _announce_line(session: Session, line: str) -> None:
    """Queue a line exactly as an action would have written it; shared with
    the caller's screens (`notices.announce_line`, issue #1124)."""
    _notices.announce_line(session, line)


def _confirmation_refused(session: Session, typed: str, expected: str, *, kept: str = "Nothing was deleted.") -> None:
    """The outcome of a type-the-name confirmation that did not match (issue
    #1119). An empty answer is a plain cancel, as the prompt offers. Anything
    else was most likely an attempt to confirm that went wrong, so it is a
    warning that says what was typed and that nothing happened -- a muted
    "Cancelled." under a screen that just redrew read as if nothing had."""
    typed = typed.strip()
    if not typed:
        _announce_line(session, "Cancelled.")
        return
    _notices.announce_styled(
        session,
        status_result(f"Cancelled: {sanitize_text(typed)!r} is not {sanitize_text(expected)!r}. {kept}", "warning"),
    )


def _announce_styled(session: Session, line: str) -> None:
    """Queue a line that is already sanitized and styled (a badge, a captured row)."""
    _notices.announce_styled(session, line)


def _pending_notice_rows(session: Session) -> int:
    return _notices.pending_notice_rows(session)


def _take_notices(session: Session) -> list[str]:
    return _notices.take_notices(session)


class _TrailingOutput:
    """A stand-in session for a flow that lives in another module and writes
    its own outcome -- `netbbs.net.managed_dns_flow`'s register, release, rename
    and cancel, which first-run onboarding also calls and which therefore
    cannot `_announce`.

    Lines are held rather than written. Anything else the flow does with the
    session -- a prompt, a read -- writes the held lines first, in order, so
    what the SysOp is asked is still asked under the text that explains it.
    Whatever is still held when the flow returns was written after its last
    question: that is its outcome, and `announce_rest` queues it for the screen
    the console draws next instead of letting that screen's clear erase it.
    Login uses it the same way for first-run onboarding and the character-set
    question, whose outcome the first main menu shows (issue #923).

    Only ever handed to a flow that uses the session to read and write. It is
    not the session: a flow that compares sessions by identity (the node
    controls' registry does) must be given the real one."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._held: list[str] = []
        # A flow that announces its outcome (`netbbs.net.notices`) instead
        # of writing it reaches the real session's queue through this.
        self.notice_session = session

    def __getattr__(self, name: str):
        attribute = getattr(self._session, name)
        if name == "read_editor_key":
            # Only there when the real session has it: flows probe for it
            # (`getattr(session, "read_editor_key", None)`) and fall back to
            # line input without it, as some session adapters need.
            async def read_editor_key(*args, **kwargs):
                await self._release()
                return await attribute(*args, **kwargs)

            return read_editor_key
        return attribute

    # `__getattr__` forwards reads only. A flow's activity trail (issue #762)
    # must land on the real session, where the monitor looks for it.
    @property
    def activity(self) -> tuple[str, ...]:
        return self._session.activity

    @activity.setter
    def activity(self, value: tuple[str, ...]) -> None:
        self._session.activity = value

    async def write_line(self, text: str = "") -> None:
        self._held.append(text)

    async def _release(self) -> None:
        held, self._held = self._held, []
        for text in held:
            await self._session.write_line(text)

    async def write(self, text: str) -> None:
        await self._release()
        await self._session.write(text)

    async def read_line(self, *args, **kwargs) -> str:
        await self._release()
        return await self._session.read_line(*args, **kwargs)

    async def read_key(self, *args, **kwargs) -> str:
        await self._release()
        return await self._session.read_key(*args, **kwargs)

    async def read_any_key(self, *args, **kwargs) -> str:
        await self._release()
        return await self._session.read_any_key(*args, **kwargs)

    # A Zmodem send talks to the terminal in raw bytes: "Starting Zmodem send
    # ..." has to be on screen before the first of them, not after the last.
    async def write_raw(self, data: bytes) -> None:
        await self._release()
        await self._session.write_raw(data)

    async def read_byte(self):
        await self._release()
        return await self._session.read_byte()

    def announce_rest(self, *, last_paragraph: bool = True) -> None:
        """Queue the flow's outcome: the last paragraph of what is still held,
        or all of it with `last_paragraph=False` -- for login's first-run
        questions, whose closing lines can be two paragraphs ("Node name set
        to ...", then "(Saved. ...)") and write no screen title (issue #923).

        Not everything still held is an outcome. `send_file_to_caller` writes a
        screen title (with its clear) and "Starting Zmodem send..." and only
        then touches the terminal in raw bytes -- so when it fails before the
        first of them (the blob is gone from disk), all three lines are still
        here. Announced, that title's clear erased the screen the console had
        just redrawn, leaving the error over a bare prompt. These flows open
        their outcome with a blank row (a leading CR LF on the line), so the
        outcome is what follows the last such break; and a line that clears
        the terminal is never one."""
        held, self._held = self._held, []
        start = 0
        for index, text in enumerate(held):
            if last_paragraph and (not text.strip() or _LEADING_BREAK.match(text)):
                start = index
        for text in held[start:]:
            if text.strip() and _CLEAR_SEQUENCE not in text:
                _announce_line(self._session, text)


async def _choice_prompt(session: Session) -> None:
    """A self-drawn console screen's prompt: any pending outcome, then `Choice:`."""
    for line in _take_notices(session):
        await session.write_line(line)
    await write_prompt(session, "Choice: ")


async def pick_item(session: Session, items, *, masthead="", **kwargs):
    """`picker.pick_item`, for the console. A picker is as likely as a menu
    to be the next screen drawn (lifting one chat restriction returns to the
    list of the rest); it shows whatever the console's last action announced
    above its own prompt, on the first draw, and it is gone once a key has
    redrawn the list."""
    # The picker's own answer to an empty list that nothing can change is to
    # print `empty_message` and return -- straight into the console menu whose
    # redraw erases it. Announced instead, it is on that menu when it draws.
    if not items and kwargs.get("refresh") is None and kwargs.get("on_create") is None:
        _announce(session, kwargs["empty_message"], color=MUTED_COLOR)
        return None
    return await _pick_item(session, items, masthead=masthead, **kwargs)


async def edit_resource_draft(session: Session, lane: DatabaseLane, **kwargs):
    """`resource_editor.edit_resource_draft`, shown whatever a field's prompt or
    the console's last action announced (see `_announce`)."""
    return await _edit_resource_draft(session, lane, notices=lambda: _take_notices(session), **kwargs)


async def show_detail(session: Session, *, message: str | None = None, **kwargs) -> tuple[str, int]:
    """`detail_view.show_detail`, with whatever the SysOp's last action announced
    shown ahead of the screen's own `message`."""
    lines = [*_take_notices(session), *([message] if message else [])]
    return await _show_detail_view(session, message="\r\n".join(lines) or None, **kwargs)


@dataclass(frozen=True)
class _Chrome:
    """The display preferences every titled screen resolves before it draws,
    fetched in one lane round trip instead of four."""

    redraw_in_place: bool
    unicode_style: bool
    collapsed: bool
    header_color: int | tuple[int, int, int]
    accent_color: int | tuple[int, int, int]


async def _load_chrome(lane: DatabaseLane, actor: User) -> _Chrome:
    def _load(db: Database) -> _Chrome:
        return _Chrome(
            redraw_in_place=redraw_in_place_enabled(db, actor),
            unicode_style=unicode_style_enabled(db, actor),
            collapsed=breadcrumb_collapsed_enabled(db, actor),
            header_color=effective_header_color_256(db),
            accent_color=effective_accent_color_256(db),
        )

    return await lane.run(_load)


def _detail_title(
    session: Session, chrome: _Chrome, title: str, *, own: bool = False, subtitle: str | None = None
) -> str:
    """A `screen_title` block for `show_detail`, which owns the clear itself
    (so that turning a page redraws in place too). The breadcrumb is where
    the session is; `own` for the frame of a screen that named itself
    (`netbbs.net.location`)."""
    return screen_title(
        title,
        breadcrumb=crumbs(session, own=own),
        subtitle=subtitle,
        width=session.terminal_width,
        clear=False,
        unicode_style=chrome.unicode_style, collapsed=chrome.collapsed,
        header_color=chrome.header_color,
        node_name_gradient=session.node_name_gradient,
    )


_BACK_ACTION = ("b", menu_key("B", "ack"))


async def _show_report(
    session: Session, lane: DatabaseLane, actor: User, title: str, *,
    sections: Sequence[Section], subtitle: str | None = None, own: bool = False,
    message: str | None = None, help_title: str, help_about: str,
) -> None:
    """Hold a finished action's result on screen until the SysOp leaves it.

    A result that was printed and returned from was wiped by the parent
    menu's clear-and-redraw before it could be read; this is the one way a
    SysOp screen reports an outcome it has nothing further to offer on.
    `own` as for `_detail_title`."""
    chrome = await _load_chrome(lane, actor)
    await show_detail(
        session,
        title=_detail_title(session, chrome, title, own=own, subtitle=subtitle),
        sections=sections, actions=[_BACK_ACTION], message=message,
        redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
        help_title=help_title, help_about=help_about,
    )


async def _write_sections(
    session: Session, sections: Sequence[Section], *, unicode_style: bool, joined: int = 0,
    places: dict[int, tuple[int, int, int]] | None = None,
) -> int:
    """Write a grouped label/value panel for a screen that keeps its own menu
    and key loop below it, a blank row between groups. Returns the rows
    written, so the caller can budget its menu against what is left of the
    terminal instead of pushing the panel's top off it.

    The first `joined` sections after the first continue it: no blank row
    before them. Give them no title, or they read as a group of their own.

    `places`, if given, receives where each one-to-a-row field's value was
    drawn, keyed by `id()` of the `Field`: its first row as a 0-based offset
    into the panel, the 0-based column it starts at, and its rows -- what an
    in-place edit needs (`editing_in_place`)."""
    rows = 0
    block_places: dict[int, tuple[int, tuple[int, int, int]]] | None = {} if places is not None else None
    blocks = render_sections(
        sections, width=session.terminal_width, unicode_style=unicode_style, places=block_places,
    )
    starts: list[int] = []
    for index, block in enumerate(blocks):
        if not 0 < index <= joined:
            # Led by a blank row, the first group included: what sits above a
            # panel is a title rule or a status line, and the panel is a new
            # paragraph either way.
            await session.write_line("")
            rows += 1
        starts.append(rows + (1 if block.heading is not None else 0))
        for line in block.lines:
            await session.write_line(line)
        rows += len(block.lines)
    if places is not None and block_places:
        for key, (block_index, (offset, column, value_rows)) in block_places.items():
            places[key] = (starts[block_index] + offset, column, value_rows)
    return rows


def _fitted_menu(options: list[MenuEntry], description_level: str, *, session: Session, used_rows: int) -> str:
    """The menu under a detail panel, as descriptive as the rows left allow.

    `_menu_row` alone budgets a menu against the whole terminal, as if
    nothing were drawn above it -- so on a screen that leads with a panel,
    the described form pushed the panel's own top row off a 24-row terminal.
    Measured here instead, the way `edit_resource_draft` measures its own:
    the described grid if it fits under what is already on screen, else the
    bar with its hotkeys aligned in columns if that fits, else the packed
    bar, the fewest rows."""
    def fits(menu: str) -> bool:
        return used_rows + _pending_notice_rows(session) + menu.count("\r\n") + 2 <= session.terminal_height

    if description_level != "off":
        described = menu_grid(
            [("", options)], width=session.terminal_width, height=session.terminal_height,
            description_level=description_level,
        )
        if fits(described):
            return described
    labels = [entry.label for entry in options]
    aligned = action_bar(labels, width=session.terminal_width, height=session.terminal_height)
    return aligned if fits(aligned) else action_bar(labels, width=session.terminal_width, height=None)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


# `netbbs.rendering.layout`'s floor below which `menu_grid` hides
# descriptions under entries.
_MIN_MENU_HEIGHT_FOR_DESCRIPTIONS = 15


def _degrade_description_level(
    *,
    panel: list[str],
    unicode_style: bool,
    description_level: str,
    entry_count: int,
    terminal_width: int,
    terminal_height: int,
) -> tuple[str, int, bool]:
    """Shrink a sub-console's description verbosity -- never its listed
    entries -- when a framed telemetry panel above the menu would
    otherwise push it past the terminal's visible height. Identical
    height-reservation math the three telemetry-panel sub-consoles
    (Users/Operations/Content) each need; factored out so a future fix
    to it lands in one place instead of three. Returns `(effective_
    description_level, available_menu_height, degraded)` -- `degraded`
    is True only when a non-"off" request was forced to "off" here, so
    `_menu_row` can still show the caller a "Descriptions hidden" notice
    even though this degrade happens before `menu_grid`'s own equivalent
    check ever runs (PR #197 review)."""
    panel_height = (len(panel) + 2) if (panel and unicode_style) else len(panel)
    reserved = panel_height + 4
    available_menu_height = max(1, terminal_height - reserved)

    effective_desc_level = description_level
    degraded = False
    if effective_desc_level != "off":
        columns = 2 if terminal_width >= 72 else 1
        rows_per_entry_row = -(-entry_count // columns)
        # `menu_grid` hides descriptions under its own height floor, so a
        # menu below it takes the one-line form too (review on #872).
        if rows_per_entry_row * 2 > available_menu_height or available_menu_height < _MIN_MENU_HEIGHT_FOR_DESCRIPTIONS:
            # Each description on its entry's own line before none at all
            # (issue #840): at 80x24 a first-time SysOp lost them exactly
            # where one-word entries needed them.
            if rows_per_entry_row <= available_menu_height:
                effective_desc_level = "inline"
            else:
                effective_desc_level = "off"
                degraded = True
    return effective_desc_level, available_menu_height, degraded


async def _write_wrapped_subtitle(
    session: Session, text: str, *, color: int | tuple[int, int, int] = MUTED_COLOR, bold: bool = False
) -> int:
    """Word-wrap a screen's plain-text descriptive subtitle to the real
    terminal width before coloring it, one wrapped physical line at a
    time -- coloring the whole sentence first and relying on the
    terminal's own soft-wrap (dogfood report) let it run past the right
    edge unpredictably on anything narrower than the text itself.

    `bold` (dogfood report: `[S]hutdown`/`[D]rain`'s own alert-colored
    warning lines had this exact same unwrapped-raw-`colored()` bug,
    just with `bold=True` on top) -- passed straight through to
    `colored()` for a caller that needs its wrapped lines bold too.

    Returns the rows written, for a screen that fits its menu under them."""
    lines = wrap_to_width(text, session.terminal_width) or [text]
    for wrapped in lines:
        await session.write_line(colored(wrapped, fg_color=color, bold=bold))
    return len(lines)


# Issue #728: the file-transfer grants of the node a console session runs on,
# for the screens deep inside it that offer an upload link. Registered by
# `admin_menu` rather than threaded through every banner menu's signature;
# weakly keyed, so a finished session takes its entry with it. Absent in the
# standalone `python -m netbbs.admin` console, which has no web listener.
_console_transfers: "weakref.WeakKeyDictionary[Session, Any]" = weakref.WeakKeyDictionary()


def _session_transfers(session: Session) -> Any:
    return _console_transfers.get(getattr(session, "notice_session", session))


def _upload_unavailable_reason(session: Session) -> str | None:
    """Why this console cannot receive a file at all, before anything is
    asked -- the same two routes a file area offers (issue #475)."""
    from netbbs.net.file_flow import supports_zmodem

    if supports_zmodem(session):
        return None
    transfers = _session_transfers(session)
    # A link needs somewhere to go: an absolute URL, or this session's own
    # browser page. Checked here, before a filename or a replacement is
    # asked for, rather than only when the link is minted.
    if transfers is not None and (
        transfers.base_url is not None or getattr(session, "offer_transfer", None) is not None
    ):
        return None
    return (
        "This connection cannot carry a Zmodem transfer, and this node has no browser "
        "transfer configured. Enable the web listener and its public_url, or connect with "
        "a Zmodem-capable terminal, to upload from here."
    )


@records_activity("Uploading")
async def _receive_sysop_upload(
    session: Session, lane: DatabaseLane, actor: User, target: SysOpUploadTarget, *, then: str,
) -> None:
    """Receive one file for `target` over Zmodem where the terminal can, or
    hand out a single-use browser link where it cannot -- the file area's
    own choice between the two (`netbbs.net.file_flow._handle_upload`)."""
    from netbbs.net import zmodem
    from netbbs.net.file_flow import offer_grant, supports_zmodem

    if supports_zmodem(session):
        await session.write_line(
            f"Your terminal should offer its Zmodem upload now; if it doesn't, start its Zmodem send (sz). "
            f"The file is saved as {target.destination.name}, whatever it is called on your side. "
            f"Ctrl-X five times cancels."
        )
        temp_path = await lane.run(new_incoming_temp_path)
        try:
            received = await zmodem.receive_file(session, max_bytes=target.max_bytes, dest_path=temp_path)
        except (zmodem.ZmodemError, NotImplementedError) as exc:
            _announce(session, f"Upload failed: {exc}", error=True)
            return
        try:
            size = await install_and_record(lane, actor, target, temp_path, sent_as=received.filename)
        except SysOpUploadError as exc:
            _announce(session, f"Upload failed: {exc}", error=True)
            return
        except OSError as exc:
            _announce(session, f"Could not write {target.destination}: {exc.strerror or exc}", error=True)
            return
        _announce(session, f"Uploaded {size} bytes to {target.destination}. {then}")
        return

    transfers = _session_transfers(session)
    if transfers is None:  # `_upload_unavailable_reason` is checked first; kept for safety
        _announce(session, _upload_unavailable_reason(session) or "No transfer route.", error=True)
        return
    if not await offer_grant(
        session, transfers,
        mint=lambda: transfers.issue_sysop_upload(user=actor, target=target),
        direction=UPLOAD, what=f"upload {target.label}",
    ):
        return
    _announce(
        session,
        f"Whatever you send is saved as {target.destination}, up to {target.max_bytes} bytes. {then}",
        color=MUTED_COLOR,
    )


def _remove_file_entry(status) -> list[MenuEntry]:
    """`[R]emove file` (issue #1119), offered only while a file is saved:
    with nothing to delete there is nothing to offer."""
    if not status.exists:
        return []
    return [MenuEntry(label=menu_key("R", "emove file"), brief="Delete the saved .ans file")]


def _format_bytes(size_bytes: int) -> str:
    """Human-readable byte count, binary (KiB/MiB/GiB) units -- a small
    local formatter rather than reaching into
    `netbbs.net.file_flow`'s own private `_format_size`, which exists
    for that module's file-listing display specifically."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size = float(size_bytes) / 1024
    for unit in ("KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"  # unreachable, satisfies type checkers
