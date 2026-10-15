"""Renaming accounts from the SysOp console (issue #1306, design doc §16 #1111).

Two ways in. A caller's request waits under Users -> Name requests, beside
the pending registrations, where the SysOp approves it or refuses it with a
reason the caller is shown at their next login. A SysOp can also rename an
account directly with `[C]hange username` on its detail screen.

A rename takes effect at once and ends the account's live sessions, each with
a line naming the new username (`netbbs.auth.renames.ended_session_notice`,
shown by the session's own account watcher). Its yes/no is the last keystroke
before that, and it names the one thing outside the node's reach: an RLogin
remote door that keys its players on the handle sees a new player.
"""

from __future__ import annotations

from netbbs.auth.renames import (
    UsernameChangeRequest,
    approve_username_request,
    list_open_username_requests,
    refuse_username_request,
    rename_user,
    sysop_rename_problem,
    username_history,
)
from netbbs.auth.users import User, UserManagementError, get_user_by_id
from netbbs.net.admin_fields import _EDIT_HINT, _read_seeded_line
from netbbs.net.admin_sessions import _request_live_access_recheck
from netbbs.net.admin_ui import (
    _BACK_ACTION,
    _announce_line,
    _detail_title,
    _load_chrome,
    pick_item,
    show_detail,
)
from netbbs.net.char_input import InputCancelled
from netbbs.net.confirm import prompt_yes_no
from netbbs.net.location import located
from netbbs.net.picker import ListColumn
from netbbs.net.resource_editor import write_field_prompt
from netbbs.net.session import Session
from netbbs.net.shutdown import NodeControls
from netbbs.rendering import (
    DATE_COLOR,
    ERROR_COLOR,
    MUTED_COLOR,
    SUCCESS_COLOR,
    VALUE_COLOR,
    colored,
    menu_key,
    sanitize_text,
)
from netbbs.rendering.detail import Field, Note, Section
from netbbs.storage.execution import DatabaseLane


def rename_question(old: str, new: str) -> str:
    """The yes/no before a rename, approved or direct."""
    return (
        f"Rename {old!r} to {new!r}? Their live sessions end, and they log in as {new!r} from now on. "
        f"{old!r} stays held for them. An RLogin remote door that keys players on the handle sees a new player."
    )


def _ended_sessions(node_controls: NodeControls | None, before: User) -> None:
    """Wake the watcher of every live session signed in under the old name: it
    finds the account renamed, tells the caller the new name and ends the
    session (§16 #1111 Decision 6). In the standalone CLI the running node's
    own poll does the same within its interval."""
    _request_live_access_recheck(node_controls, before)


def _done_line(before: User, after: User) -> str:
    return colored(
        f"{before.username!r} is now {after.username!r}. Their live sessions were ended.",
        fg_color=SUCCESS_COLOR,
    )


async def rename_account_directly(
    session: Session, lane: DatabaseLane, actor: User, target: User, node_controls: NodeControls | None,
) -> User:
    """`[C]hange username` on an account's detail screen: the new name, then
    the one yes/no. Returns the account as it now stands."""
    await write_field_prompt(
        session,
        colored(f"New username for {target.username!r} ({_EDIT_HINT}):", fg_color=MUTED_COLOR),
        hint=_EDIT_HINT,
    )
    try:
        new_name = (await _read_seeded_line(session, initial="")).strip()
    except InputCancelled:
        return target
    if not new_name:
        return target
    problem = await lane.run(sysop_rename_problem, target, new_name)
    if problem is not None:
        _announce_line(session, colored(f"Not renamed: {sanitize_text(problem)}", fg_color=MUTED_COLOR))
        return target
    if not await prompt_yes_no(session, rename_question(target.username, new_name), default=False):
        return target
    try:
        before, after = await lane.run(rename_user, target, new_name, renamed_by=actor)
    except UserManagementError as exc:
        _announce_line(session, colored(f"Not renamed: {sanitize_text(str(exc))}", fg_color=MUTED_COLOR))
        return await lane.run(get_user_by_id, target.id) or target
    _ended_sessions(node_controls, before)
    _announce_line(session, _done_line(before, after))
    return after


_REQUEST_COLUMNS = [
    ListColumn("asks for", 32, VALUE_COLOR),
    ListColumn("asked", 10, DATE_COLOR),
]


async def _decide_request(
    session: Session, lane: DatabaseLane, actor: User, request: UsernameChangeRequest,
    node_controls: NodeControls | None,
) -> None:
    """One request: who asks, for what, and the account's earlier names.
    `[A]pprove` and `[R]efuse`; `[B]ack` decides nothing."""
    chrome = await _load_chrome(lane, actor)
    account = await lane.run(get_user_by_id, request.user_id)
    if account is None:
        _announce_line(session, colored("That account no longer exists.", fg_color=MUTED_COLOR))
        return
    former = await lane.run(username_history, account)
    fields: list[Field | Note] = [
        Field("Account", sanitize_text(account.username)),
        Field("Asks for", sanitize_text(request.requested_username)),
        Field("Asked", request.requested_at[:10], color=DATE_COLOR),
        Field("Level", str(account.user_level)),
    ]
    if former:
        fields.append(Field("Formerly", ", ".join(sanitize_text(change.old_username) for change in former)))
    message: str | None = None
    while True:
        choice, _page = await show_detail(
            session,
            title=_detail_title(
                session, chrome, "Name request", own=True,
                subtitle="A caller asks for a new username.",
            ),
            sections=[
                Section("Request", fields),
                Section("If you approve", [Note(
                    "The name was checked as a signup is when it was asked for, and is checked again "
                    "now. The old name stays held for this account, so mail to it still arrives; "
                    "posts keep the name they were written under."
                )]),
            ],
            actions=[("a", menu_key("A", "pprove")), ("r", menu_key("R", "efuse")), _BACK_ACTION],
            message=message,
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            help_title="Name request help",
            help_about=(
                "Approve renames the account at once and ends its live sessions. Refuse asks for a "
                "reason, which the caller sees at their next login."
            ),
        )
        message = None
        if choice == "b":
            return
        if choice == "a":
            if not await prompt_yes_no(
                session, rename_question(account.username, request.requested_username), default=False
            ):
                continue
            try:
                before, after = await lane.run(approve_username_request, request.id, approved_by=actor)
            except UserManagementError as exc:
                message = colored(f"Not renamed: {sanitize_text(str(exc))}", fg_color=ERROR_COLOR)
                continue
            _ended_sessions(node_controls, before)
            _announce_line(session, _done_line(before, after))
            return
        if choice == "r":
            await write_field_prompt(
                session,
                colored("Why? The caller sees this at their next login (Esc cancels):", fg_color=MUTED_COLOR),
                hint=_EDIT_HINT,
            )
            try:
                reason = (await _read_seeded_line(session, initial="")).strip()
            except InputCancelled:
                continue
            if not reason:
                message = colored("Not refused: a refusal needs a reason.", fg_color=MUTED_COLOR)
                continue
            try:
                await lane.run(refuse_username_request, request.id, refused_by=actor, reason=reason)
            except UserManagementError as exc:
                message = colored(f"Not refused: {sanitize_text(str(exc))}", fg_color=ERROR_COLOR)
                continue
            _announce_line(
                session,
                colored(
                    f"Refused {sanitize_text(account.username)!r}'s request; they are told at their next login.",
                    fg_color=SUCCESS_COLOR,
                ),
            )
            return


@located("Name requests")
async def username_requests_screen(
    session: Session, lane: DatabaseLane, actor: User, node_controls: NodeControls | None,
) -> None:
    """Every open request for a new username, oldest first (§16 #1111
    Decision 1). Picking one shows it, with Approve and Refuse."""
    chrome = await _load_chrome(lane, actor)
    start: int | None = None
    while True:
        requests = await lane.run(list_open_username_requests)
        picked = await pick_item(
            session, requests,
            name_of=lambda request: request.username,
            stable_id_of=lambda request: request.id,
            columns=_REQUEST_COLUMNS,
            column_values_of=lambda request: [request.requested_username, request.requested_at[:10]],
            description_of=lambda request: (
                f"asks for {request.requested_username}, {request.requested_at[:10]}"
            ),
            title="Name requests", own=True,
            empty_message="No caller is asking for a new username.",
            start_stable_id=start,
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            collapsed=chrome.collapsed, accent_color=chrome.accent_color, header_color=chrome.header_color,
        )
        if picked is None:
            return
        start = picked.id
        await _decide_request(session, lane, actor, picked, node_controls)
