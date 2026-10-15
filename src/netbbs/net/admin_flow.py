"""
Shared SysOp admin menu (design doc -- SysOp foundation).

The single implementation of every user-management action, reachable
two ways: a gated menu option inside an authenticated BBS session
(`netbbs.net.login_flow`), and the standalone local CLI tool
(`netbbs.admin.__main__`, `python -m netbbs.admin`) -- see that
module's docstring for why the two entry points share this rather than
each carrying their own copy. Every action here is audit-logged
against whichever `User` the caller supplies, regardless of which
entry point that came from.

Follows the submenu shape already established by
`netbbs.net.profile_flow._edit_profile`: a redraw-on-real-change-only
draw function, a bell-only-on-invalid-key dispatch loop (design doc),
and `netbbs.net.picker.pick_item` for target selection.

Migrated onto the two-lane database execution model (design doc,
issue #57) -- every screen/menu
function here takes `lane: DatabaseLane` instead of `db: Database`,
and every direct domain-function call goes through `await lane.run(...)`.
Almost entirely mechanical (no
synchronous-callback contracts here the way the Tab completer in
chat_flow.py has) --
`pick_item`'s own `name_of`/`description_of` callbacks in this file only
ever read attributes already present on the objects handed to
`pick_item`, never a fresh DB call, except one: `_who_screen`'s
`description_of` cannot call `format_for_display(entry.connected_at,
db)` directly inside its lambda, since a synchronous callback cannot
dispatch through a lane -- it instead uses the same fix as elsewhere
(`resolve_display_preferences`, fetched once via
`lane.run` before the picker, then passed as `override_format`/
`override_timezone` into a plain synchronous `format_for_display`
call). `_community_label` stays `db`-first, dispatched *through* the
lane like `netbbs.net.file_flow`'s `_uploader_display_name` before it
-- it's a callee, never a caller, of the lane.

Both entry points that share this module now need a real
`DatabaseLane`: the in-BBS `[S]ysOp` menu option
(`netbbs.net.login_flow`, same `lane is None` degrade-gracefully guard
as the mail/files/chat branches before it) and the standalone
`python -m netbbs.admin` CLI (`netbbs.admin.__main__`), which
constructs its own `DatabaseLane` around its own `Database` handle --
there is no live-session/CLI distinction for admin functionality
itself once inside `admin_menu` (that's `node_controls`' job,
established independently).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import shlex
import sqlite3
import sys
import threading
from pathlib import Path
import dataclasses
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Sequence

import nacl.signing

from netbbs.rendering.menu import continue_prompt
from netbbs.access_map import (
    AccountChange,
    Gate,
    GateKind,
    GATE_WORDS,
    LadderStep,
    LevelSource,
    LevelContext,
    account_level_change,
    gate_source,
    ladder_summary,
    level_change,
    level_context,
    level_ladder,
    list_gates,
)
from netbbs.attestation import (
    AttestationError,
    ProfileFieldError,
    change_birthdate,
    change_display_name,
    get_attestation,
    get_birthdate,
    get_display_name,
    revoke_attestation,
)
from netbbs.auth.users import (
    CO_SYSOP_PRESET,
    STAFF_PERMISSION_LABELS,
    SYSOP_LEVEL,
    AuthError,
    StaffPermission,
    User,
    UserManagementError,
    UsernameRetiredError,
    approve_pending_user,
    decline_pending_user,
    count_sysops,
    create_user,
    current_account,
    delete_user,
    deletion_retires_username,
    get_user_by_id,
    has_password,
    list_users,
    describe_staff_permissions,
    check_user_level_change,
    is_usable_sysop,
    set_automatic_promotion,
    set_can_verify_identity,
    set_staff_permissions,
    set_user_disabled,
    set_user_level,
)
from netbbs.backup import (
    BackupError,
    running_node_pid,
    default_backup_destination,
    get_last_backup_summary,
)
from netbbs.backup_schedule import (
    BackupScheduleError,
    schedule_status,
    check_destination as check_backup_destination,
)
from netbbs.boards.boards import Board, list_boards
from netbbs.boards.categories import Category
from netbbs.boards.posts import count_listed_posts, count_pending_posts
from netbbs.chat.channels import Channel, list_channels
from netbbs.chat.hub import ChatHub
from netbbs.communities import get_community, list_communities
from netbbs.config import (
    RegistrationMode,
    get_max_upload_bytes,
    get_registration_mode,
    set_registration_mode,
)
from netbbs.level_names import LevelNameError, get_level_names, level_label, parse_level, set_level_name
from netbbs.promotion import (
    PromotionRule,
    PromotionRuleError,
    count_qualifying,
    get_promotion_rules,
    kept_from_rules,
    save_promotion_rules,
)
from netbbs.doors import (
    Door,
    DoorError,
    create_door,
    custom_doors_dir,
    delete_door,
    get_door,
    get_door_by_name,
    list_doors,
    update_door,
)
from netbbs.doors.bundled import available_bundled_doors
from netbbs.doors.profiles import is_remote
from netbbs.doors.outbound import (
    OutboundError,
    allow_channel,
    allow_target,
    channel_not_allowable,
    channel_targets,
    disable_outbound,
    enable_outbound,
    outbound_config,
    revoke_channel,
    revoke_target,
    set_chat_ceiling,
    set_rate_ceiling,
)
from netbbs.doors.outbound import targets as outbound_targets
from netbbs.files.areas import list_file_areas
from netbbs.files.categories import FileAreaCategory
from netbbs.files.entries import count_listed_files, count_pending_files
from netbbs.identity.keys import IdentityError, parse_verify_key
from netbbs.link.boards import LinkContext
from netbbs.link.channels import is_channel_linked
from netbbs.link.diagnostics import list_diagnostic_log_entries
from netbbs.link.enforcement import node_transport_state
from netbbs.link.carry import (
    OFFERED,
    count_carried_to_review,
    count_carry_decisions,
    hide_carried_resource,
)
from netbbs.link.trust import TrustState
from netbbs.link.work_items import list_work_items
from netbbs.moderation.blocklist import BlocklistError, block_user, is_blocked, unblock_user
from netbbs.moderation.log import list_actions_for_target_user, record_action
from netbbs.net.policy_settings import SETTINGS as POLICY_SETTINGS
from netbbs.mrc.bridge import MrcBridge
from netbbs.staff import (
    MAX_AWAY_MESSAGE_CHARS,
    away_notice,
    away_problem,
    count_moderation_items,
    count_pending_accounts,
    describe_away,
    end_away,
    has_moderation_scope,
    list_staff,
    node_today,
    set_away,
)
from netbbs.moderation.roles import describe_grant, get_grant, list_grants_for_user
from netbbs.net.char_input import (
    HELP_KEY,
    InputCancelled,
    REDRAW_KEY,
    REFRESH_KEY,
    EditorKey,
    EditorKeyKind,
    help_key_label,
    reject_unhandled_key,
)
from netbbs.net.confirm import prompt_yes_no
from netbbs.config import get_max_upload_bytes
from netbbs.sysop_uploads import (
    DOOR_FILE as SYSOP_UPLOAD_DOOR_FILE,
    SysOpUploadTarget,
    destination_problem,
    door_filename_error,
)
from netbbs.net.help_overlay import show_help, show_menu_help
from netbbs.net.location import crumbs, entered, located
from netbbs.net.picker import ListColumn, pick_item as _pick_item
from netbbs.net.resource_editor import (
    editing_in_place,
    inline_field,
    write_field_prompt,
    write_field_message,
    FieldSpec,
    bool_field,
    bool_step,
    DetailAction,
    DetailMode,
    DetailState,
    text_field,
)
from netbbs.net.session import Session, write_prompt
from netbbs.net.shutdown import NodeControls
from netbbs.net.password_screen import manage_password_screen
from netbbs.net.profile_flow import _edit_profile
from netbbs.net.ssh_key_screen import manage_ssh_keys_screen
from netbbs.auth.signup_answers import (
    MAX_REGISTRATION_QUESTION_LENGTH,
    get_registration_question,
    load_signup_answer,
    set_registration_question,
)
from netbbs.net.menu_description_preference import menu_description_level
from netbbs.net.redraw_preference import (
    start_new_account_redrawing_in_place,
    redraw_in_place_enabled,
)
from netbbs.net.breadcrumb_preference import breadcrumb_collapsed_enabled
from netbbs.net.node_theme import effective_accent_color_256, effective_header_color_256
from netbbs.net.unicode_style_preference import unicode_style_enabled
from netbbs.selfupdate import get_display_check_summary
from netbbs.permissions.levels import meets_level
from netbbs.rendering import (
    ALERT_COLOR,
    AUTHOR_COLOR,
    DATE_COLOR,
    ERROR_COLOR,
    HEADER_COLOR,
    LABEL_COLOR,
    MENU_KEY_COLOR,
    METADATA_COLOR,
    MUTED_COLOR,
    SUCCESS_COLOR,
    VALUE_COLOR,
    WARNING_COLOR,
    MenuEntry,
    action_bar,
    colored,
    counts_row,
    cut_to_width,
    highlight_hotkeys,
    highlight_result,
    menu_grid,
    menu_key,
    reflow,
    reject_keystroke,
    sanitize_text,
    screen_title,
    status_badge,
    telemetry_gauge,
    truncate,
    visible_width,
)
from netbbs.rendering.detail import Field, Note, Section, Table
from netbbs.rendering.reflow import wrap_terminal_text
from netbbs.guest import is_guest_account, set_guest_user, set_pre_login_notice
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import format_for_display, resolve_display_preferences
from netbbs.net.admin_ui import (
    _BACK_ACTION,
    _announce,
    _announce_line,
    _choice_prompt,
    _confirmation_refused,
    _console_transfers,
    _degrade_description_level,
    _detail_title,
    _fitted_menu,
    _load_chrome,
    _menu_row,
    _pending_notice_rows,
    _receive_sysop_upload,
    _show_report,
    _upload_unavailable_reason,
    _wrap_counts_panel,
    _wrap_panel_sentence,
    _write_panel,
    _write_sections,
    _yes_no,
    edit_resource_draft,
    pick_item,
    show_detail,
)
from netbbs.net.admin_fields import (
    _EDIT_HINT,
    _int_field,
    _plain_level_label,
    _read_seeded_line,
    _resolve_redraw_preference,
)
from netbbs.net.admin_resources import _sections_text
from netbbs.net.admin_maintenance import (
    _audit_log_screen,
    _backup_status_screen,
    _diagnostic_log_screen,
    _diagnostic_log_tail_screen,
    _node_log_screen,
    _prune_drafts_screen,
    _repair_carried_posts_screen,
    _search_indexes_screen,
)
from netbbs.net.admin_trust import _retired_usernames_screen
from netbbs.net.admin_link import _link_status_screen, _mail_tools_screen, _outbox_screen
from netbbs.net.admin_moderation import _pending_review_screen
from netbbs.net.admin_system import _managed_dns_status_screen, _system_menu
from netbbs.net.admin_sessions import (
    _node_menu,
    _request_live_access_recheck,
    _revoke_live_sessions,
)
from netbbs.net.admin_boards import (
    _board_detail_screen,
    _board_menu,
    _category_menu,
    _community_field,
    _community_menu,
    _grant_moderator_screen,
    _revoke_moderator_screen,
)
from netbbs.net.admin_files import _area_detail_screen, _area_menu
from netbbs.net.admin_chat import _channel_detail_screen, _channel_menu

# Design doc -- node management, Thiesi's own request: an operator
# watching this node's own stdout/journal should see a shutdown/drain
# being scheduled (or maintenance mode toggled) as it happens, not only
# via the DB-backed moderation log (`record_action`, queried separately).
_logger = logging.getLogger(__name__)


def _link_health_snapshot(db: Database, link_context: LinkContext | None) -> dict[str, int]:
    """The dead-letter count and recent-diagnostic error/warning tally
    the SysOp landing page and the Operations sub-console both need --
    factored out so the two screens can't silently drift into
    disagreeing about what "recent" means by each re-deriving it."""
    dead_letters = len(list_work_items(db, status="dead_lettered")) if link_context is not None else 0
    recent_diagnostics = list_diagnostic_log_entries(db, limit=20) if link_context is not None else []
    return {
        "dead_letters": dead_letters,
        "recent_errors": sum(entry.level == "ERROR" for entry in recent_diagnostics),
        "recent_warnings": sum(entry.level == "WARNING" for entry in recent_diagnostics),
        # Issue #681: what Link carried in on its own and the SysOp has not
        # looked at, and what waits at the carry cap for their decision.
        # Read from the database, so shown whether or not Link runs now
        # (Codex review on #800): a node that stopped Link still has them.
        "carried_to_review": count_carried_to_review(db),
        "carry_offers": count_carry_decisions(db, OFFERED),
        # Issue #844: a peer on probation here exchanges nothing, which the
        # peer count alone made look healthy.
        "peers_on_probation": _count_peers_on_probation(db) if link_context is not None else 0,
    }


def _count_peers_on_probation(db: Database) -> int:
    """Verified peers on probation here, which exchange nothing with this
    node yet (issue #844). A quarantine or block is the SysOp's own doing
    and shows on Link status as such."""
    fingerprints = [row["fingerprint"] for row in db.connection.execute("SELECT fingerprint FROM link_peers")]
    return sum(node_transport_state(db, fingerprint) == TrustState.PROBATIONARY for fingerprint in fingerprints)


def _link_count_pairs(node, state: dict) -> list[tuple[str, int]]:
    """The Link counts every dashboard shows, with the peers nothing is
    exchanged with yet when there are any (issue #844)."""
    pairs = [("Peers", len(node.peers))]
    if state.get("peers_on_probation"):
        pairs.append(("On probation", state["peers_on_probation"]))
    return [*pairs, ("Relays", len(node.relays_serving_me)), ("Dead letters", state["dead_letters"])]


async def _with_fresh_carry_counts(lane: DatabaseLane, state: dict[str, object]) -> dict[str, object]:
    """`state` with its carry attention counts read again: a sub-console
    that leads to Link status may have decided offers there (Codex review
    on #800). Two counts, not the whole dashboard reload."""
    fresh = await lane.run(lambda db: {
        "carried_to_review": count_carried_to_review(db),
        "carry_offers": count_carry_decisions(db, OFFERED),
    })
    return {**state, **fresh}


def _carry_attention_pairs(state: dict[str, object]) -> list[tuple[str, int]]:
    """The dashboard's Link attention counts (issue #681), only when there
    is something to attend to: a node with nothing new draws as before."""
    pairs = []
    if state.get("carried_to_review"):
        pairs.append(("Newly carried", state["carried_to_review"]))
    if state.get("carry_offers"):
        pairs.append(("Offered", state["carry_offers"]))
    return pairs


def _offline_console_note(node_running: bool) -> str:
    """What the health panel says when this console has no live node behind
    it -- `python -m netbbs.admin`, run on the node's machine (issue #834).
    "Standalone mode" read as "because I declined Link"; the reason is that
    this process is not the node, and whether the node is up at all."""
    if node_running:
        return (
            "This console runs outside the node. For live controls, log in to "
            "the node and open the SysOp menu there."
        )
    return "The node isn't running, so there are no live controls. Start the node to get them."


def _get_display_backup_summary(db: Database) -> tuple[str | None, str | None]:
    """Return backup state with its timestamp ready for human display."""
    backup_at, backup_path = get_last_backup_summary(db)
    return (
        format_for_display(backup_at, db) if backup_at else None,
        backup_path,
    )


def _get_display_next_backup(db: Database) -> str | None:
    """When the backup schedule runs next, for display; `None` while it is off
    (issue #727)."""
    status = schedule_status(db)
    if not status.schedule.enabled:
        return None
    if status.overdue:
        return "due now"
    return format_for_display(status.next_run.isoformat(), db) if status.next_run else None


# -- outcomes carried into the next redraw ------------------------------------
#
# The mechanism lives in `netbbs.net.notices` (issue #680 shared it with the
# caller-facing screens); these names are the console's own spelling of it.
# An action does not *write* its outcome; it `_announce`s it, and whichever
# console screen is drawn next shows it directly above its prompt --
# `_choice_prompt` for a screen that draws itself, `show_detail` for a paged
# one.


def _can_verify_label(target: User) -> str:
    """The account's "Can verify identity" row. A SysOp verifies by level
    (issue #1103), so the row says so rather than a bare "no" that reads as
    if they could not."""
    if meets_level(target, SYSOP_LEVEL):
        return "yes, as SysOp (age/name attestation)"
    return f"{_yes_no(target.can_verify_identity)} (age/name attestation)"


def _co_sysop_question(username: str) -> str:
    """The Co-SysOp preset's yes/no question. Verifying identity is not part
    of the preset (maintainer decision, issue #1103): vouching for a
    caller's age or name travels to other nodes and opens adult areas, so a
    SysOp grants it to a helper on its own -- with `[V]erify identity` on
    the staff screen this question is asked from (issue #1115), not with a
    key on the account screen the SysOp is not looking at. Not a second
    question after this one: AGENTS.md allows no dialog chains."""
    return (
        f"Make {username!r} a Co-SysOp -- approve accounts, manage accounts (disable, "
        "password reset, display names and birthdates, levels up to 254) and moderate "
        "everything? They can't act on "
        "SysOps or other staff, or reach Settings, Link, Node, DNS or backups, and members "
        "see them under Operators. Verifying identity is separate: [V]erify identity "
        "on this screen."
    )


def _verify_identity_question(username: str, giving: bool) -> str:
    """`[V]erify identity`'s yes/no on the staff screen (issue #1115)."""
    if giving:
        return (
            f"Let {username!r} verify identity? They could then vouch for callers' "
            "ages and real names, which other nodes may trust."
        )
    return f"Stop {username!r} from verifying identity?"


@located("SysOp")
async def admin_menu(
    session: Session,
    lane: DatabaseLane,
    user: User,
    *,
    node_controls: NodeControls | None = None,
    link_context: LinkContext | None = None,
) -> None:
    """
    Top-level SysOp admin menu. Callers are responsible for their own
    level gating before entering this -- it performs no permission
    check of its own, matching `pick_item`'s "presentation and
    selection only" precedent.

    `node_controls` (design doc), if given, unlocks the `[N]ode` quick
    action and its entry in `[O]perations` (list/disconnect sessions,
    trigger shutdown), and lets `[F]ull backups` use the running node's
    configured identity path -- present when called
    from within a live session (`netbbs.net.login_flow`), absent
    (`None`) when called from the standalone `python -m netbbs.admin`
    CLI, which has no access to a running node's live in-memory state
    at all (confirmed design decision, not an oversight -- see that
    module's docstring).

    `link_context` (design doc), if given, unlocks the
    `[L]ink this board` command inside the board-management screens --
    same presence/absence reasoning as `node_controls`: absent for the
    standalone CLI and for any node with Link disabled.

    The landing view is an at-a-glance operations dashboard. Navigation
    separates users, content, running-node operations, and durable settings;
    context-sensitive quick actions avoid forcing an operator through that
    hierarchy when responding to a visible condition. Historical `[M]anage`
    and `[S]ystem` operation keys remain accepted as compatibility aliases but
    are no longer advertised in the reorganized console.
    """
    transfers = node_controls.transfers if node_controls is not None else None
    owner = getattr(session, "notice_session", session)
    if transfers is not None:
        _console_transfers[owner] = transfers
    else:
        _console_transfers.pop(owner, None)
    dashboard_state = await _draw_admin_menu(
        session, lane, user, node_controls=node_controls, link_context=link_context
    )
    while True:
        choice = (await session.read_key()).lower()

        if await _operator_lost_sysop(session, lane, user, node_controls):
            await session.write_line(
                colored("\r\nYour account no longer has SysOp access.", fg_color=ALERT_COLOR)
            )
            return

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "r":
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == "u":
            await session.write_line("")
            await _users_menu(session, lane, user, node_controls=node_controls, link_context=link_context)
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice in {"c", "m"}:
            await session.write_line("")
            await _content_menu(
                session, lane, user, link_context=link_context,
                mrc_bridge=node_controls.mrc_bridge if node_controls is not None else None,
                chat_hub=node_controls.chat_hub if node_controls is not None else None,
                door_services=node_controls.door_services if node_controls is not None else None,
                transfers=node_controls.transfers if node_controls is not None else None,
                backup_identity_dir=node_controls.backup_identity_dir if node_controls is not None else None,
            )
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == "o":
            await session.write_line("")
            await _operations_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
            # Link status is reachable from here too: its offer decisions
            # show, without reloading the rest (Codex review on #800).
            dashboard_state = await _with_fresh_carry_counts(lane, dashboard_state)
            await _draw_admin_menu(session, lane, user, node_controls=node_controls,
                                   link_context=link_context, state=dashboard_state)
        elif choice == "s":
            await session.write_line("")
            await _system_menu(session, lane, user, node_controls=node_controls, link_context=link_context)
            dashboard_state = await _with_fresh_carry_counts(lane, dashboard_state)
            await _draw_admin_menu(session, lane, user, node_controls=node_controls,
                                   link_context=link_context, state=dashboard_state)
        elif choice == "n" and node_controls is not None:
            await session.write_line("")
            await _node_menu(session, lane, user, node_controls)
            await _draw_admin_menu(session, lane, user, node_controls=node_controls,
                                   link_context=link_context, state=dashboard_state)
        elif choice == "l" and link_context is not None:
            await session.write_line("")
            await _link_status_screen(session, lane, user, link_context=link_context, node_controls=node_controls)
            # Reloaded: Link status is where offers are accepted or
            # excluded (Codex review on #800).
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == "q" and link_context is not None:
            await session.write_line("")
            await _outbox_screen(session, lane, user)
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == "f":
            await session.write_line("")
            await _backup_status_screen(session, lane, user, node_controls=node_controls)
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == "d":
            await session.write_line("")
            await _managed_dns_status_screen(session, lane, user)
            await _draw_admin_menu(session, lane, user, node_controls=node_controls,
                                   link_context=link_context, state=dashboard_state)
        elif choice == "t":
            await session.write_line("")
            await _away_screen(session, lane, user)
            dashboard_state = await _draw_admin_menu(
                session, lane, user, node_controls=node_controls, link_context=link_context
            )
        elif choice == HELP_KEY:
            console, quick = _admin_menu_entries(node_controls, link_context)
            await show_menu_help(
                session, "SysOp console help", [*console, *quick],
                about="The landing page of the SysOp console: the node's health and what waits for "
                      "you above, every area of the console below. Quick keys jump straight to a "
                      "screen that also sits deeper in the menus.",
                header_color=dashboard_state["header_color"], unicode_style=dashboard_state["unicode_style"],
            )
            await _draw_admin_menu(session, lane, user, node_controls=node_controls,
                                   link_context=link_context, state=dashboard_state)
        else:
            await session.write(reject_unhandled_key(choice))


@located("Staff")
async def staff_menu(
    session: Session,
    lane: DatabaseLane,
    user: User,
    *,
    node_controls: NodeControls | None = None,
    link_context: LinkContext | None = None,
) -> None:
    """
    The Staff console (design doc §5.6, issue #836): the reduced console a
    staff member reaches from `[S]taff` on the main menu. Its landing view
    counts what waits for them, and it offers only the screens their
    permissions reach -- the accounts waiting for approval, the account
    list, the moderation queue -- which are the SysOp console's own screens
    (`_pick_and_edit_user`, `_pending_review_screen`), narrowed by the
    actor rather than copied. Nothing here reaches Settings, Link, Node,
    DNS or backups.

    Callers gate entry on `netbbs.staff.is_staff`. The console re-checks at
    every key, like the SysOp's (issue #659): once the operator holds no
    staff permission, it closes.
    """
    await _draw_staff_menu(session, lane, user)
    while True:
        choice = (await session.read_key()).lower()
        fresh = await lane.run(current_account, user)
        if fresh is None or not fresh.staff_permissions or fresh.user_level >= SYSOP_LEVEL:
            await session.write_line(colored("\r\nYour account no longer has staff access.", fg_color=ALERT_COLOR))
            return
        user = fresh
        if choice == "b":
            await session.write_line("")
            return
        if choice == "r":
            pass
        elif choice == "a" and user.has_staff(StaffPermission.APPROVE_ACCOUNTS):
            await session.write_line("")
            await _pick_and_edit_user(
                session, lane, user, node_controls, title="Waiting for approval", pending_only=True
            )
        elif choice == "u" and user.has_staff(StaffPermission.MANAGE_ACCOUNTS):
            await session.write_line("")
            await _pick_and_edit_user(session, lane, user, node_controls, title="Accounts")
        elif choice == "t":
            await session.write_line("")
            await _away_screen(session, lane, user)
        elif choice == "m" and await lane.run(has_moderation_scope, user):
            await session.write_line("")
            await _pending_review_screen(
                session, lane, user, link_context=link_context,
                transfers=node_controls.transfers if node_controls is not None else None,
            )
        elif choice == HELP_KEY:
            moderates, header_color, unicode_style = await lane.run(lambda db: (
                has_moderation_scope(db, user), effective_header_color_256(db), unicode_style_enabled(db, user),
            ))
            await show_menu_help(
                session, "Staff console help", _staff_menu_entries(user, moderates=moderates),
                about="Your staff work on this node: only the screens your staff permissions reach "
                      "are listed. The counts above show what is waiting for you.",
                header_color=header_color, unicode_style=unicode_style,
            )
        else:
            await session.write(reject_unhandled_key(choice))
            continue
        await _draw_staff_menu(session, lane, user)


async def _draw_staff_menu(session: Session, lane: DatabaseLane, user: User) -> None:
    def _load(db: Database) -> dict[str, object]:
        return {
            "pending_accounts": count_pending_accounts(db),
            "moderates": has_moderation_scope(db, user),
            "held": count_moderation_items(db, user),
            "description_level": menu_description_level(db, user),
            "redraw_in_place": redraw_in_place_enabled(db, user),
            "unicode_style": unicode_style_enabled(db, user),
            "collapsed": breadcrumb_collapsed_enabled(db, user),
            "header_color": effective_header_color_256(db),
            **_away_state(db, user),
        }

    state = await lane.run(_load)
    unicode_style = state["unicode_style"]
    await session.write_line(
        "\r\n" + screen_title(
            "Staff console",
            breadcrumb=crumbs(session, own=True),
            subtitle=f"Your staff permissions: {describe_staff_permissions(user.staff_permissions)}.",
            width=session.terminal_width,
            clear=state["redraw_in_place"],
            unicode_style=unicode_style, collapsed=state["collapsed"],
            header_color=state["header_color"], node_name_gradient=session.node_name_gradient,
        )
    )
    counts: list[Field] = []
    if user.has_staff(StaffPermission.APPROVE_ACCOUNTS):
        waiting = state["pending_accounts"]
        counts.append(Field(
            "Waiting for approval", str(waiting), color=WARNING_COLOR if waiting else MUTED_COLOR,
        ))
    if state["moderates"]:
        held = state["held"]
        counts.append(Field("Held for moderation", str(held), color=WARNING_COLOR if held else MUTED_COLOR))
    panel_rows = await _write_sections(session, [Section(None, counts)], unicode_style=unicode_style) if counts else 0
    for line in _away_panel_lines(state, width=min(session.terminal_width, 78) - 4, unicode_style=unicode_style):
        await session.write_line(line)
        panel_rows += 1
    options = _staff_menu_entries(user, moderates=bool(state["moderates"]))
    await session.write_line(
        "\r\n" + _fitted_menu(options, state["description_level"], session=session, used_rows=panel_rows + 5)
    )
    await _choice_prompt(session)


def _staff_menu_entries(user: User, *, moderates: bool) -> list[MenuEntry]:
    """The Staff console's menu, shared by its draw and its help."""
    options: list[MenuEntry] = []
    if user.has_staff(StaffPermission.APPROVE_ACCOUNTS):
        options.append(MenuEntry(label=menu_key("A", "ccounts waiting"), brief="Approve or decline signups"))
    if user.has_staff(StaffPermission.MANAGE_ACCOUNTS):
        options.append(MenuEntry(label=menu_key("U", "sers"), brief="Levels, disabling, passwords"))
    if moderates:
        options.append(MenuEntry(label=menu_key("M", "oderation"), brief="Held posts and uploads"))
    options.append(MenuEntry(label=menu_key("T", "ime away"), brief="Tell members you're away"))
    options.append(MenuEntry(label=menu_key("R", "efresh"), brief="Redraw with current numbers"))
    options.append(MenuEntry(label=menu_key("B", "ack"), brief="Return to the main menu"))
    return options


def _date_of(db: Database, stamp: str | None) -> str | None:
    """A stored timestamp as the node-local date alone (design doc §5.6:
    Operators shows the day of a last session, not the time)."""
    if not stamp:
        return None
    _fmt, tz_name = resolve_display_preferences(db)
    try:
        return format_for_display(stamp, override_format="%Y-%m-%d", override_timezone=tz_name)
    except ValueError:
        return None


def _away_state(db: Database, user: User) -> dict[str, object]:
    notice = away_notice(db, user)
    return {"away": notice, "away_since": _date_of(db, notice.since) if notice is not None else None}


def _away_panel_lines(state: dict[str, object], *, width: int, unicode_style: bool) -> list[str]:
    notice = state.get("away")
    if notice is None:
        return []
    text = f"You are marked {describe_away(notice, state['away_since'])}. [T]ime away ends it."
    return [
        colored(f"  {line}", fg_color=WARNING_COLOR)
        for line in _wrap_panel_sentence(sanitize_text(text), prefix="  ", width=width, unicode_style=unicode_style)
    ]


def _parse_return_date(raw: str) -> datetime.date | None:
    """`YYYY-MM-DD`, the one spelling that reads the same everywhere."""
    return datetime.date.fromisoformat(raw)


async def _away_screen(session: Session, lane: DatabaseLane, user: User) -> None:
    """
    Mark yourself away, or end it (design doc §5.6, issue #836): one short
    line of plain text and an optional return date. It shows on the Staff
    list beside your name and, when every approver is away, to pending
    callers. Being away changes nobody's permissions.
    """
    while True:
        state = await lane.run(lambda db: _away_state(db, user))
        notice = state["away"]
        await session.write_line("")
        await session.write_line(colored("Away notice", fg_color=LABEL_COLOR, bold=True))
        if notice is None:
            await session.write_line(colored("  You are not marked away.", fg_color=MUTED_COLOR))
        else:
            await session.write_line("  " + sanitize_text(describe_away(notice, state["away_since"])))
        options = [menu_key("S", "et" if notice is None else "et a new notice")]
        if notice is not None:
            options.append(menu_key("E", "nd it"))
        options.append(menu_key("B", "ack"))
        await write_prompt(session, f"\r\n{action_bar(options, width=session.terminal_width, height=session.terminal_height)}: ")
        choice = (await session.read_key()).lower()
        await session.write_line("")
        if choice == "b":
            return
        if choice == "e" and notice is not None:
            await lane.run(end_away, user)
            _announce_line(session, "You are no longer marked away.")
            return
        if choice == HELP_KEY:
            help_entries = [MenuEntry(label=menu_key("S", "et"), brief="A one-line notice, optional date")]
            if notice is not None:
                help_entries.append(MenuEntry(label=menu_key("E", "nd it"), brief="Stop being marked away"))
            header_color, unicode_style = await lane.run(
                lambda db: (effective_header_color_256(db), unicode_style_enabled(db, user))
            )
            await show_menu_help(
                session, "Away notice help", help_entries,
                about="Tell members you are away. The notice shows beside your name under Operators; "
                      "it changes none of your permissions.",
                header_color=header_color, unicode_style=unicode_style,
            )
            continue
        if choice != "s":
            await session.write(reject_unhandled_key(choice))
            continue
        await write_prompt(session, f"Message (one line, up to {MAX_AWAY_MESSAGE_CHARS} characters): ")
        message = (await session.read_line()).strip()
        if not message:
            _announce_line(session, colored("Cancelled.", fg_color=MUTED_COLOR))
            continue
        await write_prompt(session, "Back on (YYYY-MM-DD, blank if you don't know): ")
        raw_date = (await session.read_line()).strip()
        try:
            until = _parse_return_date(raw_date) if raw_date else None
        except ValueError:
            _announce_line(session, colored(f"{raw_date!r} is not a date like 2026-10-12.", fg_color=MUTED_COLOR))
            continue
        problem = away_problem(message, until, await lane.run(node_today))
        if problem is not None:
            _announce_line(session, colored(f"{problem[0].upper()}{problem[1:]}.", fg_color=MUTED_COLOR))
            continue
        try:
            await lane.run(set_away, user, message, until)
        except UserManagementError as exc:
            _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
            continue
        _announce_line(session, "You are marked away. Members see it under Operators.")
        return


def _grants_seen_by(db: Database, viewer: User, grants: Sequence) -> str:
    """A moderator's grants in words, leaving out any that would name
    something `viewer` can't see -- a hidden Community, a board, area or
    channel their level or age keeps from them (review on #870). A grant
    over every board or area names nothing, so it always shows."""
    from netbbs.net.board_flow import visible_boards
    from netbbs.net.chat_flow import list_visible_channels_for
    from netbbs.net.file_flow import visible_areas

    if is_usable_sysop(viewer):
        shown = list(grants)
    else:
        seen = {
            "board": {b.id for b in visible_boards(db, viewer, community_id=None, community_scoped=False)},
            "file_area": {a.id for a in visible_areas(db, viewer)},
            "channel": {c.id for c in list_visible_channels_for(db, viewer)},
        }
        shown = []
        for grant in grants:
            if grant.object_id is not None:
                visible = grant.object_id in seen[grant.object_type]
            elif grant.community_id is not None:
                community = get_community(db, grant.community_id)
                visible = community is not None and not community.hidden
            else:
                visible = True
            if visible:
                shown.append(grant)
    if not shown:
        return "moderates content you can't open"
    return "; ".join(describe_grant(db, grant) for grant in shown)


async def staff_list_screen(session: Session, lane: DatabaseLane, user: User) -> None:
    """
    Who runs the node (design doc §5.6, issue #836): every usable SysOp,
    staff member and moderator, what they look after, the day of their
    last session, and any away notice. For members; the main menu offers
    it to everyone but guests.
    """
    def _load(db: Database) -> tuple[list, dict[int, str | None], dict[int, str | None], dict[int, str]]:
        entries = list_staff(db)
        last_on = {entry.user.id: _date_of(db, entry.user.last_login_at) for entry in entries}
        away_since = {entry.user.id: _date_of(db, entry.away.since) if entry.away else None for entry in entries}
        looks_after = {
            entry.user.id: entry.looks_after or _grants_seen_by(db, user, entry.grants) for entry in entries
        }
        return entries, last_on, away_since, looks_after

    entries, last_on, away_since, looks_after = await lane.run(_load)
    rows = [
        [
            (sanitize_text(entry.user.username), AUTHOR_COLOR),
            entry.role,
            sanitize_text(looks_after[entry.user.id]),
            (last_on[entry.user.id] or "never", DATE_COLOR),
        ]
        for entry in entries
    ]
    body: list[Field | Note | Table] = (
        [Table(("Name", "Role", "Looks after", "Last on"), rows, flex=2)] if rows
        else [Note("Nobody is listed yet.")]
    )
    for entry in entries:
        if entry.away is not None:
            body.append(Note(
                sanitize_text(f"{entry.user.username}: {describe_away(entry.away, away_since[entry.user.id])}"),
                color=WARNING_COLOR,
            ))
    await _show_report(
        session, lane, user, "Staff",
        subtitle="Who runs this node, and when they were last on.", sections=[Section(None, body)],
        help_title="Staff help",
        help_about=(
            "Everyone who runs this node: SysOps, staff and moderators, what each looks after, and any away "
            "notice."
        ),
    )


async def _operator_lost_sysop(
    session: Session, lane: DatabaseLane, user: User, node_controls: NodeControls | None
) -> bool:
    """
    Whether the console's operator has been demoted, disabled or deleted
    since it opened (issue #659) -- by another SysOp, by themselves, or
    from another process.

    In a live session the account watcher unwinds the console anyway, and
    keeps the registry's baseline current, so this reads that baseline
    rather than adding a database round trip to every key -- an await
    here shifts the timing of the drain/shutdown screens' cancellation.
    The standalone CLI has no watcher and asks the database.
    """
    if user.user_level < SYSOP_LEVEL:
        return False
    if node_controls is not None:
        baseline = node_controls.session_registry.account_baseline(session)
        return baseline is not None and baseline[0] < SYSOP_LEVEL
    return not await lane.run(_still_sysop, user)


def _still_sysop(db: Database, user: User) -> bool:
    current = current_account(db, user)
    return current is not None and current.user_level >= SYSOP_LEVEL


async def _draw_admin_menu(
    session: Session,
    lane: DatabaseLane,
    actor: User,
    *,
    node_controls: NodeControls | None,
    link_context: LinkContext | None,
    state: dict[str, object] | None = None,
) -> dict[str, object]:
    """Render the SysOp landing page as an operations overview, not a link list."""

    def _load(db: Database) -> dict[str, object]:
        all_users = list_users(db)
        all_boards = list_boards(db)
        all_areas = list_file_areas(db)
        pending_users = sum(user.pending_approval for user in all_users)
        # Counted, not listed, and without sweeping expiry: this runs on every
        # draw of the dashboard (issue #681).
        pending_posts = sum(count_pending_posts(db, board) for board in all_boards)
        pending_files = sum(count_pending_files(db, area) for area in all_areas)
        return {
            "pending_users": pending_users,
            "pending_posts": pending_posts,
            "pending_files": pending_files,
            # Dogfood follow-up: the dashboard previously showed only
            # *pending* counts (always 0 on a quiet node) with no sense
            # of overall node scale at all -- a SysOp glancing at the
            # landing screen couldn't tell how many users/boards/posts/
            # files actually exist.
            "total_users": len(all_users),
            "total_boards": len(all_boards),
            "total_posts": sum(count_listed_posts(db, board)[0] for board in all_boards),
            "total_areas": len(all_areas),
            "total_files": sum(count_listed_files(db, area)[0] for area in all_areas),
            **_link_health_snapshot(db, link_context),
            "backup": _get_display_backup_summary(db),
            "next_backup": _get_display_next_backup(db),
            "update": get_display_check_summary(db),
            "description_level": menu_description_level(db, actor),
            "redraw_in_place": redraw_in_place_enabled(db, actor),
            "unicode_style": unicode_style_enabled(db, actor),
            "collapsed": breadcrumb_collapsed_enabled(db, actor),
            "header_color": effective_header_color_256(db),
            "node_running": node_controls is None and running_node_pid(db.path) is not None,
            **_away_state(db, actor),
        }

    if state is None:
        state = await lane.run(_load)
    active_sessions = len(node_controls.session_registry) if node_controls is not None else None
    maintenance = node_controls.maintenance.is_active() if node_controls is not None else False
    lockdown = node_controls.maintenance.is_lockdown_active() if node_controls is not None else False

    unicode_style = state["unicode_style"]
    collapsed = state["collapsed"]
    box_inner_width = min(session.terminal_width, 78) - 4
    await session.write_line(
        "\r\n" + screen_title(
            "SysOp operations console",
            breadcrumb=crumbs(session, own=True),
            subtitle="Live health, attention queues, and administrative controls.",
            width=session.terminal_width,
            clear=state["redraw_in_place"],
            unicode_style=unicode_style, collapsed=collapsed,
            header_color=state["header_color"],
        node_name_gradient=session.node_name_gradient)
    )

    node_badge = status_badge(
        "LOCKDOWN" if lockdown else "MAINTENANCE" if maintenance else "ONLINE",
        tone="error" if lockdown else "warning" if maintenance else "success",
        unicode_style=unicode_style,
    ) if node_controls is not None else status_badge("LOCAL ADMIN", tone="neutral", unicode_style=unicode_style)
    health: list[str] = [colored("NODE  ", fg_color=LABEL_COLOR, bold=True) + node_badge]
    if active_sessions is not None:
        # No configured session-capacity limit exists anywhere in this
        # codebase to give a gauge a real denominator -- a `max(10,
        # active_sessions)` placeholder made the bar permanently 100%-
        # full (and red) for any count above 10, an identical "at
        # capacity" alarm for 11 sessions and 10,000. An unbounded count
        # has no natural gauge; show the number alone.
        health.append(f"  {counts_row([('Active sessions', active_sessions)])}")
    else:
        standalone_lines = _wrap_panel_sentence(
            _offline_console_note(bool(state.get("node_running"))),
            prefix="  ", width=box_inner_width, unicode_style=unicode_style,
        )
        health.extend(colored(f"  {line}", fg_color=MUTED_COLOR) for line in standalone_lines)

    if link_context is None:
        # `node_controls is None` is this module's own established signal
        # for "running through the standalone admin CLI, which opens its
        # own DB handle and never observes the live node process" (see
        # the identical check just above, for `active_sessions`) -- a
        # standalone session genuinely cannot tell whether the live node
        # actually has Link configured or not, so labeling it "DISABLED"
        # the same as a real, observed disabled config would mislead a
        # SysOp into diagnosing a configuration problem that may not
        # exist (Codex follow-up).
        link_badge_text = "UNAVAILABLE" if node_controls is None else "DISABLED"
        health.append(colored("LINK  ", fg_color=LABEL_COLOR, bold=True) + status_badge(link_badge_text, tone="neutral", unicode_style=unicode_style))
    else:
        node = link_context.link_node
        link_tone = "warning" if not node.peers or state["dead_letters"] else "success"
        link_label = "ATTENTION" if link_tone == "warning" else "HEALTHY"
        health.append(colored("LINK  ", fg_color=LABEL_COLOR, bold=True) + status_badge(link_label, tone=link_tone, unicode_style=unicode_style))
        health.extend(
            _wrap_counts_panel(
                "  ",
                _link_count_pairs(node, state),
                width=box_inner_width,
            )
        )

    health.append(colored("CONTENT", fg_color=LABEL_COLOR, bold=True))
    health.extend(
        _wrap_counts_panel(
            "  ",
            [
                ("Users", state["total_users"]),
                ("Message boards", state["total_boards"]),
                ("Posts", state["total_posts"]),
                ("File areas", state["total_areas"]),
                ("Files", state["total_files"]),
            ],
            width=box_inner_width,
        )
    )

    pending_total = state["pending_users"] + state["pending_posts"] + state["pending_files"]
    health.append(colored("ATTENTION", fg_color=LABEL_COLOR, bold=True))
    # No gauge here: there's no real moderation-queue capacity to measure
    # against (same reasoning as the active-session count above) -- a
    # `max(10, pending_total)` denominator would just make the bar
    # permanently "full" past 10 pending items, same bug as the session
    # gauge this file already dropped for.
    health.append("  " + counts_row([("Moderation", pending_total)]) + " pending")
    health.extend(
        _wrap_counts_panel(
            "    ",
            [("Users", state["pending_users"]), ("Posts", state["pending_posts"]), ("Files", state["pending_files"]),
             *_carry_attention_pairs(state)],
            width=box_inner_width,
        )
    )
    backup_at, _backup_path = state["backup"]
    update_at, update_outcome = state["update"]
    # A real update-check outcome can be an arbitrary-length message (an
    # HTTP client's own exception text, e.g.) -- when boxed, cut it to
    # what the frame can actually hold so it doesn't push the right
    # border past the terminal edge. Only under unicode_style: the flat,
    # unboxed ASCII fallback has no such constraint and must stay
    # byte-for-byte unchanged. `cut_to_width` runs on the plain text
    # before `sanitize_text`/`colored()` wrap it, matching `screen_
    # title`'s own "cut plain, then color" order (this codebase never
    # cuts already-SGR-styled text -- see `double_frame`'s docstring for
    # why).
    def _fit(text: str, prefix_len: int) -> str:
        return cut_to_width(text, box_inner_width - prefix_len) if unicode_style else text

    health.append(
        "  Backup: "
        + (sanitize_text(_fit(backup_at, 10)) if backup_at else colored("never", fg_color=WARNING_COLOR))
    )
    if state.get("next_backup") is not None:
        health.append("  Next backup: " + sanitize_text(_fit(state["next_backup"], 15)))
    health.append(
        "  Update check: "
        + (
            sanitize_text(_fit(update_outcome or update_at or "completed", 17))
            if update_at
            else colored("never", fg_color=WARNING_COLOR)
        )
    )
    if link_context is not None:
        health.extend(
            _wrap_counts_panel(
                "  ",
                [("Recent Link errors", state["recent_errors"]), ("warnings", state["recent_warnings"])],
                width=box_inner_width,
            )
        )

    console, quick = _admin_menu_entries(node_controls, link_context)
    # The landing page is the one console screen that had no answer to a short
    # terminal: a twelve-row health panel over a described two-section menu is
    # 32 rows, so on the classic 24 its own title and the top of the panel had
    # scrolled away before `Choice:` appeared. The sub-consoles already compact
    # their panels and drop descriptions (`_degrade_description_level`); here
    # the same two steps are *measured* rather than estimated -- the full panel
    # with the menu as asked for, then the compact panel, then the compact
    # panel over an undescribed menu -- and the first that fits is drawn.
    def _menu(level: str) -> str:
        return menu_grid(
            [("Console", console), ("Quick", quick)],
            width=session.terminal_width, height=session.terminal_height, description_level=level,
        )

    framed = unicode_style and min(session.terminal_width, 78) >= 4
    title_rows = 3 + (0 if state["redraw_in_place"] else 1)

    def _rows(panel: list[str], menu: str) -> int:
        return title_rows + len(panel) + (2 if framed else 0) + 1 + menu.count("\r\n") + 1 + 1

    compact = _compact_dashboard_panel(
        state, node_badge=node_badge, active_sessions=active_sessions, link_context=link_context,
        node_controls=node_controls, unicode_style=unicode_style, width=box_inner_width,
    )
    # Design doc §5.6: your own away notice, on every landing -- the reminder
    # that ends a notice nobody dated.
    away_lines = _away_panel_lines(state, width=box_inner_width, unicode_style=unicode_style)
    health.extend(away_lines)
    compact.extend(away_lines)
    level = state["description_level"]
    # Before hiding the descriptions, each on its entry's own line (issue #840).
    fallbacks = ((health, level), (compact, level), (compact, "inline"), (compact, "off"))
    for panel, menu_level in fallbacks if level != "off" else ((health, level), (compact, level)):
        menu = _menu(menu_level)
        if _rows(panel, menu) <= session.terminal_height:
            break
    await _write_panel(session, panel, unicode_style=unicode_style, header_color=state["header_color"])
    await session.write_line("\r\n" + menu)
    if menu_level == "off" and level != "off" and "Descriptions hidden" not in menu:
        await session.write_line(
            colored("Descriptions hidden -- terminal too short to show them.", fg_color=MUTED_COLOR)
        )
    await _choice_prompt(session)
    return state


def _admin_menu_entries(
    node_controls: NodeControls | None, link_context: LinkContext | None
) -> tuple[list[MenuEntry], list[MenuEntry]]:
    """The landing page's two menu sections, shared by its draw and its help."""
    # Brief descriptions are kept to roughly 34 characters or less --
    # the actual available width once this renders in two columns at
    # the classic 80-column terminal (menu_grid's own column_width
    # minus its description indent). Longer, fuller text belongs in
    # `detailed`, shown only when a caller opts into that verbosity.
    console = [
        MenuEntry(label=menu_key("U", "sers"), brief="Manage user accounts"),
        MenuEntry(
            label=menu_key("C", "ontent"),
            brief="Boards, areas, channels & more",
            detailed="Manage message boards, file areas, chat channels, and Communities -- including GC (storage garbage collection) under file areas.",
        ),
        MenuEntry(
            label=menu_key("O", "perations"),
            brief="Observe the node, fix trouble",
            detailed="Live node observation: sessions, Link status, the audit log, backup status, and draft cleanup.",
        ),
        MenuEntry(label=menu_key("S", "ettings"), brief="Durable node configuration"),
        # Dogfood follow-up: this used to say "Dashboard" (hotkey "d"),
        # which reads as a promise of some separate, deeper stats view
        # -- it's actually a manual redraw of the exact screen already
        # on display (see the "r" dispatch case in `admin_menu`).
        # "Refresh" says what it actually does; "d" wasn't a natural
        # fit for that word, so the hotkey moves to "r" (unused at
        # this menu) rather than forcing a mismatched letter.
        MenuEntry(label=menu_key("R", "efresh"), brief="Redraw with current numbers"),
        MenuEntry(label=menu_key("B", "ack"), brief="Return to the main menu"),
    ]
    quick = [
        MenuEntry(label=menu_key("F", "ull backups"), brief="Create and review complete backups"),
        MenuEntry(label=menu_key("D", "NS"), brief="Managed netbbs.org name status"),
        MenuEntry(label=menu_key("T", "ime away"), brief="Tell members you're away"),
    ]
    if node_controls is not None:
        quick.insert(
            0,
            MenuEntry(label=menu_key("N", "ode"), brief="Sessions, shutdown, and drain"),
        )
    if link_context is not None:
        quick.extend([
            MenuEntry(label=menu_key("L", "ink status"), brief="NetBBS Link peer/network health"),
            MenuEntry(label=menu_key("Q", "ueue (outbox)"), brief="Pending outgoing Link work items"),
        ])
    return console, quick


def _compact_dashboard_panel(
    state: dict[str, object], *, node_badge: str, active_sessions: int | None,
    link_context: LinkContext | None, node_controls: NodeControls | None, unicode_style: bool, width: int,
) -> list[str]:
    """The landing page's health panel with each group on one row -- the same
    facts, in the same colours, in six rows instead of twelve. Every row is
    wrapped to the frame by `_wrap_counts_panel`, as the sub-consoles' own
    compact panels are."""
    def _label(text: str) -> str:
        return colored(f"{text:<11}", fg_color=LABEL_COLOR, bold=True)

    panel: list[str] = []
    if active_sessions is not None:
        panel.extend(_wrap_counts_panel(
            _label("NODE") + node_badge + "  ", [("Active sessions", active_sessions)], width=width,
        ))
    else:
        panel.append(_label("NODE") + node_badge)
        panel.extend(
            colored(f"  {line}", fg_color=MUTED_COLOR)
            for line in _wrap_panel_sentence(
                _offline_console_note(bool(state.get("node_running"))),
                prefix="  ", width=width, unicode_style=unicode_style,
            )
        )
    if link_context is None:
        # See `_draw_admin_menu`'s own check for why `node_controls is None`,
        # not `link_context` alone, decides between these two words.
        panel.append(_label("LINK") + status_badge(
            "UNAVAILABLE" if node_controls is None else "DISABLED", tone="neutral", unicode_style=unicode_style,
        ))
    else:
        node = link_context.link_node
        link_tone = "warning" if not node.peers or state["dead_letters"] else "success"
        panel.extend(_wrap_counts_panel(
            _label("LINK")
            + status_badge("ATTENTION" if link_tone == "warning" else "HEALTHY", tone=link_tone, unicode_style=unicode_style)
            + "  ",
            _link_count_pairs(node, state),
            width=width,
        ))
    panel.extend(_wrap_counts_panel(
        _label("CONTENT"),
        [
            ("Users", state["total_users"]), ("Message boards", state["total_boards"]),
            ("Posts", state["total_posts"]), ("File areas", state["total_areas"]), ("Files", state["total_files"]),
        ],
        width=width,
    ))
    pending_total = state["pending_users"] + state["pending_posts"] + state["pending_files"]
    panel.extend(_wrap_counts_panel(
        _label("ATTENTION") + counts_row([("Moderation", pending_total)]) + " pending  ",
        [("Users", state["pending_users"]), ("Posts", state["pending_posts"]), ("Files", state["pending_files"]),
         *_carry_attention_pairs(state)],
        width=width,
    ))
    backup_at, _backup_path = state["backup"]
    update_at, update_outcome = state["update"]
    label_width = 11

    def _fit(text: str, used: int) -> str:
        return cut_to_width(text, max(1, width - used)) if unicode_style else text

    # Issue #727: with a schedule on, the same row says when the next one
    # runs, rather than a row of its own on the screen with least room.
    next_backup = state.get("next_backup")
    if next_backup is not None:
        backup_value = f"{backup_at or 'never'}  next {next_backup}"
        panel.append(
            _label("BACKUP")
            + colored(sanitize_text(_fit(backup_value, label_width)), fg_color=VALUE_COLOR if backup_at else WARNING_COLOR)
        )
    else:
        panel.append(
            _label("BACKUP")
            + (colored(sanitize_text(_fit(backup_at, label_width)), fg_color=VALUE_COLOR) if backup_at
               else colored("never", fg_color=WARNING_COLOR))
        )
    panel.append(
        _label("UPDATES")
        + (colored(sanitize_text(_fit(update_outcome or update_at or "completed", label_width)), fg_color=VALUE_COLOR)
           if update_at else colored("never checked", fg_color=WARNING_COLOR))
    )
    if link_context is not None:
        panel.extend(_wrap_counts_panel(
            _label("LINK LOG"),
            [("Recent errors", state["recent_errors"]), ("warnings", state["recent_warnings"])],
            width=width,
        ))
    return panel


# -- users submenu ---------------------------------------------------------


@located("Users")
async def _users_menu(
    session: Session, lane: DatabaseLane, actor: User, *, node_controls: NodeControls | None,
    link_context: LinkContext | None = None,
) -> None:
    """Every user-account action, grouped together (design doc): create,
    list/detail, registration policy, promote/demote, enable/disable,
    delete. `[U]ser list`/`[P]romote/demote`/`[E]nable/disable`/
    `[D]elete user` all route through the same `_pick_and_edit_user` ->
    `_user_detail_screen` central editor now (design doc -- node
    management, Thiesi's own dogfood-testing report), differing only in
    the picker's own title text. `node_controls` is threaded straight
    through to that editor -- it needs it for the live-session-
    revocation guard on disable/delete -- this submenu itself doesn't
    use it directly."""
    def _load_stats(db: Database) -> dict[str, Any]:
        all_users = list_users(db)
        total = len(all_users)
        pending = sum(u.pending_approval for u in all_users)
        disabled = sum(u.disabled_at is not None for u in all_users)
        sysops = count_sysops(db)
        active = sum(not u.pending_approval and u.disabled_at is None for u in all_users)
        return {
            "total": total,
            "active": active,
            "pending": pending,
            "disabled": disabled,
            "sysops": sysops,
            "description_level": menu_description_level(db, actor),
            "unicode_style": unicode_style_enabled(db, actor),
            "collapsed": breadcrumb_collapsed_enabled(db, actor),
            "redraw_in_place": redraw_in_place_enabled(db, actor),
            "header_color": effective_header_color_256(db),
        }

    stats = await lane.run(_load_stats)
    await _draw_users_menu(session, stats=stats)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "c":
            await session.write_line("")
            await _create_user_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "u":
            await session.write_line("")
            await _pick_and_edit_user(session, lane, actor, node_controls, title="Registered users")
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "r":
            await session.write_line("")
            await _registration_settings_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "p":
            await session.write_line("")
            await _pick_and_edit_user(session, lane, actor, node_controls, title="Promote/demote which user?")
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "e":
            await session.write_line("")
            await _pick_and_edit_user(session, lane, actor, node_controls, title="Enable/disable which user?")
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "d":
            await session.write_line("")
            await _pick_and_edit_user(session, lane, actor, node_controls, title="Delete which user?")
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "a":
            await session.write_line("")
            await _promotion_rules_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "l":
            await session.write_line("")
            await _levels_screen(session, lane, actor, node_controls=node_controls, link_context=link_context)
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == "h":
            await session.write_line("")
            await _retired_usernames_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_users_menu(session, stats=stats)
        elif choice == HELP_KEY:
            await show_menu_help(
                session, "Users help", _users_menu_entries(),
                about="Every action on user accounts: creating and finding them, their levels, "
                      "who may sign up, and the names deleted accounts still hold.",
                header_color=stats["header_color"], unicode_style=stats["unicode_style"],
            )
            await _draw_users_menu(session, stats=stats)
        else:
            await session.write(reject_unhandled_key(choice))


def _users_menu_entries() -> list[MenuEntry]:
    """The Users screen's menu, shared by its draw and its help."""
    return [
        MenuEntry(label=menu_key("C", "reate user"), brief="Add a new user account"),
        MenuEntry(label=menu_key("U", "ser list"), brief="Browse and edit accounts"),
        MenuEntry(label=menu_key("R", "egistration"), brief="Signup policy settings"),
        MenuEntry(label=menu_key("P", "romote/demote"), brief="Change a user's level"),
        MenuEntry(label=menu_key("L", "evels (LAST)"), brief="What each level opens"),
        MenuEntry(label=menu_key("A", "uto-promotion rules"), brief="Raise new accounts automatically"),
        MenuEntry(label=menu_key("E", "nable/disable"), brief="Toggle account access"),
        MenuEntry(label=menu_key("D", "elete user"), brief="Permanently remove a user"),
        MenuEntry(label=menu_key("H", "eld names"), brief="Names held for deleted accounts"),
        MenuEntry(label=menu_key("B", "ack"), brief="Return to the SysOp console"),
    ]


_LADDER_COLUMNS = [
    ListColumn("users", 5, VALUE_COLOR, align_right=True),
    ListColumn("opens here", 46, VALUE_COLOR),
]

_LEVEL_VIEWS = {
    "new": "New at this level",
    "open": "Everything open",
    "closed": "Still closed",
    "conditions": "Open, with other gates",
}
_LEVEL_VIEW_ORDER = tuple(_LEVEL_VIEWS)

# What kind of thing each row is, in a word that fits its column (dogfood:
# the access word alone left a SysOp guessing whether "read" meant a board).
_GATE_TYPE_WORDS = {
    "board": "board", "file area": "area", "channel": "channel", "door": "door", "node-wide": "node",
}

# Kept inside 80 columns with a name still worth reading; ALSO gives way
# first, and the narrow-terminal line (`_gate_description`) keeps it whole.
_GATE_COLUMNS = [
    ListColumn("type", 7, MUTED_COLOR),
    ListColumn("access", 8, VALUE_COLOR),
    ListColumn("level", 5, VALUE_COLOR, align_right=True),
    ListColumn("from", 16, MUTED_COLOR),
    ListColumn("also", 12, MUTED_COLOR),
]


def _gate_stable_id(gate: Gate) -> int:
    return list(GateKind).index(gate.kind) * 10_000_000 + (gate.object_id or 0)


def _gate_columns(gate: Gate) -> list[str]:
    action, what = GATE_WORDS[gate.kind]
    also = [f"off: {gate.off}"] if gate.off else []
    also += list(gate.conditions)
    if gate.note:
        also.append(gate.note)
    return [_GATE_TYPE_WORDS[what], action, str(gate.opens_at), gate_source(gate), ", ".join(also)]


def _gate_description(gate: Gate) -> str:
    """The table's columns as one line, for a terminal too narrow for the
    table: `board: post at 10, Community Market; age 18+`."""
    kind, action, level, source, also = _gate_columns(gate)
    return f"{kind}: {action} at {level}, {source}" + (f"; {also}" if also else "")


def _gates_in_view(gates: list[Gate], level: int, view: str) -> list[Gate]:
    if view == "new":
        return [g for g in gates if g.off is None and g.opens_at == level]
    if view == "open":
        return [g for g in gates if g.opens_for(level)]
    if view == "closed":
        return [g for g in gates if not g.opens_for(level)]
    return [g for g in gates if g.opens_for(level) and g.conditions]


async def _open_gate_resource(
    session: Session, lane: DatabaseLane, actor: User, gate: Gate, *,
    node_controls: NodeControls | None, link_context: LinkContext | None,
) -> None:
    """The board's, file area's, channel's or door's own detail screen, the
    one the Content menu opens, so its levels can be changed from here. It
    gets the same live services the Content menu hands it: the chat hub
    that refuses renaming an occupied channel and moves callers out of a
    deleted one, the door supervisor, the transfer grants."""
    from netbbs.doors.registry import list_doors

    what = GATE_WORDS[gate.kind][1]
    listing = {"board": list_boards, "file area": list_file_areas, "channel": list_channels, "door": list_doors}[what]
    resource = next((r for r in await lane.run(listing) if r.id == gate.object_id), None)
    if resource is None:
        _announce_line(session, colored("That no longer exists.", fg_color=MUTED_COLOR))
        return
    controls = node_controls
    if what == "board":
        await _board_detail_screen(session, lane, actor, resource, link_context=link_context)
    elif what == "file area":
        await _area_detail_screen(
            session, lane, actor, resource, link_context=link_context,
            transfers=controls.transfers if controls is not None else None,
        )
    elif what == "channel":
        await _channel_detail_screen(
            session, lane, actor, resource, link_context=link_context,
            mrc_bridge=controls.mrc_bridge if controls is not None else None,
            chat_hub=controls.chat_hub if controls is not None else None,
        )
    else:
        await _door_detail_screen(
            session, lane, actor, resource,
            door_services=controls.door_services if controls is not None else None,
            backup_identity_dir=controls.backup_identity_dir if controls is not None else None,
        )


async def _level_detail_screen(
    session: Session, lane: DatabaseLane, actor: User, level: int, *,
    node_controls: NodeControls | None, link_context: LinkContext | None,
) -> None:
    """Design doc §5.7, issue #1007: what one level opens, gate by gate, with
    where each level comes from. `[V]iew` steps through what is new at this
    level, everything open, what stays closed, and what opens with another
    gate still in the way; picking a board, file area, channel or door opens
    its own screen."""
    view = "new"
    start: int | None = None

    async def _load() -> list[Gate]:
        return _gates_in_view(await lane.run(list_gates), level, view)

    async def _next_view() -> list[Gate]:
        nonlocal view
        view = _LEVEL_VIEW_ORDER[(_LEVEL_VIEW_ORDER.index(view) + 1) % len(_LEVEL_VIEW_ORDER)]
        return await _load()

    chrome = await _load_chrome(lane, actor)
    names = await lane.run(get_level_names)
    with entered(session, f"Level {level_label(level, names)}"):
        users = await lane.run(
            lambda db: db.connection.execute(
                "SELECT COUNT(*) FROM users WHERE user_level = ? AND disabled_at IS NULL AND pending_approval = 0",
                (level,),
            ).fetchone()[0]
        )
        while True:
            picked = await _pick_item(
                session, await _load(),
                name_of=lambda gate: gate.name + (" (hidden)" if gate.hidden else ""),
                stable_id_of=_gate_stable_id,
                columns=_GATE_COLUMNS,
                column_values_of=_gate_columns,
                description_of=_gate_description,
                title=f"Level {level_label(level, names)}", own=True,
                empty_message=f"Nothing in this view of level {level}.",
                refresh=_load,
                live_keys={"v": _next_view},
                live_nav=[MenuEntry(label=menu_key("V", "iew"), brief="New, open, closed, other gates")],
                live_label=lambda: f"Showing: {_LEVEL_VIEWS[view]}, {users} account{'s' if users != 1 else ''} at {level}",
                selectable_of=lambda gate: gate.object_id is not None,
                start_stable_id=start,
                description_level=await lane.run(menu_description_level, actor),
                redraw_in_place=chrome.redraw_in_place,
                unicode_style=chrome.unicode_style,
                collapsed=chrome.collapsed,
                accent_color=chrome.accent_color,
                header_color=chrome.header_color,
            )
            if picked is None:
                return
            start = _gate_stable_id(picked)
            await _open_gate_resource(
                session, lane, actor, picked, node_controls=node_controls, link_context=link_context
            )


@located("Levels")
async def _levels_screen(
    session: Session, lane: DatabaseLane, actor: User, *,
    node_controls: NodeControls | None, link_context: LinkContext | None,
) -> None:
    """Design doc §5.7, issue #1007: the level ladder -- every level that
    matters on this node, how many accounts hold it, and what it first
    opens. Picking a level shows what it opens in full; `[G]o to level`
    asks for any level, in use or not."""
    start: int | None = None
    names: dict[int, str] = {}
    status: str | None = None

    async def _load() -> list[LadderStep]:
        nonlocal names
        ladder, names = await lane.run(lambda db: (level_ladder(db), get_level_names(db)))
        return ladder

    async def _name_level(step: LadderStep) -> list[LadderStep] | None:
        """Issue #1009: name, rename or clear a level's name. The name is a
        label; nobody's access changes."""
        nonlocal status
        if step.level >= SYSOP_LEVEL:
            status = f"{SYSOP_LEVEL} is always called SysOp."
            return None
        await write_field_prompt(
            session, colored(f"Name for level {step.level} (blank clears it):", fg_color=MUTED_COLOR),
            hint=_EDIT_HINT,
        )
        try:
            raw = await _read_seeded_line(session, initial=names.get(step.level, ""))
        except InputCancelled:
            return None
        try:
            updated = await lane.run(set_level_name, step.level, raw, changed_by=actor)
        except LevelNameError as exc:
            status = str(exc)
            return None
        name = updated.get(step.level)
        status = f"Level {step.level} is now called {name}." if name else f"Level {step.level} has no name now."
        return await _load()

    def _status_line() -> str:
        return status or "Each row: what that level adds to the ones below it"

    async def _other_level() -> list[LadderStep] | None:
        await write_field_prompt(
            session, colored(f"Which level? (0-{SYSOP_LEVEL}, or a level's name)", fg_color=MUTED_COLOR),
            hint=_EDIT_HINT,
        )
        try:
            raw = (await _read_seeded_line(session, initial="")).strip()
        except InputCancelled:
            return None
        chosen = parse_level(raw, names)
        if chosen is None or not 0 <= chosen <= SYSOP_LEVEL:
            await session.write("\a")
            return None
        await _level_detail_screen(
            session, lane, actor, chosen, node_controls=node_controls, link_context=link_context
        )
        # A level may have been changed on the way: count again.
        return await _load()

    chrome = await _load_chrome(lane, actor)
    while True:
        picked = await _pick_item(
            session, await _load(),
            name_of=lambda step: level_label(step.level, names),
            stable_id_of=lambda step: step.level,
            columns=_LADDER_COLUMNS,
            column_values_of=lambda step: [str(step.users), ladder_summary(step)],
            description_of=lambda step: (
                f"{step.users} user{'s' if step.users != 1 else ''}; opens {ladder_summary(step)}"
            ),
            title="Level Admin SysOp Tool (LAST)", own=True,
            empty_message="No levels in use.",
            refresh=_load,
            live_keys={"g": _other_level},
            item_keys={"n": _name_level},
            live_nav=[
                MenuEntry(label=menu_key("G", "o to level"), brief="What any level opens"),
                MenuEntry(label=menu_key("N", "ame"), brief="Name or rename a level"),
            ],
            live_label=_status_line,
            start_stable_id=start,
            description_level=await lane.run(menu_description_level, actor),
            redraw_in_place=chrome.redraw_in_place,
            unicode_style=chrome.unicode_style,
            collapsed=chrome.collapsed,
            accent_color=chrome.accent_color,
            header_color=chrome.header_color,
        )
        if picked is None:
            return
        start = picked.level
        await _level_detail_screen(
            session, lane, actor, picked.level, node_controls=node_controls, link_context=link_context
        )


@dataclass(frozen=True)
class _RuleRow:
    """One row of the Promotion rules screen: the rule, how many accounts it
    would promote at their next login, and what its step opens."""

    rule: PromotionRule
    qualifying: int
    opens: str


_RULE_COLUMNS = [
    ListColumn("needs", 22, VALUE_COLOR),
    ListColumn("ready", 5, VALUE_COLOR, align_right=True),
    ListColumn("opens", 24, MUTED_COLOR),
]


def _load_rule_rows(db: Database) -> tuple[list[_RuleRow], dict[int, str]]:
    gates = list_gates(db)
    rows = []
    for rule in get_promotion_rules(db):
        gained = level_change(gates, rule.from_level, rule.to_level).gained
        opens = ladder_summary(LadderStep(rule.to_level, 0, gained)) if gained else "nothing"
        rows.append(_RuleRow(rule, count_qualifying(db, rule), opens))
    return rows, get_level_names(db)


class _RuleDraftError(PromotionRuleError):
    pass


async def _edit_promotion_rule(
    session: Session, lane: DatabaseLane, actor: User, existing: PromotionRule | None,
) -> PromotionRule | None:
    """Create or edit one rule (issue #992). Five values are a draft (§3.5)."""
    draft: dict = dict(asdict(existing)) if existing is not None else {
        "from_level": 0, "to_level": 10, "min_age_hours": 24, "min_logins": 2, "min_posts": 0,
    }
    names = await lane.run(get_level_names)
    fields = [
        FieldSpec(
            key="from_level", label="From level",
            render=lambda d: level_label(d["from_level"], names),
            prompt=_int_field("from_level", "Level the account is at"), brief="Accounts at this level",
            help="The rule looks at accounts at exactly this level. One rule per level.", section="Step",
        ),
        FieldSpec(
            key="to_level", label="To level",
            render=lambda d: level_label(d["to_level"], names),
            prompt=_int_field("to_level", "Level to raise it to"), brief="Raised to this level",
            help=f"The level the account is raised to; higher than From, at most {SYSOP_LEVEL - 1}.",
            section="Step",
        ),
        FieldSpec(
            key="min_age_hours", label="Account age",
            render=lambda d: f"{d['min_age_hours']} hours",
            prompt=_int_field("min_age_hours", "Hours since the account was created"),
            brief="Hours since signup", help="How long the account must have existed.", section="Needs",
        ),
        FieldSpec(
            key="min_logins", label="Logins",
            render=lambda d: str(d["min_logins"]),
            prompt=_int_field("min_logins", "Logins, this one included"),
            brief="Logins, this one included",
            help="How many times the account must have logged in, counting the login being checked.",
            section="Needs",
        ),
        FieldSpec(
            key="min_posts", label="Posts",
            render=lambda d: str(d["min_posts"]),
            prompt=_int_field("min_posts", "Approved posts"), brief="Approved posts on this node",
            help="Approved posts the account wrote on this node, each counted once however often edited. "
                 "0 asks for none.",
            section="Needs",
        ),
    ]

    async def save(values: dict) -> PromotionRule:
        rule = PromotionRule(**{key: values[key] for key in (
            "from_level", "to_level", "min_age_hours", "min_logins", "min_posts")})

        def _persist(db: Database) -> PromotionRule:
            others = [r for r in get_promotion_rules(db) if existing is None or r.from_level != existing.from_level]
            try:
                save_promotion_rules(db, [*others, rule], changed_by=actor)
            except PromotionRuleError as exc:
                raise _RuleDraftError(str(exc)) from exc
            return rule

        return await lane.run(_persist)

    rule = await edit_resource_draft(
        session, lane,
        title="Edit promotion rule" if existing is not None else "New promotion rule",
        fields=fields, draft=draft, save=save, error_type=_RuleDraftError,
        save_menu_text=menu_key("S", "ave"), back_menu_text=menu_key("B", "ack"),
        description_level=await lane.run(menu_description_level, actor),
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
        accent_color=await lane.run(effective_accent_color_256),
        header_color=await lane.run(effective_header_color_256),
    )
    if rule is not None:
        _announce_line(session, f"Saved: {rule.from_level} -> {rule.to_level} ({rule.describe()}).")
    return rule


@located("Promotion rules")
async def _promotion_rules_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Design doc §4.3, issue #992: the node's promotion rules. Each row says
    what an account needs, how many accounts would be promoted at their next
    login, and what the step opens. `[C]reate` adds a rule, picking one
    edits it, `[D]elete` removes the highlighted one."""
    names: dict[int, str] = {}
    status: str | None = None
    start: int | None = None
    # The picker returns what `[C]reate` made as the pick; that rule was
    # just edited, so it is not opened again.
    created: int | None = None

    async def _load() -> list[_RuleRow]:
        nonlocal names
        rows, names = await lane.run(_load_rule_rows)
        return rows

    async def _create() -> _RuleRow | None:
        nonlocal created
        rule = await _edit_promotion_rule(session, lane, actor, None)
        if rule is None:
            return None
        created = rule.from_level
        return next((row for row in await _load() if row.rule == rule), None)

    async def _delete(row: _RuleRow) -> list[_RuleRow] | None:
        nonlocal status
        await session.write_line("")
        if not await prompt_yes_no(session, f"Delete the rule {row.rule.from_level} -> {row.rule.to_level}?",
                                   default=False):
            return None

        def _persist(db: Database) -> None:
            rules = [r for r in get_promotion_rules(db) if r.from_level != row.rule.from_level]
            save_promotion_rules(db, rules, changed_by=actor)

        await lane.run(_persist)
        status = f"Deleted the rule {row.rule.from_level} -> {row.rule.to_level}."
        return await _load()

    chrome = await _load_chrome(lane, actor)
    while True:
        picked = await _pick_item(
            session, await _load(),
            name_of=lambda row: f"{row.rule.from_level} → {level_label(row.rule.to_level, names)}",
            stable_id_of=lambda row: row.rule.from_level,
            columns=_RULE_COLUMNS,
            column_values_of=lambda row: [row.rule.describe(), str(row.qualifying), row.opens],
            description_of=lambda row: (
                f"needs {row.rule.describe()}; {row.qualifying} ready; opens {row.opens}"
            ),
            title="Promotion rules", own=True,
            empty_message="No promotion rules. [C]reate one: levels then change by hand only.",
            refresh=_load,
            on_create=_create,
            item_keys={"d": _delete},
            live_nav=[MenuEntry(label=menu_key("D", "elete"), brief="Remove a rule")],
            live_label=lambda: status or "Checked at each login, one step per login. Ready: promoted at next login",
            start_stable_id=start,
            description_level=await lane.run(menu_description_level, actor),
            redraw_in_place=chrome.redraw_in_place,
            unicode_style=chrome.unicode_style,
            collapsed=chrome.collapsed,
            accent_color=chrome.accent_color,
            header_color=chrome.header_color,
        )
        if picked is None:
            return
        start = picked.rule.from_level
        if created == picked.rule.from_level:
            created = None
            continue
        rule = await _edit_promotion_rule(session, lane, actor, picked.rule)
        if rule is not None:
            start = rule.from_level


async def _draw_users_menu(session: Session, *, stats: dict[str, Any]) -> None:
    unicode_style = stats["unicode_style"]
    collapsed = stats["collapsed"]
    redraw_in_place = stats["redraw_in_place"]
    header_color = stats["header_color"]
    description_level = stats["description_level"]

    await session.write_line(
        "\r\n"
        + screen_title(
            "Users",
            breadcrumb=crumbs(session, own=True),
            subtitle="Account administration, registration policy, and access levels.",
            width=session.terminal_width,
            clear=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            header_color=header_color,
            node_name_gradient=session.node_name_gradient,
        )
    )

    panel: list[str] = []
    if session.terminal_height >= 18:
        compact = session.terminal_height < 28 or session.terminal_width < 72
        box_inner_width = min(session.terminal_width, 78) - 4
        if compact:
            panel.extend(
                _wrap_counts_panel(
                    colored("ACCOUNTS: ", fg_color=LABEL_COLOR, bold=True),
                    [
                        ("Total users", stats["total"]),
                        ("Active", stats["active"]),
                        ("Pending", stats["pending"]),
                        ("Disabled", stats["disabled"]),
                        ("SysOps", stats["sysops"]),
                    ],
                    width=box_inner_width,
                )
            )
            # `telemetry_gauge`'s own ` current/total` suffix grows with
            # this node's real account counts (unbounded, unlike the
            # fixed-width bar itself) -- a fixed 10-cell bar plus a
            # 4-digit-vs-4-digit ratio ("1000/1000") already overflows
            # this 36-column compact frame once the "Active ratio: "
            # label is added. A narrower 6-cell bar keeps the same
            # meter legible while leaving enough budget for a
            # established node's real account counts (Codex follow-up,
            # PR #197 review).
            panel.append(
                "  "
                + colored("Active ratio: ", fg_color=METADATA_COLOR)
                + telemetry_gauge(stats["active"], max(1, stats["total"]), width=6, unicode_style=unicode_style, tone="health")
            )
            if stats["pending"] > 0:
                # The non-compact branch below already warns on pending
                # registrations -- compact mode silently dropped the same
                # signal entirely (PR #197 review), not just the glyph
                # (that was finding #9, fixed separately).
                warning_prefix = "⚠ " if unicode_style else "! "
                warning_lines = _wrap_panel_sentence(
                    f"{warning_prefix}{stats['pending']} registration{'s' if stats['pending'] != 1 else ''} awaiting review",
                    prefix="  ", width=box_inner_width, unicode_style=unicode_style,
                )
                panel.extend("  " + colored(line, fg_color=WARNING_COLOR, bold=True) for line in warning_lines)
        else:
            panel = [
                colored("ACCOUNTS", fg_color=LABEL_COLOR, bold=True),
                "  "
                + counts_row(
                    [
                        ("Total users", stats["total"]),
                        ("Active", stats["active"]),
                        ("Pending", stats["pending"]),
                        ("Disabled", stats["disabled"]),
                    ]
                ),
                "  "
                + counts_row([("SysOps", stats["sysops"])])
                + "    "
                + colored("Active ratio: ", fg_color=METADATA_COLOR)
                + telemetry_gauge(stats["active"], max(1, stats["total"]), unicode_style=unicode_style, tone="health"),
            ]
            if stats["pending"] > 0:
                warning_prefix = "⚠ " if unicode_style else "! "
                panel.append(
                    "  "
                    + colored(
                        f"{warning_prefix}{stats['pending']} registration{'s' if stats['pending'] != 1 else ''} awaiting review",
                        fg_color=WARNING_COLOR,
                        bold=True,
                    )
                )

        await _write_panel(session, panel, unicode_style=unicode_style, header_color=header_color)

    entries = _users_menu_entries()
    effective_desc_level, available_menu_height, desc_degraded = _degrade_description_level(
        panel=panel, unicode_style=unicode_style, description_level=description_level,
        entry_count=len(entries), terminal_width=session.terminal_width, terminal_height=session.terminal_height,
    )

    await session.write_line(
        _menu_row(
            entries,
            effective_desc_level,
            width=session.terminal_width,
            height=available_menu_height,
            degraded=desc_degraded,
        )
    )
    await _choice_prompt(session)


# -- system submenu ----------------------------------------------------------


def _diagnostics_available(node_controls: NodeControls | None, link_context: LinkContext | None) -> bool:
    """Whether the bounded diagnostic log is worth offering.

    It is written by Link *and* by the MRC bridge (issue #275), and MRC can
    be switched on live without Link -- so on any running node the log is
    worth reading. Operations and Settings' hidden aliases share this one
    gate (issue #732: Settings once required Link alone)."""
    return link_context is not None or node_controls is not None


@located("Operations")
async def _operations_menu(
    session: Session,
    lane: DatabaseLane,
    actor: User,
    *,
    node_controls: NodeControls | None,
    link_context: LinkContext | None,
) -> None:
    """Operational observation and intervention, separate from durable settings."""
    def _load_ops(db: Database) -> dict[str, Any]:
        return {
            "node_running": node_controls is None and running_node_pid(db.path) is not None,
            **_link_health_snapshot(db, link_context),
            "backup": _get_display_backup_summary(db),
            "description_level": menu_description_level(db, actor),
            "unicode_style": unicode_style_enabled(db, actor),
            "collapsed": breadcrumb_collapsed_enabled(db, actor),
            "redraw_in_place": redraw_in_place_enabled(db, actor),
            "header_color": effective_header_color_256(db),
        }

    # A durable snapshot, reloaded only after an action that can actually
    # change one of `_load_ops`'s own DB-backed fields (dead_letters,
    # diagnostics, backup) -- mirrors `_draw_admin_menu`'s own snapshot-
    # reuse discipline. Reloading unconditionally on every redraw (the
    # previous shape here) meant returning from `[N]ode` after scheduling
    # an immediate shutdown/drain re-ran this full DB-lane query before
    # the screen could unwind, reintroducing a cancellable wait into the
    # shutdown path. `active_sessions`/`maintenance`/`lockdown` are never
    # part of this snapshot -- they read `node_controls` directly, in
    # memory, so recomputing them every loop iteration is free.
    state = await lane.run(_load_ops)
    diagnostics_available = _diagnostics_available(node_controls, link_context)
    while True:
        unicode_style = state["unicode_style"]
        collapsed = state["collapsed"]
        redraw_in_place = state["redraw_in_place"]
        header_color = state["header_color"]
        description_level = state["description_level"]

        active_sessions = len(node_controls.session_registry) if node_controls is not None else None
        maintenance = node_controls.maintenance.is_active() if node_controls is not None else False
        lockdown = node_controls.maintenance.is_lockdown_active() if node_controls is not None else False

        await session.write_line(
            "\r\n" + screen_title(
                "Operations",
                breadcrumb=crumbs(session, own=True),
                subtitle="Observe the running node, investigate trouble, and recover work.",
                width=session.terminal_width,
                clear=redraw_in_place,
                unicode_style=unicode_style, collapsed=collapsed,
                header_color=header_color,
                node_name_gradient=session.node_name_gradient,
            )
        )

        node_badge = status_badge(
            "LOCKDOWN" if lockdown else "MAINTENANCE" if maintenance else "ONLINE",
            tone="error" if lockdown else "warning" if maintenance else "success",
            unicode_style=unicode_style,
        ) if node_controls is not None else status_badge("LOCAL ADMIN", tone="neutral", unicode_style=unicode_style)

        panel: list[str] = []
        if session.terminal_height >= 18:
            compact = session.terminal_height < 28 or session.terminal_width < 72
            box_inner_width = min(session.terminal_width, 78) - 4
            backup_at, _backup_path = state["backup"]
            backup_str = sanitize_text(backup_at) if backup_at else colored("never", fg_color=WARNING_COLOR)

            if compact:
                if active_sessions is not None:
                    panel.extend(
                        _wrap_counts_panel(
                            colored("NODE HEALTH: ", fg_color=LABEL_COLOR, bold=True) + node_badge + "  ",
                            [("Active sessions", active_sessions)],
                            width=box_inner_width,
                        )
                    )
                else:
                    panel.append(colored("NODE HEALTH: ", fg_color=LABEL_COLOR, bold=True) + node_badge)
                    standalone_lines = _wrap_panel_sentence(
                        _offline_console_note(bool(state.get("node_running"))),
                        prefix="  ", width=box_inner_width, unicode_style=unicode_style,
                    )
                    panel.extend(colored(f"  {line}", fg_color=MUTED_COLOR) for line in standalone_lines)

                if link_context is None:
                    # See `_draw_admin_menu`'s identical check for why
                    # `node_controls is None` -- not `link_context` alone
                    # -- decides the label here (Codex follow-up).
                    link_badge_text = "UNAVAILABLE" if node_controls is None else "DISABLED"
                    panel.append(colored("LINK OPERATIONS: ", fg_color=LABEL_COLOR, bold=True) + status_badge(link_badge_text, tone="neutral", unicode_style=unicode_style))
                else:
                    node = link_context.link_node
                    link_tone = "warning" if not node.peers or state["dead_letters"] else "success"
                    link_label = "ATTENTION" if link_tone == "warning" else "HEALTHY"
                    panel.extend(
                        _wrap_counts_panel(
                            colored("LINK OPERATIONS: ", fg_color=LABEL_COLOR, bold=True)
                            + status_badge(link_label, tone=link_tone, unicode_style=unicode_style)
                            + "  ",
                            _link_count_pairs(node, state),
                            width=box_inner_width,
                        )
                    )
                backup_pairs = []
                if link_context is not None:
                    # `_load_ops` deliberately skips the diagnostics query
                    # without Link (see its own body) -- reporting these
                    # as a bare "0" here would claim a check that never
                    # happened, exactly like the non-compact branch below
                    # already avoids by omitting the counts entirely.
                    backup_pairs = [("Recent errors", state["recent_errors"]), ("warnings", state["recent_warnings"])]
                # `_wrap_counts_panel` only ever wraps/omits its `pairs`,
                # never its own `label` -- fine for every other label
                # here (a short, fixed heading), but `backup_at` is a
                # full ISO timestamp (~27 columns) once a backup has
                # actually run, wide enough on its own to overflow this
                # frame regardless of `backup_pairs` (worse still with
                # none, the `link_context is None` case, where
                # `_wrap_counts_panel` has nothing to test the label's
                # width against at all and returns it completely as-is).
                # Cut the plain timestamp first, then color -- same
                # order `_fit` above already established for this exact
                # value on the landing page.
                backup_label_prefix, backup_label_suffix = "  Backup: ", "  "
                if backup_at and unicode_style:
                    backup_budget = box_inner_width - visible_width(backup_label_prefix) - visible_width(backup_label_suffix)
                    compact_backup_str = sanitize_text(cut_to_width(backup_at, backup_budget))
                else:
                    compact_backup_str = backup_str
                panel.extend(
                    _wrap_counts_panel(
                        backup_label_prefix + compact_backup_str + backup_label_suffix,
                        backup_pairs, width=box_inner_width,
                    )
                )
            else:
                panel = [colored("NODE HEALTH  ", fg_color=LABEL_COLOR, bold=True) + node_badge]
                if active_sessions is not None:
                    panel.append(f"  {counts_row([('Active sessions', active_sessions)])}")
                else:
                    panel.extend(
                        colored(f"  {line}", fg_color=MUTED_COLOR)
                        for line in _wrap_panel_sentence(
                            _offline_console_note(bool(state.get("node_running"))),
                            prefix="  ", width=box_inner_width, unicode_style=unicode_style,
                        )
                    )

                if link_context is None:
                    link_badge_text = "UNAVAILABLE" if node_controls is None else "DISABLED"
                    panel.append(colored("LINK OPERATIONS  ", fg_color=LABEL_COLOR, bold=True) + status_badge(link_badge_text, tone="neutral", unicode_style=unicode_style))
                else:
                    node = link_context.link_node
                    link_tone = "warning" if not node.peers or state["dead_letters"] else "success"
                    link_label = "ATTENTION" if link_tone == "warning" else "HEALTHY"
                    panel.append(colored("LINK OPERATIONS  ", fg_color=LABEL_COLOR, bold=True) + status_badge(link_label, tone=link_tone, unicode_style=unicode_style))
                    panel.append(
                        "  " + counts_row(
                            _link_count_pairs(node, state)
                        )
                    )
                    panel.append(
                        "  " + counts_row([("Recent Link errors", state["recent_errors"]), ("warnings", state["recent_warnings"])])
                    )
                panel.append("  Backup: " + backup_str)

            await _write_panel(session, panel, unicode_style=unicode_style, header_color=header_color)

        options = _operations_menu_entries(node_controls, link_context, diagnostics_available)

        effective_desc_level, available_menu_height, desc_degraded = _degrade_description_level(
            panel=panel, unicode_style=unicode_style, description_level=description_level,
            entry_count=len(options), terminal_width=session.terminal_width, terminal_height=session.terminal_height,
        )

        await session.write_line(
            _menu_row(
                options, effective_desc_level, width=session.terminal_width,
                height=available_menu_height, degraded=desc_degraded,
            )
        )
        await _choice_prompt(session)
        choice = (await session.read_key()).lower()
        if choice == "b":
            await session.write_line("")
            return
        # Every dispatch branch below except `[N]ode` reloads the
        # snapshot on return: Outbox can change `dead_letters`, Follow
        # Log/Diagnostics can leave newer entries behind after however
        # long the SysOp spent watching them, and a fresh backup can be
        # triggered from Backup status -- none of them share Node
        # control's own reason to skip the reload (a real DB-lane wait
        # sitting in the shutdown/drain path itself), so unlike that one
        # branch there's no cost to staying accurate here (Codex
        # follow-up: the first cut of this fix over-corrected to
        # "reload only after Outbox", leaving every other action's
        # numbers stale until the SysOp happened to also visit Outbox
        # or re-enter Operations from scratch).
        if choice == "n" and node_controls is not None:
            await _node_menu(session, lane, actor, node_controls)
        elif choice == "l" and link_context is not None:
            await _link_status_screen(session, lane, actor, link_context=link_context, node_controls=node_controls)
            state = await lane.run(_load_ops)
        elif choice == "o" and link_context is not None:
            await _outbox_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "d" and diagnostics_available:
            await _diagnostic_log_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "w" and diagnostics_available:
            await _diagnostic_log_tail_screen(session, lane)
            state = await lane.run(_load_ops)
        elif choice == "r" and link_context is not None:
            await _repair_carried_posts_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "f":
            await _backup_status_screen(session, lane, actor, node_controls=node_controls)
            state = await lane.run(_load_ops)
        elif choice == "p":
            await _prune_drafts_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "s":
            await _search_indexes_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "a":
            await _audit_log_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "e":
            await _node_log_screen(session, lane, actor)
            state = await lane.run(_load_ops)
        elif choice == "m":
            await _mail_tools_screen(session, lane, actor, link_context=link_context)
            state = await lane.run(_load_ops)
        elif choice == HELP_KEY:
            await show_menu_help(
                session, "Operations help",
                _operations_menu_entries(node_controls, link_context, diagnostics_available),
                about="Watch the running node and fix trouble: sessions, backups, logs, mail and Link. "
                      "Durable settings live under Settings instead.",
                header_color=header_color, unicode_style=unicode_style,
            )
        else:
            await session.write(reject_unhandled_key(choice))


def _operations_menu_entries(
    node_controls: NodeControls | None, link_context: LinkContext | None, diagnostics_available: bool
) -> list[MenuEntry]:
    """The Operations screen's menu, shared by its draw and its help."""
    options = [
        MenuEntry(label=menu_key("F", "ull backups"), brief="Create and review complete backups"),
        MenuEntry(label=menu_key("P", "rune drafts"), brief="Clean up old unsaved drafts"),
        MenuEntry(label=menu_key("S", "earch indexes"), brief="Check and rebuild Find's indexes"),
        MenuEntry(label=menu_key("A", "udit log"), brief="Moderation action history"),
        MenuEntry(label=menu_key("E", "rror log"), brief="Warnings and errors in netbbs.log"),
        MenuEntry(label=menu_key("M", "ail"), brief="Mailbox sizes, refused Link mail"),
    ]
    if node_controls is not None:
        options.insert(0, MenuEntry(label=menu_key("N", "ode and sessions"), brief="Sessions, shutdown, and drain"))
    if link_context is not None:
        options.extend([
            MenuEntry(label=menu_key("L", "ink status"), brief="NetBBS Link peer/network health"),
            MenuEntry(label=menu_key("O", "utbox"), brief="Pending outgoing Link work items"),
        ])
    if diagnostics_available:
        options.extend([
            MenuEntry(label=menu_key("D", "iagnostics"), brief="Recent Link and MRC diagnostics"),
            MenuEntry(label=menu_key("W", "atch log"), brief="Live-tail the diagnostic log"),
        ])
    if link_context is not None:
        options.append(
            MenuEntry(label=menu_key("R", "epair carried posts"), brief="Fix inconsistent carried posts")
        )
    options.append(MenuEntry(label=menu_key("B", "ack"), brief="Return to the SysOp console"))
    return options


# -- trust policy (Phase 4, issue #129) -------------------------------------


# -- create ------------------------------------------------------------


def _create_user_password_field() -> Callable[[Session, DatabaseLane, dict], Awaitable[None]]:
    """The new account's password, typed unseen into the field and then
    once more to confirm it. An empty first answer clears it; a mismatch
    keeps what the draft had and says so. This screen's draft is never
    seeded from an existing account, so there is no stored password a
    clear/keep distinction would need to protect."""

    @inline_field
    async def prompt(session: Session, lane: DatabaseLane, draft: dict) -> None:
        await write_field_prompt(
            session, colored("Password (typed unseen; Enter on an empty line clears it):", fg_color=MUTED_COLOR),
            hint="Password, typed unseen; empty clears it",
        )
        try:
            first = await _read_seeded_line(session, initial="", echo=False)
            if not first:
                draft["password"] = None
                return
            await write_field_prompt(
                session, colored("Confirm password:", fg_color=MUTED_COLOR), hint="Type it again to confirm",
            )
            second = await _read_seeded_line(session, initial="", echo=False)
        except InputCancelled:
            return
        if first != second:
            await write_field_message(
                session, colored("Passwords did not match -- password unchanged.", fg_color=MUTED_COLOR),
            )
            return
        draft["password"] = first

    return prompt


def _create_user_pubkey_field() -> Callable[[Session, DatabaseLane, dict], Awaitable[None]]:
    """`_create_user_password_field`'s counterpart for the public key: pasted
    into the field, an empty line clears it, a key that does not parse
    keeps what the draft had and says why."""

    @inline_field
    async def prompt(session: Session, lane: DatabaseLane, draft: dict) -> None:
        await write_field_prompt(
            session, colored("Public key (base64, or an ssh-ed25519 line; empty clears it):", fg_color=MUTED_COLOR),
            hint="Paste the key; empty clears it",
        )
        try:
            text = (await _read_seeded_line(session, initial="")).strip()
        except InputCancelled:
            return
        if not text:
            draft["verify_key"] = None
            return
        try:
            draft["verify_key"] = parse_verify_key(text)
        except IdentityError as exc:
            await write_field_message(
                session, colored(f"Could not parse key: {exc} -- key unchanged.", fg_color=MUTED_COLOR),
            )

    return prompt


def _create_user_field_specs() -> list[FieldSpec]:
    """Dogfood feature request, issue #160's cursor-navigation follow-up
    (item 4 of the prioritized list): the linear username -> password? ->
    key? -> level wizard forced every field in sequence with no way to
    revisit or skip ahead. Same draft-editor shape as every other
    create/edit screen -- see `netbbs.net.resource_editor`'s own module
    docstring -- except this one has no `existing` counterpart to edit:
    account creation is the only thing this screen does."""
    return [
        FieldSpec(
            key="username", label="Username",
            render=lambda d: d.get("username") or "",
            prompt=text_field("username", required=True),
            help="Letters, digits, '_', '-', and '.' only. Case-insensitive uniqueness.",
        ),
        FieldSpec(
            key="password", label="Password",
            render=lambda d: "set" if d.get("password") else "(not set)",
            prompt=_create_user_password_field(),
            help=(
                "An account needs a password, a public key, or both. Type it, unseen, then "
                "again to confirm; an empty answer clears the password from this draft."
            ),
        ),
        FieldSpec(
            key="verify_key", label="Public key",
            render=lambda d: "set" if d.get("verify_key") else "(not set)",
            prompt=_create_user_pubkey_field(),
            help="For SSH key-based login. Paste as base64, or a full 'ssh-ed25519 ...' line.",
        ),
        FieldSpec(
            key="level", label="Starting level",
            render=lambda d: str(d.get("level", 0)),
            prompt=_int_field("level", "Starting level"),
            help=(
                "The account's initial permission level. 0 is an ordinary caller; higher "
                "numbers unlock moderator/SysOp capability (see the Level field on an "
                "existing account's own detail screen for the specific thresholds)."
            ),
        ),
    ]


async def _create_user_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    draft: dict = {"username": "", "password": None, "verify_key": None, "level": 0}

    async def save(draft: dict) -> User:
        # create_user, not create_user_async -- the latter's off-loop
        # hashing split existed specifically to keep Argon2 hashing off
        # the *raw* event loop; lane.run() already dispatches this whole
        # call to a worker thread, so the plain synchronous create_user
        # (per its own docstring, "for command-line/admin callers") does
        # the hash and the write in one lane dispatch. Blank username and
        # "neither password nor key" are both rejected by create_user
        # itself (AuthError), so this closure has no validation of its
        # own to duplicate.
        try:
            new_user = await lane.run(
                create_user, draft["username"], password=draft["password"],
                verify_key=draft["verify_key"], user_level=draft["level"],
            )
        except UsernameRetiredError as exc:
            # Issue #594. The exception's own text is what a remote caller is
            # shown, and deliberately reads as "taken". A SysOp is owed the
            # reason and the way out, and raising the editor's `error_type`
            # keeps the draft open so nothing typed is lost.
            raise AuthError(exc.sysop_detail) from exc
        await lane.run(
            record_action, actor=actor, action="create_user", target_user_id=new_user.id,
            detail=f"created user {new_user.username!r} at level {draft['level']}",
        )
        # Dogfood report: three testers on modern (ANSI-capable) clients
        # never discovered in-place redraw existed, so never turned it
        # on. Every new account starts with it on, however it was made
        # (issue #840).
        await lane.run(start_new_account_redrawing_in_place, new_user)
        return new_user

    redraw_in_place, redraw_hint = await lane.run(_resolve_redraw_preference, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    new_user = await edit_resource_draft(
        session, lane,
        title="Create user",
        fields=_create_user_field_specs(),
        draft=draft, save=save, error_type=AuthError,
        save_menu_text=menu_key("C", "reate"), save_hotkey="c", back_menu_text=menu_key("B", "ack"),
        description_level=await lane.run(menu_description_level, actor),
        redraw_in_place=redraw_in_place, redraw_hint=redraw_hint,
        unicode_style=unicode_style,
        collapsed=collapsed,
        accent_color=await lane.run(effective_accent_color_256),
        header_color=await lane.run(effective_header_color_256),
    )
    if new_user is not None:
        _announce_line(session, f"Created {new_user.username!r} at level {new_user.user_level}.")
        _announce_line(session,
            colored(
                "In-place redraw is on by default for this account -- they can turn it off in Your profile.",
                fg_color=MUTED_COLOR,
            )
        )


# -- list / detail -------------------------------------------------------


# Mirrors netbbs.net.picker's own reserved-lines/max-page-size budget --
# duplicated rather than imported (that module's own private helpers
# aren't reached into from other modules, the same "duplicate rather
# than reach into another module's private helper" convention
# netbbs.link.files._file_area_from_row's own docstring already states
# for the identical reasoning).
#
# Code review follow-up (PR #216): the condensed status line (issue
# #206) added one more line to this screen's own render below --
# title/subtitle, sorted-by, showing-filter, the status line, nav, and
# the trailing "or type a 2-digit number" prompt -- without a matching
# bump here, a full page pushed the nav/choice prompt past the viewport
# on exactly the terminal heights this budget exists to protect.
_USER_PICKER_RESERVED_LINES = 8
_USER_PICKER_MAX_PAGE_SIZE = 99

# One key per sort dimension, each a live toggle between its ascending
# and descending `netbbs.auth.users.list_users` `order_by` value.
# Design doc -- Thiesi's own follow-up dogfood-testing request: pressing
# the currently-active key flips direction; pressing a different key
# switches to it, always starting ascending. Deliberately a *bespoke*
# screen, not a generic extension to netbbs.net.picker.pick_item -- that
# component is shared by boards/channels/file areas too, and this
# project's own convention (worklog) is to design a shared abstraction
# against a second real consumer, not build one on spec for a single
# caller.
_USER_SORT_MODES = {
    "a": ("Alphabetical", "alphabetical", "alphabetical_desc"),
    "r": ("Registration date", "registered", "registered_desc"),
    "l": ("Level", "level_asc", "level_desc"),
}

# A second, independent live toggle -- requested by the SysOp of the
# biggest node once real dogfood use with ~50 registered users made
# scrolling past disabled accounts to find an active one (or vice versa)
# a real friction point pagination/search alone didn't solve. A 3-state
# cycle, not a plain on/off filter, since both "I only care about active
# accounts" and "I only care about disabled ones" (e.g. reviewing who to
# actually delete) are real, equally common tasks -- one boolean can't
# express both. `[V]` always advances one step forward through the same
# fixed order; there's no direct "jump to state N" key, matching how
# `[A]`/`[R]`/`[L]` already only offer "toggle the active one" rather
# than a menu of every possible value.
_USER_VISIBILITY_MODES = ("all", "active_only", "disabled_only")
_USER_VISIBILITY_LABELS = {
    "all": "All users",
    "active_only": "Active users only (disabled hidden)",
    "disabled_only": "Disabled users only",
}


_USER_COLUMNS = [
    ListColumn("level", 5, VALUE_COLOR, align_right=True),
    # Wide enough for "pending approval", the longest `_status_label`
    # returns (Codex review). At 10 it truncated to "pending a..." on
    # the one list whose job is scanning exactly this field -- and the
    # prose row it replaced showed the whole thing. A width too narrow
    # for its own longest value is not a column, it is a riddle.
    ListColumn("status", 16, VALUE_COLOR),
]


def _user_columns(user: User) -> list[str | tuple[str, SegmentColor]]:
    """Level and status as their own columns (issue #537, design doc
    §3.6).

    `_user_description` stapled them into one grey string -- and the
    account list is the single place a SysOp most often scans for
    exactly these two fields, so it is the worst place to have left
    them as prose.
    """
    status = _status_label(user)
    return [
        str(user.user_level),
        (status, ALERT_COLOR if status == "disabled" else VALUE_COLOR),
    ]


@dataclass
class _UserListView:
    """How the account list is sorted and filtered, kept while a SysOp
    goes into an account and back out (issue #1109), so Back lands on the
    list as it was left rather than on a freshly sorted one."""

    mode: str = "a"
    descending: bool = False
    visibility: str = "all"
    # The accounts the list showed last, in order: where Back lands when
    # the account just viewed is gone (deleted from its own screen).
    shown_ids: list[int] = dataclasses.field(default_factory=list)


async def _pick_target_user(
    session: Session, lane: DatabaseLane, actor: User, *, title: str, pending_only: bool = False,
    view: _UserListView | None = None, start_stable_id: int | None = None,
) -> User | None:
    """
    The single screen every `[U]sers` submenu entry reaches a target
    account through (design doc -- Thiesi's own dogfood-testing report).

    A `pick_item` call since issue #537. It used to be a hand-rolled
    copy of one: justified when it was written, because it needed live
    sort toggles and a visibility filter and the shared picker could do
    neither -- and then quietly wrong for every month after, because the
    shared picker kept gaining things this screen could not inherit.
    Cursor navigation, `Ctrl-L`, `Ctrl-R`, `Ctrl-H`, reopening on the
    page you left, and columnar rows were all missing here and nowhere
    else. The dogfood question that started it -- "why does the user
    list have no arrow keys?" -- had that one-word answer: because it is
    not the picker.

    The capabilities that forced the fork are in `pick_item` now, and
    the four keys are still four keys. `live_keys` maps a character to a
    callback that returns a new working set, `live_nav` puts the entries
    in the nav row, and `live_label` puts the standing state in the
    trailer beside `Sort:` -- so [A]/[R]/[L] still re-sort in one press
    (the active one again flipping direction) and [V] still cycles
    all -> active-only -> disabled-only.

    Expressing them as `[O]rder`-style prompts was the obvious way to
    fit them into what `pick_item` already had, and it was the wrong
    trade: a SysOp asked for single keystrokes on a fifty-account roster
    by name, and paying for this screen's features with the thing they
    asked for is not a refactor, it is a regression with a tidy diff.

    The filter scopes search too, as it did before -- the whole
    point of hiding a class of accounts is to stop having to reach them
    until the SysOp widens the filter again.
    """
    view = view if view is not None else _UserListView()

    def _load(db: Database) -> list[User]:
        _, ascending_order, descending_order = _USER_SORT_MODES[view.mode]
        users = list_users(db, order_by=descending_order if view.descending else ascending_order)
        if pending_only:
            # The Staff console's accounts waiting for approval (issue #836).
            users = [u for u in users if u.pending_approval]
        if view.visibility == "active_only":
            users = [u for u in users if u.disabled_at is None]
        elif view.visibility == "disabled_only":
            users = [u for u in users if u.disabled_at is not None]
        view.shown_ids = [u.id for u in users]
        return users

    unicode_style = await lane.run(unicode_style_enabled, actor)
    users = await lane.run(_load)
    # Nobody to list at all ends here. A filter kept from the last visit
    # that now matches no one (the one disabled account was just
    # re-enabled) stays on the list instead, which says "No users match
    # that view." and lets [V] widen it again (#1109 review).
    if not users and view.visibility == "all":
        _announce_line(session, "\r\nNo accounts are waiting for approval." if pending_only else "\r\nNo registered users yet.")
        return None

    def _standing_label() -> str:
        """The two lines the old screen kept above its list, on one line
        in the picker's trailer -- where `sort_label` already puts
        exactly this kind of standing state, and where truncation takes
        the boilerplate before it."""
        label, _, _ = _USER_SORT_MODES[view.mode]
        # The arrow, not the word: the direction is the thing a SysOp
        # is checking at a glance, and an arrow reads at a glance.
        # ASCII gets the word, as everywhere else this screen chooses
        # between the two.
        if unicode_style:
            arrow = "↓" if view.descending else "↑"
        else:
            arrow = "desc" if view.descending else "asc"
        return (
            f"Sorted by: {label} {arrow}"
            f", Showing: {_USER_VISIBILITY_LABELS[view.visibility]}"
        )

    def _sort_key(chosen: str):
        async def toggle() -> list[User]:
            # Exactly what the top-level keys did before: the active
            # dimension pressed again reverses it, a different one
            # switches to it and starts ascending.
            if chosen == view.mode:
                view.descending = not view.descending
            else:
                view.mode, view.descending = chosen, False
            return await lane.run(_load)

        return toggle

    async def _cycle_visibility() -> list[User]:
        # One key, forward through a fixed cycle -- not a menu of every
        # value, matching how the sort keys only ever offer "toggle this
        # one" (the original screen's own reasoning, kept).
        position = _USER_VISIBILITY_MODES.index(view.visibility)
        view.visibility = _USER_VISIBILITY_MODES[(position + 1) % len(_USER_VISIBILITY_MODES)]
        return await lane.run(_load)

    async def _reload() -> list[User]:
        """Ctrl-R. The screen claims to inherit it, so it has to have it
        (Codex review) -- `pick_item` rings the bell and keeps the stale
        list when `refresh` is None, and an account list is exactly the
        thing another session changes underneath you."""
        return await lane.run(_load)

    live_keys = {key: _sort_key(key) for key in _USER_SORT_MODES}
    live_keys["v"] = _cycle_visibility
    live_nav = [
        MenuEntry(label=menu_key("A", "lphabetical"), brief="Sort by name"),
        MenuEntry(label=menu_key("R", "egistration"), brief="Sort by join date"),
        MenuEntry(label=menu_key("L", "evel"), brief="Sort by level"),
        MenuEntry(label=menu_key("V", "isibility"), brief="All/active/disabled"),
    ]

    return await pick_item(
        session, users,
        name_of=lambda user: user.username,
        stable_id_of=lambda user: user.id,
        start_stable_id=start_stable_id,
        description_of=_user_description,
        columns=_USER_COLUMNS,
        column_values_of=_user_columns,
        title=title,
        empty_message="No users match that view.",
        refresh=_reload,
        live_keys=live_keys,
        live_nav=live_nav,
        live_label=_standing_label,
        description_level=await lane.run(menu_description_level, actor),
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=unicode_style,
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
        accent_color=await lane.run(effective_accent_color_256),
        header_color=await lane.run(effective_header_color_256),
    )


def _neighbour_id(ids: list[int], gone: int) -> int | None:
    """The account after `gone` in `ids`, or the one before it when it was
    the last; `None` when it was the only one."""
    if gone not in ids:
        return None
    position = ids.index(gone)
    rest = ids[:position] + ids[position + 1:]
    if not rest:
        return None
    return rest[min(position, len(rest) - 1)]


async def _pick_and_edit_user(
    session: Session, lane: DatabaseLane, actor: User, node_controls: NodeControls | None, *, title: str,
    pending_only: bool = False,
) -> None:
    """
    Every per-user action funnels through here now (design doc -- node
    management, Thiesi's own dogfood-testing report: SysOps wanted one
    central editor rather than picking the same user again through
    three separate single-purpose screens to promote them, then disable
    them, then...). `title` is the only thing that still varies by
    which top-level `[U]sers` menu entry got here -- `[U]ser list`/
    `[P]romote/demote`/`[E]nable/disable`/`[D]elete user` all land on
    the exact same full editor once a user is actually selected, so a
    SysOp who only meant to promote someone can still also disable them
    right there without leaving and re-picking them a second time.
    """
    # Back from an account returns to the list it was picked from, on the
    # same row, sorted and filtered as it was left (issue #1109) -- not to
    # the menu above the list, which made a SysOp working down the roster
    # pick each account's list again from scratch.
    view = _UserListView()
    start_id: int | None = None
    while True:
        target = await _pick_target_user(
            session, lane, actor, title=title, pending_only=pending_only, view=view, start_stable_id=start_id,
        )
        if target is None:
            return
        shown = list(view.shown_ids)
        await _user_detail_screen(session, lane, actor, target, node_controls)
        start_id = target.id
        if await lane.run(get_user_by_id, target.id) is None:
            # Deleted from its own screen: land on the account that took
            # its place in the list, not back at the top (#1109 review).
            start_id = _neighbour_id(shown, target.id)


def _status_label(user: User) -> str:
    if user.disabled_at is not None:
        return "disabled"
    if user.pending_approval:
        return "pending approval"
    return "active"


def _user_description(user: User) -> str:
    return f"level {user.user_level}, {_status_label(user)}"


def _user_detail_field_line(hotkey: str, label: str, value: str, *, selected: str | None, accent: int) -> str:
    """Dogfood feature request, issue #160's cursor-navigation follow-up
    (item 1 of the prioritized list): the same `>`-cursor/accent-color
    highlight convention `netbbs.net.resource_editor.edit_resource_draft`
    already renders its own fields with -- duplicated rather than
    imported, since this screen is a bespoke dispatch loop (below), not
    a draft-editor call. One `colored()` call per side, same reasoning
    as that module's own field-row rendering: a marker/label split
    across two `colored()` calls would insert an SGR reset between them,
    breaking what should read as one contiguous highlighted run."""
    prefix = (
        colored(f"> {label}", fg_color=accent, bold=True)
        if selected == hotkey
        else colored(f"  {label}", fg_color=LABEL_COLOR)
    )
    return f"{prefix}: {colored(value, fg_color=MUTED_COLOR)}"


async def _draw_user_detail(
    session: Session, lane: DatabaseLane, target: User, description_level: str, redraw_in_place: bool,
    unicode_style: bool,
    collapsed: bool,
    *,
    selected: str | None = None,
    allowed: frozenset[str] | None = None,
    layout: dict[str, Any] | None = None,
) -> bool:
    """Returns whether `target` is currently on the local blocklist --
    unlike `disabled_at`, blocked status isn't a field on `User` itself,
    so `_user_detail_screen`'s dispatch loop needs it back to know
    which of `[R]estrict`'s two directions a confirmation should offer,
    same shape `_render_profile` already uses to
    hand a caller-needed piece of drawn state back to its own dispatch
    loop.

    `selected` (issue #160's cursor-navigation follow-up), if given one
    of this screen's five field hotkeys (`l`/`t`/`i`/`k`/`r`), highlights
    that line's own `>` cursor -- `None` (nothing arrow-highlighted yet,
    or a `[A]pprove`/`[D]elete`/`[B]ack` action, none of which are
    arrow-selectable fields, matching `edit_resource_draft`'s own
    Save/Back convention) renders identically to before this feature.

    `allowed` (issue #836) narrows the menu to what a staff member may do
    (`_user_detail_keys`); `None` is the SysOp's full screen.

    `layout`, if given, receives where the screen put what an in-place edit
    needs: `fields` maps each editable field's key to its value's 1-based
    screen row, 0-based column and rows, `prompt_row` is the `Choice:` row,
    and `fits` says whether the screen fitted the terminal, without which
    rows counted from the top are not where anything is."""
    title = screen_title(sanitize_text(target.username),
        breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
        header_color=await lane.run(effective_header_color_256), node_name_gradient=session.node_name_gradient)
    await session.write_line("\r\n" + title)
    accent = await lane.run(effective_accent_color_256)
    editable: dict[str, Field] = {}

    def _editable(hotkey: str, label: str, value: str, *, color: int = VALUE_COLOR) -> Field:
        editable[hotkey] = Field(label, value, color=color, selected=selected == hotkey, accent=accent)
        return editable[hotkey]

    display_format, display_timezone = await lane.run(resolve_display_preferences)
    member_since = format_for_display(
        target.created_at, override_format=display_format, override_timezone=display_timezone
    )
    status = _status_label(target)
    # Dogfood follow-up (`netbbs.moderation.blocklist`): the local
    # blocklist enforcement path was real and already wired into login
    # (`netbbs.net.login_flow`'s own distinct "Your access to this
    # system has been revoked." message), but nothing in the
    # interactive product could ever create an entry -- only a
    # dev/admin script (`scripts/block_user.py`) could. `[T]oggle
    # enable/disabled` already covers "stop this local account from
    # logging in" for most purposes; this is additionally fingerprint-
    # based when the account has a keypair, the form the same
    # mechanism is designed to extend to remote nodes/traffic later
    # (module docstring) -- a real, separate capability, not just a
    # second button for the same thing.
    blocked = await lane.run(is_blocked, target)
    entries = await lane.run(list_actions_for_target_user, target.id)
    age_verified = await lane.run(get_attestation, target, "age") is not None
    name_verified = await lane.run(get_attestation, target, "name") is not None
    # Issue #1119: every field the cursor walks is in one block, top to
    # bottom in `_USER_DETAIL_FIELD_ORDER`, and what can't be changed here
    # follows it. Pairing the account's fields side by side made the cursor
    # zigzag between columns, stepping over read-only rows. One section, not
    # three: their headings cost the rows that let the screen still fit 24
    # now that the fields are one to a row. The account's record continues
    # the block, in its own muted colors, with no heading or blank row of its
    # own: those two rows are what let the key bar line its hotkeys up at
    # 80x24 (maintainer request, 2026-10-09).
    sections = [
        Section("Account", [
            _editable("l", "Level", level_label(target.user_level, await lane.run(get_level_names))),
            _editable("t", "Status", status, color=SUCCESS_COLOR if status == "active" else WARNING_COLOR),
            _editable(
                "r", "Blocked", "yes (local blocklist)" if blocked else "no",
                color=ERROR_COLOR if blocked else VALUE_COLOR,
            ),
            # Issue #1110: the caller's own name and birthdate, which a SysOp
            # or account manager can correct. Shown only to whoever may edit
            # them (review on #1112): a birthdate is private, and an approver
            # reviewing a signup has no need for it.
            *(_detail_fields(
                await lane.run(get_display_name, target),
                await lane.run(get_birthdate, target),
                age_verified=age_verified,
                name_verified=name_verified,
                field=_editable,
            ) if allowed is None or "w" in allowed else ()),
            # Issue #611: whether a password exists, never the password. Beside
            # the key line because the two together are "how this account gets
            # in", and a SysOp resetting one wants to see the other.
            _editable(
                "k", "Public key", target.fingerprint if target.fingerprint else "(none)",
                color=VALUE_COLOR if target.fingerprint else MUTED_COLOR,
            ),
            _editable(
                "p", "Password",
                "set" if await lane.run(has_password, target) else "(none -- key login only)",
            ),
            # Design doc §5.6 (issue #836, F132): what this account may do
            # beyond its level -- the staff permissions, and the
            # verify-identity permission (§5.5) beside them but separate.
            _editable(
                "s", "Staff", describe_staff_permissions(target.staff_permissions),
                color=VALUE_COLOR if target.staff_permissions else MUTED_COLOR,
            ),
            _editable("i", "Can verify identity", _can_verify_label(target)),
            # Issue #992: last, so the arrow order of the fields above holds.
            _editable("u", "Auto promotion", _auto_promotion_label(target, await lane.run(kept_from_rules, target))),
        ]),
    ]
    # The account's record: nothing here is changed on this screen. The
    # admin-action list itself is a screen of its own (`[H]istory`); ten
    # rows of it here pushed the account's fields off a 24-row terminal.
    sections.append(Section(None, [
        Field("Member since", member_since, color=METADATA_COLOR),
        Field(
            "Admin actions", f"{len(entries)} recorded" if entries else "none recorded",
            color=VALUE_COLOR if entries else MUTED_COLOR,
        ),
        _grants_field(await lane.run(_grant_summaries, target)),
    ], paired=True))
    # Issue #835 (F072): what the caller said when signing up, for whoever
    # decides on the account. Typed by an unauthenticated caller, so
    # sanitized like any other remote text. Read-only, so below the fields.
    signup_answer = await lane.run(load_signup_answer, target.id) if target.pending_approval else None
    if signup_answer is not None:
        sections.append(Section("Signup answer", [
            Note(f"Asked: {sanitize_text(signup_answer.question)}"),
            Note(f"Answer: {sanitize_text(signup_answer.answer)}"),
        ]))
    places: dict[int, tuple[int, int, int]] = {}
    panel_rows = await _write_sections(session, sections, unicode_style=unicode_style, joined=1, places=places)
    offered = allowed if allowed is not None else _ALL_USER_DETAIL_KEYS
    options = []
    if target.pending_approval and "a" in offered:
        options.append(MenuEntry(label=menu_key("A", "pprove"), brief="Approve this pending signup"))
    if "l" in offered:
        options.append(MenuEntry(label=menu_key("L", "evel"), brief="Change this user's access level"))
    if "u" in offered:
        options.append(MenuEntry(label=menu_key("U", "se promotion rules"), brief="Promotion rules on or off"))
    if "t" in offered:
        options.append(MenuEntry(label=menu_key("T", "oggle enable/disabled"), brief="Enable or disable this account"))
    if "n" in offered:
        options.append(MenuEntry(label=menu_key("N", "ame"), brief="Correct or clear the display name"))
    if "w" in offered:
        options.append(MenuEntry(label=menu_key("W", "hen born"), brief="Correct or clear the birthdate"))
    if "e" in offered:
        options.append(MenuEntry(label=menu_key("E", "dit profile"), brief="Their bio, blocks and settings"))
    if "s" in offered:
        options.append(MenuEntry(label=menu_key("S", "taff"), brief="Staff permissions, Co-SysOp preset"))
    if "i" in offered:
        options.append(MenuEntry(label=menu_key("I", "dentity verification"), brief="Grant/revoke attestation rights"))
    # Issue #1115: only while there is a verification to withdraw.
    if "v" in offered and (age_verified or name_verified):
        options.append(MenuEntry(label=menu_key("V", "erification: revoke"), brief="Withdraw a verified age or name"))
    if "k" in offered:
        options.append(MenuEntry(label=menu_key("K", "ey"), brief="View/replace this user's SSH key"))
    if "p" in offered:
        options.append(MenuEntry(label=menu_key("P", "assword"), brief="Set or clear this user's password"))
    if "r" in offered:
        options.append(MenuEntry(label=menu_key("R", "estrict login"), brief="Block or unblock this account"))
    options.append(MenuEntry(label=menu_key("H", "istory"), brief="Admin actions on this account"))
    if target.pending_approval and "d" in offered:
        # Issue #835: turning down a signup is routine, and used to need
        # the full permanent-delete warning and typed-name confirmation.
        options.append(MenuEntry(label=menu_key("D", "ecline"), brief="Turn down this signup"))
    elif "d" in offered:
        options.append(MenuEntry(label=menu_key("D", "elete"), brief="Permanently remove this user"))
    options.append(MenuEntry(label=menu_key("B", "ack"), brief="Return to the picker"))
    # Above the panel: the title and its rule, and -- unless the screen was
    # cleared, which takes it -- the blank row the title is written after.
    # Below it: the blank row before the menu and the help hint.
    chrome_rows = 4 if redraw_in_place else 5
    menu = _fitted_menu(options, description_level, session=session, used_rows=panel_rows + chrome_rows)
    await session.write_line("\r\n" + menu)
    await session.write_line(colored(f"({help_key_label(session)} for help on these fields)", fg_color=MUTED_COLOR))
    if layout is not None:
        # Counted from the top of a cleared screen: the title, the panel, the
        # blank row and the menu, the help hint, any carried outcome, then
        # `Choice:`. Every row is drawn to fit the width, so none wraps.
        title_rows = title.count("\r\n") + 1
        prompt_row = title_rows + panel_rows + 1 + menu.count("\r\n") + 1 + 1 + _pending_notice_rows(session) + 1
        layout["fields"] = {
            key: (title_rows + places[id(field)][0] + 1, places[id(field)][1], places[id(field)][2])
            for key, field in editable.items() if id(field) in places
        }
        layout["prompt_row"] = prompt_row
        layout["fits"] = redraw_in_place and prompt_row <= session.terminal_height
    await _choice_prompt(session)
    return blocked


_USER_DETAIL_FIELD_ORDER = ("l", "t", "r", "n", "w", "k", "p", "s", "i", "u")

#: Every action key on the account detail -- what a SysOp gets.
_ALL_USER_DETAIL_KEYS = frozenset("alutrnwkpsihdve")


def _auto_promotion_label(target: User, kept: str | None) -> str:
    """Whether the promotion rules (issue #992) may raise this account. The
    on/off is the switch `[U]se promotion rules` flips (a level set by hand turns
    it off); anything else keeping the rules away is said beside it, since
    the switch does not change it."""
    if target.level_set_by_hand:
        return "off (level set by hand)"
    return "on" if kept is None else f"on, but skipped ({kept})"


def _user_detail_keys(actor: User, target: User, *, guest: bool = False) -> frozenset[str]:
    """
    The account-detail actions `actor` is offered on `target` (design doc
    §5.6). A SysOp gets all of them. A staff member gets `[H]istory`, and
    -- only on an account below 255 holding no staff permission -- level,
    enable/disable, password, display name, birthdate (issue #1110) and the
    account's Profile with manage accounts, approve and decline
    with approve accounts. Never keys, the blocklist, staff, identity
    verification, or deleting an account.

    `[E]dit profile` is never offered on your own account, whose Profile is
    on the main menu. On the guest account (`guest`) it sets what every guest
    starts with, which is the whole node's, so only a SysOp is offered it.

    Presentation only: the mutators check again against the database, so
    a permission revoked while this screen is open refuses the action.
    """
    profile = set() if actor.id == target.id else {"e"}
    if is_usable_sysop(actor):
        return _ALL_USER_DETAIL_KEYS - ({"e"} - profile)
    keys = {"h"}
    within_reach = target.user_level < SYSOP_LEVEL and not target.staff_permissions
    if within_reach and actor.has_staff(StaffPermission.MANAGE_ACCOUNTS):
        keys |= {"l", "u", "t", "p", "n", "w"} | (set() if guest else profile)
    if within_reach and target.pending_approval and actor.has_staff(StaffPermission.APPROVE_ACCOUNTS):
        keys |= {"a", "d"}
    return frozenset(keys)


def _detail_fields(
    display_name: str | None,
    birthdate: datetime.date | None,
    *,
    age_verified: bool,
    name_verified: bool,
    field: Callable[..., Field],
) -> list[Field]:
    """The account detail's display name and birthdate (issue #1110), the
    caller's own fields. A verification on record is said in the value,
    since an edit here never changes it and it still decides the gates.
    The display name can come from the caller, so it is sanitized like any
    other text they typed."""
    name = sanitize_text(display_name) if display_name else "(not set)"
    born = birthdate.isoformat() if birthdate else "(not set)"
    return [
        field(
            "n", "Display name", name + ("; real name verified" if name_verified else ""),
            color=VALUE_COLOR if display_name else MUTED_COLOR,
        ),
        field(
            "w", "Birthdate", born + ("; age verified" if age_verified else ""),
            color=VALUE_COLOR if birthdate else MUTED_COLOR,
        ),
    ]


def _grant_summaries(db: Database, target: User) -> list[str]:
    """`target`'s moderator grants in words (`describe_grant`)."""
    return [describe_grant(db, grant) for grant in list_grants_for_user(db, target)]


def _grants_field(summaries: list[str]) -> Field:
    """The account detail's moderator-grant line: how many, and each one
    beneath it. Board and Community names can come from other nodes over
    Link, so they are sanitized like any remote text."""
    if not summaries:
        return Field("Moderator grants", "none", color=MUTED_COLOR)
    return Field(
        "Moderator grants", str(len(summaries)),
        note="; ".join(sanitize_text(summary) for summary in summaries),
    )
async def _user_history_screen(session: Session, lane: DatabaseLane, actor: User, target: User) -> None:
    """Every recorded admin action against `target`, newest first, paged.

    Dogfood follow-up: this list used to show *what* happened but never
    *who* did it, even though `actor_user_id` is stored for exactly this
    (`netbbs.moderation.log.ModerationLogEntry`'s own docstring: nullable
    specifically so a deleted actor's audit trail survives, implying
    display was always the intent) -- the in-channel notice for the same
    action already says "by bob," this screen just never repeated it.

    It was the bottom ten rows of the user detail screen, under the
    account's own fields, and on a 24-row terminal pushed them off the
    top. A screen of its own, it shows all of them."""
    entries = list(reversed(await lane.run(list_actions_for_target_user, target.id)))
    display_format, display_timezone = await lane.run(resolve_display_preferences)
    actor_ids = {entry.actor_user_id for entry in entries if entry.actor_user_id is not None}
    actor_usernames: dict[int, str] = {}
    for actor_id in actor_ids:
        found = await lane.run(get_user_by_id, actor_id)
        actor_usernames[actor_id] = found.username if found is not None else "(deleted account)"

    rows = []
    for entry in entries:
        when = format_for_display(
            entry.created_at, override_format=display_format, override_timezone=display_timezone
        )
        by = actor_usernames.get(entry.actor_user_id, "(unknown)") if entry.actor_user_id is not None else "(system)"
        rows.append([(when, DATE_COLOR), entry.action, (by, AUTHOR_COLOR), (entry.detail or "", METADATA_COLOR)])
    body: list[Field | Note | Table] = (
        [Table(("When", "Action", "By", "Detail"), rows, flex=3)] if rows
        else [Note("No recorded admin actions.")]
    )
    await _show_report(
        session, lane, actor, "Admin actions",
        sections=[Section(None, body)],
        help_title="Admin actions help",
        help_about="Every administrative action taken on this account, newest first.",
    )


async def _read_user_detail_key(session: Session) -> tuple[EditorKey, bool]:
    """`netbbs.net.resource_editor._read_navigable_key`'s own fallback
    shape, duplicated here rather than imported -- this project's own
    "duplicate rather than reach into another module's private helper"
    convention (see `netbbs.link.files._file_area_from_row`'s own
    docstring for the identical reasoning), since this screen is a
    bespoke dispatch loop (below), not an `edit_resource_draft` caller:
    `[A]pprove` (conditional) and `[D]elete` (which ends the whole
    screen mid-field, not just mutates one) don't fit that shared
    driver's fields-plus-save-plus-back shape, so this screen keeps its
    own hand-rolled loop and borrows only the cursor-navigation
    mechanics, matching this same file's own `_USER_SORT_MODES`
    precedent for when a bespoke screen beats a generic extension.

    `distinguish_ctrl_h=True` (dogfood feature request: this screen had
    no on-demand help at all until now) -- without it, real byte 0x08
    collapses into `BACKSPACE`, unreachable as help the same way
    `edit_resource_draft`'s own Ctrl-H was before that bug was found and
    fixed (see that module's `_read_navigable_key` docstring). This
    screen never needs a real Backspace at its own top level either.

    The flag says whether the key was echoed (only `read_key` echoes), so
    a rejected key erases only a character that is really on screen."""
    read_editor_key = getattr(session, "read_editor_key", None)
    if read_editor_key is not None:
        try:
            return await read_editor_key(distinguish_ctrl_h=True), False
        except NotImplementedError:
            pass
    raw = await session.read_key()
    return EditorKey(EditorKeyKind.CHAR, char=raw), True


# Ctrl-H's own content for the five arrow-selectable fields
# (`_USER_DETAIL_FIELD_ORDER`) -- dogfood feature request, this screen
# had no on-demand help at all until now. Keyed the same as
# `_USER_DETAIL_FIELD_ORDER`, not a `FieldSpec` list, since this
# screen's status lines are drawn by `_draw_user_detail` directly, not
# through `netbbs.net.resource_editor`.
_USER_DETAIL_HELP: dict[str, tuple[str, str]] = {
    "n": (
        "Display name",
        "The name this caller chose for themselves (Your profile > Name & details). "
        "Correct or clear it here, for a typo or a name that has to go; blank clears it. "
        "The same rules as the caller's own apply, and the change is recorded in the "
        "account's history. A verified real name is a separate record and is not changed.",
    ),
    "w": (
        "Birthdate",
        "The birthdate this caller entered themselves, which age gates use when there is "
        "no verified age. Correct or clear it here; blank clears it. The history records "
        "that it changed, never the date. A verified age is a separate record, is not "
        "changed here, and still decides every age gate.",
    ),
    "u": (
        "Auto promotion",
        "Whether the node's promotion rules (Users > Auto-promotion rules) may raise this "
        "account at its next login. Setting its level by hand turns them off, so a "
        "demotion is not undone; this turns them back on, or off. They never apply to "
        "the guest account, staff, SysOps, or pending and disabled accounts.",
    ),
    "l": (
        "Level",
        "The account's permission level. 0 is an ordinary caller; higher numbers unlock "
        "moderator/SysOp capability.",
    ),
    "t": (
        "Status",
        "Enable/disable this account. A disabled account can't log in; existing posts/"
        "files/messages they created are untouched.",
    ),
    "s": (
        "Staff",
        "Staff permissions: approve accounts, manage accounts (disable/"
        "enable, password reset, display name and birthdate, levels up to 254) and "
        "moderate everything. Co-SysOp sets "
        "all three. A staff member never acts on level 255 or on other staff, and can't "
        "grant anything.",
    ),
    "i": (
        "Can verify identity",
        "Lets this account confirm other callers' age or real name. It is separate "
        "from moderator grants and staff permissions.",
    ),
    "k": (
        "Public key",
        "This account's SSH/Link public key. Setting or replacing it here lets them log "
        "in over SSH with key-based authentication.",
    ),
    "p": (
        "Password",
        "Whether this account has a password. Opens a screen to set a new one (the old "
        "one is never shown or recovered) or, if the account also has an SSH key, to "
        "remove the password so it signs in by key only. Every change is audit-logged "
        "with your name.",
    ),
    "r": (
        "Blocked",
        "The local blocklist -- a separate, fingerprint-based mechanism from Status/"
        "disable, designed to extend to remote nodes/traffic later. Unlike disabling, a "
        "block also survives the account being re-enabled.",
    ),
    "e": (
        "Edit profile",
        "Opens this member's own Profile screen, acting on their account: bio, "
        "signature, location, who they block, and their display, mail and chat "
        "settings. Each change is recorded in the account's history; private text such "
        "as the bio, and who they block, is recorded as changed, never quoted. Whether a "
        "verification is shared over Link stays their own choice. On the guest account "
        "it sets the guest defaults, what every guest starts with; only a SysOp can.",
    ),
}


async def _edit_account_detail(
    session: Session, lane: DatabaseLane, actor: User, target: User, choice: str,
    *, run_read: Callable[[Callable[[], Awaitable[str]]], Awaitable[str]] | None = None,
) -> None:
    """Issue #1110: correct or clear `target`'s display name (`n`) or
    birthdate (`w`). The value opens in the line, as the level does; blank
    clears it, Esc keeps it.

    `run_read` runs the prompt and read: the account detail's own puts them
    on the value where it is drawn (`_user_detail_screen`). Without one they
    go below the screen's prompt."""
    if choice == "n":
        current = await lane.run(get_display_name, target) or ""
        label = f"Display name for {target.username!r} (blank clears it, {_EDIT_HINT}):"
    else:
        stored = await lane.run(get_birthdate, target)
        current = stored.isoformat() if stored else ""
        label = f"Birthdate for {target.username!r} as YYYY-MM-DD (blank clears it, {_EDIT_HINT}):"
    async def read() -> str:
        await write_field_prompt(session, colored(label, fg_color=MUTED_COLOR), hint=_EDIT_HINT)
        return (await _read_seeded_line(session, initial=current)).strip()

    try:
        raw = await (run_read or _read_below_prompt(session))(read)
    except InputCancelled:
        return
    if raw == current:
        return
    try:
        if choice == "n":
            changed = await lane.run(change_display_name, target, raw or None, changed_by=actor)
            what = f"Display name for {target.username!r} " + (f"is now {raw!r}." if raw else "cleared.")
        else:
            try:
                new_birthdate = datetime.date.fromisoformat(raw) if raw else None
            except ValueError:
                _announce_line(session, colored("Not a valid date (expected YYYY-MM-DD) -- unchanged.", fg_color=MUTED_COLOR))
                return
            changed = await lane.run(change_birthdate, target, new_birthdate, changed_by=actor)
            what = f"Birthdate for {target.username!r} " + ("updated." if raw else "cleared.")
    except (ProfileFieldError, UserManagementError) as exc:
        _announce_line(session, colored(f"Not changed: {exc}", fg_color=MUTED_COLOR))
        return
    if changed:
        _announce_line(session, what)


def _read_below_prompt(session: Session) -> Callable[[Callable[[], Awaitable[str]]], Awaitable[str]]:
    """A field's prompt and read on the rows below the screen's `Choice:`."""

    async def run(read: Callable[[], Awaitable[str]]) -> str:
        await session.write_line("")
        return await read()

    return run


async def _revoke_verification(session: Session, lane: DatabaseLane, actor: User, target: User) -> None:
    """Issue #1115: withdraw `target`'s verified age or real name. Clearing
    the self-entered birthdate or display name (`n`, `w`) never does this,
    so it is an action of its own, confirmed and recorded. With both on
    record, the SysOp picks one first."""
    age = await lane.run(get_attestation, target, "age")
    name = await lane.run(get_attestation, target, "name")
    if age is None and name is None:
        _announce_line(session, f"No verification on record for {target.username!r}.")
        return
    if age is not None and name is not None:
        while True:
            await write_prompt(
                session, highlight_hotkeys("Revoke which: verified [A]ge, verified real [N]ame, or [B]ack? ")
            )
            which = (await session.read_key()).lower()
            await session.write_line("")
            if which != HELP_KEY:
                break
            await show_menu_help(
                session, "Revoke verification help",
                [MenuEntry(label=menu_key("A", "ge"), brief="Withdraw the verified age"),
                 MenuEntry(label=menu_key("N", "ame"), brief="Withdraw the verified real name")],
                about="Both an age and a real name are verified for this account; choose which to "
                      "withdraw. You are asked to confirm before anything changes.",
                header_color=await lane.run(effective_header_color_256),
                unicode_style=await lane.run(unicode_style_enabled, actor),
            )
        if which not in ("a", "n"):
            return
        attribute = "age" if which == "a" else "name"
    else:
        attribute = "age" if age is not None else "name"
    label = "age" if attribute == "age" else "real name"
    shared = (age if attribute == "age" else name).link_visible
    if not await prompt_yes_no(
        session,
        f"Revoke the verified {label} of {target.username!r}? Anything that asks for a "
        f"verified {label} will refuse them again.",
        default=False,
    ):
        return
    try:
        revoked = await lane.run(revoke_attestation, target, attribute, actor=actor)
    except AttestationError as exc:
        _announce(session, f"Not revoked: {exc}", error=True)
        return
    if revoked:
        extra = " The nodes it was shared with are told at the next sync." if shared else ""
        _announce_line(session, f"Revoked the verified {label} of {target.username!r}.{extra}")
    else:
        # Someone else revoked it while this screen waited for an answer.
        _announce_line(session, f"No verified {label} on record for {target.username!r} any more.")


async def _show_user_detail_help(
    session: Session, lane: DatabaseLane, *, selected: str | None, unicode_style: bool = False
) -> None:
    """Same "narrow to the highlighted field if one is selected, else
    list everything" shape `netbbs.net.resource_editor._show_field_help`
    already establishes for `edit_resource_draft`'s own Ctrl-H."""
    header_color = await lane.run(effective_header_color_256)
    if selected is not None:
        label, help_text = _USER_DETAIL_HELP[selected]
        await show_help(
            session, "Field help", [colored(label, fg_color=header_color, bold=True), f"  {help_text}"],
            header_color=header_color, unicode_style=unicode_style,
        )
        return
    lines: list[str] = []
    for label, help_text in _USER_DETAIL_HELP.values():
        lines.append(colored(label, fg_color=header_color, bold=True))
        lines.append(f"  {help_text}")
        lines.append("")
    await show_help(session, "Field help", lines[:-1], header_color=header_color, unicode_style=unicode_style)


def _gate_row(gate: Gate, extra: str | None = None) -> tuple[str, str, str]:
    """`(action, name, what)` cells for one gate in a level-change preview."""
    action, what = GATE_WORDS[gate.kind]
    name = gate.name + (" (hidden)" if gate.hidden else "")
    remarks = [text for text in (extra, gate.note) if text]
    return action, name, "; ".join([what, *remarks])


def _level_change_sections(change: AccountChange) -> list[Section]:
    sections = []
    for title, gates in (("Gains", change.gained), ("Loses", change.lost)):
        rows = [_gate_row(gate) for gate in gates]
        sections.append(Section(title, [Table(("", "Name", "What"), rows, flex=1)] if rows else [Note("Nothing.")]))
    if change.blocked:
        rows = [_gate_row(gate, "needs " + ", ".join(fails)) for gate, fails in change.blocked]
        sections.append(Section("Still blocked", [
            Note("The new level opens these, but another gate keeps this account out."),
            Table(("", "Name", "What"), rows, flex=1),
        ]))
    return sections


async def _preview_level_change(
    session: Session, lane: DatabaseLane, actor: User, target: User, change: AccountChange
) -> bool:
    """Design doc §5.7, issue #1006: what a level change opens and closes for
    this account, shown before it is made. Returns whether the SysOp applied
    it; `[B]ack` leaves the level as it was."""
    chrome = await _load_chrome(lane, actor)
    names = await lane.run(get_level_names)
    choice, _page = await show_detail(
        session,
        title=_detail_title(
            session, chrome,
            f"Level {level_label(change.old_level, names)} → {level_label(change.new_level, names)}",
            subtitle=f"What changes for {target.username} by level. Grants and other gates are counted.",
        ),
        sections=_level_change_sections(change),
        actions=[("a", menu_key("A", "pply")), _BACK_ACTION],
        redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
        help_title="Level change help",
        help_about="What this account gains and loses by moving to the new level. Nothing changes until you confirm.",
    )
    return choice == "a"


async def _change_user_level(
    session: Session, lane: DatabaseLane, actor: User, target: User, new_level: int,
    node_controls: NodeControls | None,
) -> User:
    """Preview, then apply, a level change from the user detail screen.
    A change that opens and closes nothing for the account is applied
    without the preview; the outcome line says so. Returns the account as
    it now stands."""
    try:
        # Refused before it is previewed, not after the SysOp read the preview.
        await lane.run(check_user_level_change, target, new_level, changed_by=actor)
    except UserManagementError as exc:
        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
        return target
    change = await lane.run(account_level_change, target, new_level)
    if not change.changes_nothing and not await _preview_level_change(session, lane, actor, target, change):
        _announce_line(session, colored(f"{target.username!r} stays at level {target.user_level}.", fg_color=MUTED_COLOR))
        return target
    try:
        updated = await lane.run(set_user_level, target, new_level, changed_by=actor)
    except UserManagementError as exc:
        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
        return target
    outcome = f"{updated.username!r} is now level {updated.user_level}."
    if change.changes_nothing:
        outcome += " That opens and closes nothing for them."
    _announce_line(session, outcome)
    _request_live_access_recheck(node_controls, updated)
    return updated


async def _user_detail_screen(
    session: Session, lane: DatabaseLane, actor: User, target: User, node_controls: NodeControls | None
) -> None:
    """
    The single per-user action screen every `[U]sers` submenu entry
    lands on now (design doc -- node management, Thiesi's own dogfood-
    testing report), mirroring the board/channel/file-area admin
    screens' own established "draw status, dispatch a lettered action,
    redraw" shape rather than the linear one-pass-of-prompts this used
    to be. A SysOp can now promote, then disable, then delete the exact
    same already-selected account without leaving this screen or
    re-picking them through three separate single-purpose flows.

    Dogfood feature request, issue #160's cursor-navigation follow-up
    (item 1 of the prioritized list): the five status lines
    (`_USER_DETAIL_FIELD_ORDER`) are also reachable by moving a `>`
    cursor with Up/Down and activating the highlighted one with Space
    or Enter -- purely additive, every hotkey letter keeps working
    exactly as before. `[A]pprove`/`[D]elete`/`[B]ack` are never
    arrow-selectable, the same "always hotkey-only" treatment
    `edit_resource_draft` already gives its own Save/Back.
    """
    with entered(session, sanitize_text(target.username)):
        description_level = await lane.run(menu_description_level, actor)
        unicode_style = await lane.run(unicode_style_enabled, actor)
        collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
        redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
        selected: str | None = None
        target_is_guest = await lane.run(is_guest_account, target)

        async def _redraw(layout: dict[str, Any] | None = None) -> bool:
            # Recomputed on every draw: an approval or a level change can move
            # the account in or out of a staff member's reach.
            nonlocal allowed, field_order
            allowed = _user_detail_keys(actor, target, guest=target_is_guest)
            field_order = tuple(key for key in _USER_DETAIL_FIELD_ORDER if key in allowed)
            return await _draw_user_detail(
                session, lane, target, description_level, redraw_in_place, unicode_style, collapsed,
                selected=selected, allowed=allowed if allowed != _ALL_USER_DETAIL_KEYS else None, layout=layout,
            )

        def _edit_in_place(hotkey: str) -> Callable[[Callable[[], Awaitable[str]]], Awaitable[str]]:
            """A typed field's prompt and read, on its value where the screen draws
            it: redrawn with the field highlighted, the hint on the `Choice:` row
            and the cursor in the value, as on every Create/Edit screen. A screen
            that scrolls, or does not fit, reads below its prompt as before."""

            async def run(read: Callable[[], Awaitable[str]]) -> str:
                nonlocal blocked, selected
                if redraw_in_place:
                    if hotkey in field_order:
                        selected = hotkey
                    layout: dict[str, Any] = {}
                    blocked = await _redraw(layout)
                    place = layout["fields"].get(hotkey) if layout.get("fits") else None
                    if place is not None:
                        row, column, rows = place
                        with editing_in_place(
                            session, row=row, column=column, rows=rows, prompt_row=layout["prompt_row"],
                        ) as position:
                            try:
                                return await read()
                            finally:
                                # A resize cancels the edit and says so on the next draw.
                                if position.message:
                                    _announce_line(session, colored(position.message, fg_color=MUTED_COLOR))
                await session.write_line("")
                return await read()

            return run

        allowed: frozenset[str] = _ALL_USER_DETAIL_KEYS
        field_order: tuple[str, ...] = _USER_DETAIL_FIELD_ORDER
        blocked = await _redraw()
        while True:
            key, echoed = await _read_user_detail_key(session)

            if key.kind == EditorKeyKind.UP:
                if not field_order:
                    continue
                index = field_order.index(selected) if selected in field_order else 0
                selected = field_order[(index - 1) % len(field_order)]
                blocked = await _redraw()
                continue
            if key.kind == EditorKeyKind.DOWN:
                if not field_order:
                    continue
                index = field_order.index(selected) if selected in field_order else -1
                selected = field_order[(index + 1) % len(field_order)]
                blocked = await _redraw()
                continue
            if key.kind == EditorKeyKind.ESCAPE:
                if selected is not None:
                    selected = None
                    blocked = await _redraw()
                    continue
                await session.write("\a")
                continue
            if key.kind == EditorKeyKind.CTRL and key.char == "h":
                await _show_user_detail_help(session, lane, selected=selected, unicode_style=unicode_style)
                blocked = await _redraw()
                continue
            if key.kind == EditorKeyKind.ENTER or (key.kind == EditorKeyKind.CHAR and key.char == " "):
                if selected is None:
                    await session.write("\a")
                    continue
                choice = selected
            elif key.kind == EditorKeyKind.CHAR and key.char is not None:
                choice = key.char.lower()
                if choice == HELP_KEY:
                    # A session with no real `read_editor_key` (falls back
                    # to plain `read_key()`) delivers Ctrl-H as an ordinary
                    # character, never as `EditorKeyKind.CTRL` -- same dual
                    # path `edit_resource_draft` itself handles.
                    await _show_user_detail_help(session, lane, selected=selected, unicode_style=unicode_style)
                    blocked = await _redraw()
                    continue
                if choice in field_order:
                    selected = choice
            else:
                # Left/Right/Backspace/Tab/Home/End/Page Up/Page Down --
                # nothing on this screen defines a step, same silent no-op
                # `edit_resource_draft` gives Left/Right on a step-less field.
                continue

            if choice == "b":
                await session.write_line("")
                return
            elif choice not in allowed:
                # Design doc §5.6: a staff member sees the account, but only the
                # actions their permissions cover, and none on a SysOp or
                # another staff member.
                await session.write(reject_unhandled_key(choice) if echoed else "\a")
            elif choice == "a" and target.pending_approval:
                await session.write_line("")
                if await prompt_yes_no(session, "Approve this account so it can log in?", default=False):
                    try:
                        target = await lane.run(approve_pending_user, target, approved_by=actor)
                    except UserManagementError as exc:
                        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                    else:
                        _announce_line(session, f"{target.username!r} approved.")
                blocked = await _redraw()
            elif choice == "l":
                # One style for a number (issue #845, F136): the value opens in
                # the line to edit, as on every Create/Edit screen, instead of a
                # `[current]` default beside an empty line -- and, with the screen
                # redrawn in place, on the level itself.
                async def _read_level() -> str:
                    await write_field_prompt(
                        session,
                        colored(f"New level for {target.username!r} ({_EDIT_HINT}):", fg_color=MUTED_COLOR),
                        hint=_EDIT_HINT,
                    )
                    return (await _read_seeded_line(session, initial=str(target.user_level))).strip()

                try:
                    raw = await _edit_in_place("l")(_read_level)
                except InputCancelled:
                    raw = ""
                if raw and raw != str(target.user_level):
                    # A level's name works as well as its number (issue #1009).
                    new_level = parse_level(raw, await lane.run(get_level_names))
                    if new_level is None:
                        _announce_line(session, colored("Not a level or a level's name -- cancelled.", fg_color=MUTED_COLOR))
                    elif new_level != target.user_level:
                        target = await _change_user_level(session, lane, actor, target, new_level, node_controls)
                blocked = await _redraw()
            elif choice == "u":
                # A toggle toggles (design doc §3.5): nothing to confirm.
                try:
                    target = await lane.run(
                        set_automatic_promotion, target, target.level_set_by_hand, changed_by=actor
                    )
                except UserManagementError as exc:
                    _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                else:
                    _announce_line(
                        session,
                        f"Promotion rules {'no longer apply' if target.level_set_by_hand else 'apply again'} "
                        f"to {target.username!r}.",
                    )
                blocked = await _redraw()
            elif choice == "t":
                await session.write_line("")
                currently_disabled = target.disabled_at is not None
                action_word = "Enable" if currently_disabled else "Disable"
                if await prompt_yes_no(session, f"{action_word} {target.username!r}?", default=False):
                    try:
                        target = await lane.run(set_user_disabled, target, not currently_disabled, changed_by=actor)
                    except UserManagementError as exc:
                        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                    else:
                        _announce_line(
                            session,
                            f"{target.username!r} is now {'disabled' if target.disabled_at is not None else 'active'}.",
                        )
                        if target.disabled_at is not None:
                            await _revoke_live_sessions(session, node_controls, target, actor)
                blocked = await _redraw()
            elif choice in ("n", "w"):
                await _edit_account_detail(session, lane, actor, target, choice, run_read=_edit_in_place(choice))
                blocked = await _redraw()
            elif choice == "v":
                await session.write_line("")
                await _revoke_verification(session, lane, actor, target)
                blocked = await _redraw()
            elif choice == "i":
                await session.write_line("")
                new_state = "revoke" if target.can_verify_identity else "grant"
                if await prompt_yes_no(
                    session, f"{new_state.capitalize()} identity-verification permission?", default=False
                ):
                    try:
                        target = await lane.run(
                            set_can_verify_identity, target, not target.can_verify_identity, changed_by=actor
                        )
                    except UserManagementError as exc:
                        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                    else:
                        _announce_line(session,
                            f"{target.username!r} can now verify identity: "
                            f"{'yes' if target.can_verify_identity else 'no'}."
                        )
                        _request_live_access_recheck(node_controls, target)
                blocked = await _redraw()
            elif choice == "e":
                # The member's own Profile, acting on their account (design doc
                # §5.6): its writes check this actor again and are recorded.
                await _edit_profile(session, lane, target, actor=actor)
                target = await lane.run(get_user_by_id, target.id) or target
                blocked = await _redraw()
            elif choice == "s":
                target = await _staff_permissions_screen(
                    session, lane, actor, target, node_controls, description_level=description_level,
                    redraw_in_place=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                )
                blocked = await _redraw()
            elif choice == "k":
                # SysOp-assisted counterpart to the self-service Profile
                # `[K]` field (`netbbs.net.login_flow`'s own `_edit_profile`)
                # -- both now open the same shared `manage_ssh_keys_screen`.
                target = await manage_ssh_keys_screen(session, lane, target, changed_by=actor)
                blocked = await _redraw()
            elif choice == "p":
                # Issue #611: the SysOp-assisted counterpart to the Profile
                # `[A]ccount password` field -- the same shared
                # `manage_password_screen`. `changed_by=actor` is what makes
                # it skip the current-password proof for someone else's
                # account, and what the audit row names.
                target = await manage_password_screen(session, lane, target, changed_by=actor)
                blocked = await _redraw()
            elif choice == "r":
                await session.write_line("")
                action_word = "Unrestrict" if blocked else "Restrict"
                prompt = (
                    f"{action_word} {target.username!r} from logging in?"
                    if not blocked
                    else f"{action_word} {target.username!r} (allow login again)?"
                )
                if await prompt_yes_no(session, prompt, default=False):
                    if blocked:
                        await lane.run(unblock_user, target)
                        _announce_line(session, f"{target.username!r} can log in again.")
                    else:
                        try:
                            await lane.run(block_user, target, blocked_by=actor)
                        except BlocklistError as exc:
                            _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                        else:
                            _announce_line(session, f"{target.username!r} is now blocked from logging in.")
                            await _revoke_live_sessions(session, node_controls, target, actor)
                blocked = await _redraw()
            elif choice == "h":
                await _user_history_screen(session, lane, actor, target)
                blocked = await _redraw()
            elif choice == "d" and target.pending_approval:
                await session.write_line("")
                if await prompt_yes_no(
                    session, f"Decline {target.username!r}'s signup and remove the account?", default=False
                ):
                    try:
                        await lane.run(decline_pending_user, target, declined_by=actor)
                    except UserManagementError as exc:
                        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
                        target = await lane.run(get_user_by_id, target.id) or target
                    else:
                        _announce_line(session, f"{target.username!r}'s signup declined.")
                        return
                blocked = await _redraw()
            elif choice == "d":
                await session.write_line("")
                deleted = await _delete_user_confirm(session, lane, actor, target, node_controls)
                if deleted:
                    return
                blocked = await _redraw()
            else:
                await session.write(reject_unhandled_key(choice) if echoed else "\a")


_STAFF_TOGGLE_KEYS: dict[str, StaffPermission] = {
    "a": StaffPermission.APPROVE_ACCOUNTS,
    "m": StaffPermission.MANAGE_ACCOUNTS,
    "g": StaffPermission.MODERATE_ALL,
}


async def _staff_permissions_screen(
    session: Session, lane: DatabaseLane, actor: User, target: User, node_controls: NodeControls | None,
    *, description_level: str, redraw_in_place: bool = False, unicode_style: bool = True, collapsed: bool = False,
) -> User:
    """
    Give or take `target`'s staff permissions (design doc §5.6, issue
    #836): one toggle each, the Co-SysOp preset that sets all three in one
    confirmed step, and one that removes them all. Every change is
    confirmed, audited by `set_staff_permissions`, and carried into the
    account's live sessions the way a level change is. Returns the account
    as it now stands.

    A screen of its own (issue #1115): with redraw-in-place on it is drawn
    from the top on every pass instead of scrolling a new copy under the
    old one, and each outcome is carried into that redraw above the prompt.
    """
    header_color = await lane.run(effective_header_color_256)
    while True:
        await session.write_line(
            ("" if redraw_in_place else "\r\n")
            + screen_title(
                "Staff permissions",
                breadcrumb=crumbs(session),
                width=session.terminal_width, clear=redraw_in_place, unicode_style=unicode_style,
                collapsed=collapsed, header_color=header_color, node_name_gradient=session.node_name_gradient,
            )
        )
        options = [
            MenuEntry(label=menu_key("A", "pprove accounts"),
                      brief=_yes_no(target.has_staff(StaffPermission.APPROVE_ACCOUNTS))),
            MenuEntry(label=menu_key("M", "anage accounts"),
                      brief=_yes_no(target.has_staff(StaffPermission.MANAGE_ACCOUNTS))),
            MenuEntry(label=menu_key("G", "lobal moderation"),
                      brief=_yes_no(target.has_staff(StaffPermission.MODERATE_ALL))),
        ]
        # Verifying identity is not a staff permission (§5.5) but a SysOp
        # gives it to the same helpers, so it sits here too (issue #1115):
        # the Co-SysOp preset's outcome points at this key on this screen.
        options.append(MenuEntry(label=menu_key("V", "erify identity"), brief=_yes_no(target.can_verify_identity)))
        options.append(MenuEntry(label=menu_key("C", "o-SysOp preset"), brief="All three at once"))
        options.append(MenuEntry(label=menu_key("N", "one"), brief="Remove every staff permission"))
        options.append(MenuEntry(label=menu_key("B", "ack"), brief="Return to the account"))
        await session.write_line(_fitted_menu(options, description_level, session=session, used_rows=4))
        await _choice_prompt(session)
        choice = (await session.read_key()).lower()
        await session.write_line("")
        if choice == "b":
            return target
        if choice == "v":
            target = await _toggle_verify_identity(session, lane, actor, target, node_controls)
            continue
        if choice == HELP_KEY:
            await show_menu_help(
                session, "Staff permissions help", _STAFF_PERMISSIONS_HELP,
                about="What this account may do as staff. Each key turns one permission on or off, "
                      "and asks first; the account's live sessions pick up the change.",
                header_color=header_color, unicode_style=unicode_style,
            )
            continue
        if choice in _STAFF_TOGGLE_KEYS:
            flag = _STAFF_TOGGLE_KEYS[choice]
            giving = not target.has_staff(flag)
            new_mask = target.staff_permissions | int(flag) if giving else target.staff_permissions & ~int(flag)
            question = (
                f"{'Give' if giving else 'Remove'} {STAFF_PERMISSION_LABELS[flag]} "
                f"{'to' if giving else 'from'} {target.username!r}?"
                + (" Staff are shown to members under Operators." if giving and not target.staff_permissions else "")
            )
        elif choice == "c":
            new_mask = int(CO_SYSOP_PRESET)
            question = _co_sysop_question(target.username)
        elif choice == "n":
            new_mask = 0
            question = f"Remove every staff permission from {target.username!r}?"
        else:
            await session.write(reject_unhandled_key(choice))
            continue
        if new_mask == target.staff_permissions:
            _announce_line(session, colored("Nothing to change.", fg_color=MUTED_COLOR))
            continue
        if not await prompt_yes_no(session, question, default=False):
            continue
        try:
            target = await lane.run(set_staff_permissions, target, new_mask, changed_by=actor)
        except UserManagementError as exc:
            _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
            continue
        _announce_line(
            session,
            f"{target.username!r} staff permissions: {describe_staff_permissions(target.staff_permissions)}.",
        )
        if choice == "c" and not target.can_verify_identity:
            # Verifying is not part of the preset (issue #1103); the outcome
            # names the key that grants it on the screen it is shown on
            # (issue #1115).
            _announce_line(session, "To let them verify identity too, use [V]erify identity.")
        _request_live_access_recheck(node_controls, target)


# The staff screen's own briefs are each permission's on/off state; its help
# says what each one lets the account do instead.
_STAFF_PERMISSIONS_HELP = (
    MenuEntry(label=menu_key("A", "pprove accounts"), brief="Approve or decline new accounts"),
    MenuEntry(label=menu_key("M", "anage accounts"), brief="Levels, disabling, passwords"),
    MenuEntry(label=menu_key("G", "lobal moderation"), brief="Every board, area and channel"),
    MenuEntry(label=menu_key("V", "erify identity"), brief="Verify callers' age or real name"),
    MenuEntry(label=menu_key("C", "o-SysOp preset"), brief="All three permissions at once"),
    MenuEntry(label=menu_key("N", "one"), brief="Remove every staff permission"),
)


async def _toggle_verify_identity(
    session: Session, lane: DatabaseLane, actor: User, target: User, node_controls: NodeControls | None
) -> User:
    """`[V]erify identity` on the staff screen: the same audited
    `set_can_verify_identity` as `[i]` on the account, one confirmation
    behind the key (§3.5). Returns the account as it now stands."""
    giving = not target.can_verify_identity
    if not await prompt_yes_no(session, _verify_identity_question(target.username, giving), default=False):
        return target
    try:
        target = await lane.run(set_can_verify_identity, target, giving, changed_by=actor)
    except UserManagementError as exc:
        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
        return target
    _announce_line(
        session, f"{target.username!r} can now verify identity: {'yes' if target.can_verify_identity else 'no'}."
    )
    _request_live_access_recheck(node_controls, target)
    return target


async def _delete_user_confirm(
    session: Session, lane: DatabaseLane, actor: User, target: User, node_controls: NodeControls | None
) -> bool:
    """Returns whether the account was actually deleted -- the caller
    (`_user_detail_screen`) uses this to know whether to return
    entirely (nothing left worth redrawing) or keep showing the same,
    unchanged detail screen (a declined confirmation)."""
    await session.write_line(
        colored(
            "\r\nThis permanently deletes the account. Posts and files they created "
            "keep their recorded author name; their entries in the node's "
            "previous-callers history also "
            "survive, keeping whatever name-visibility choice was in effect at the "
            "time (a prior opt-out stays hidden -- SysOps still see the real name "
            "regardless); moderator grants, chat channel membership/invitations, "
            "preferences, and blocklist entries tied to this account are removed. "
            "This cannot be undone.",
            fg_color=MUTED_COLOR,
        )
    )
    if await lane.run(deletion_retires_username, target):
        await session.write_line(
            colored(
                f"This node has run NetBBS Link, so the username {target.username!r} stays "
                "retired afterwards: nobody can register it and inherit this account's Link "
                "mail address, authorship and reputation. Users -> Held names releases it.",
                fg_color=MUTED_COLOR,
            )
        )
    await write_prompt(
        session, f"Type the username {target.username!r} to confirm, or anything else to cancel: "
    )
    confirmation = (await session.read_line()).strip()
    if confirmation != target.username:
        _confirmation_refused(session, confirmation, target.username)
        return False
    try:
        await lane.run(delete_user, target, deleted_by=actor)
    except UserManagementError as exc:
        _announce_line(session, colored(str(exc), fg_color=MUTED_COLOR))
        return False
    _announce_line(session, f"{target.username!r} deleted.")
    await _revoke_live_sessions(session, node_controls, target, actor)
    return True


# -- self-service registration settings (design doc) -----------


_REGISTRATION_MODE_LABELS = {
    RegistrationMode.OPEN: "open (new accounts active immediately)",
    RegistrationMode.APPROVAL_REQUIRED: "approval required (SysOp must approve new accounts)",
    RegistrationMode.CLOSED: "closed (no public registration; SysOp-created accounts only)",
}


async def _registration_settings_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """
    Sets the node's `registration_mode` (design doc) --
    open/approval_required/closed, replacing the earlier plain
    require-approval toggle -- and surfaces how many self-registered
    accounts are currently waiting on approval. Approving/rejecting any
    of them individually still happens via `[U]ser list` -> a pending
    account's own detail screen (`_user_detail_screen`'s `[A]pprove` action),
    reusing the existing user-management flow rather than building a
    second, parallel pending-accounts queue UI.

    The screen stays up after a change and shows the mode it now has:
    it used to print the outcome and return, and the Users menu's
    redraw wiped the line before it could be read.
    """
    def _load(db: Database) -> tuple[RegistrationMode, int, str | None]:
        return (
            get_registration_mode(db),
            sum(1 for u in list_users(db) if u.pending_approval),
            get_registration_question(db),
        )

    modes = {"o": RegistrationMode.OPEN, "a": RegistrationMode.APPROVAL_REQUIRED, "c": RegistrationMode.CLOSED}
    chrome = await _load_chrome(lane, actor)
    message: str | None = None
    while True:
        current, pending_count, question = await lane.run(_load)
        rows: list[Field | Note] = [Field("Current mode", _REGISTRATION_MODE_LABELS[current], bold=True)]
        # Issue #835 (F072). Shown in every mode, since a SysOp may set it
        # before switching to approval, but asked only in approval mode.
        rows.append(Field(
            "Signup question",
            sanitize_text(question) if question else "(none)",
            color=VALUE_COLOR if question else MUTED_COLOR,
            note=None if current == RegistrationMode.APPROVAL_REQUIRED or not question
            else "asked only while approval is required",
        ))
        if pending_count:
            rows.append(Field(
                "Awaiting approval", f"{pending_count} account(s) -- see [U]ser list", color=WARNING_COLOR
            ))
        if current == RegistrationMode.APPROVAL_REQUIRED:
            # Issue #835: a SysOp's banner promised newcomers they could
            # "look around a bit" while they waited, and they can't.
            rows.append(Note(
                "A new account can't log in at all until you approve it -- not even to look around. "
                "The caller is told it is waiting for your approval."
            ))
        choice, _page = await show_detail(
            session,
            title=_detail_title(
                session, chrome, "Registration",
                subtitle="Who may create an account on this node by themselves.",
            ),
            sections=[Section("Self-service registration", rows)],
            actions=[
                ("o", menu_key("O", "pen")),
                ("a", menu_key("A", "pproval required")),
                ("c", menu_key("C", "losed")),
                ("q", menu_key("Q", "uestion")),
                _BACK_ACTION,
            ],
            message=message,
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            help_title="Registration help",
            help_about=(
                "Who may create an account on this node by themselves, and the question new callers answer "
                "while approval is required."
            ),
        )
        if choice == "b":
            return
        if choice == "q":
            await session.write_line("")
            await write_prompt(
                session,
                f"Question to ask new callers (up to {MAX_REGISTRATION_QUESTION_LENGTH} characters, "
                "blank for none): ",
            )
            text = (await session.read_line()).strip()
            if len(text) > MAX_REGISTRATION_QUESTION_LENGTH:
                message = colored(
                    f"That is {len(text)} characters; the question can be at most "
                    f"{MAX_REGISTRATION_QUESTION_LENGTH}. Not changed.",
                    fg_color=ERROR_COLOR,
                )
                continue

            def _apply_question(db: Database) -> None:
                set_registration_question(db, text or None)
                record_action(
                    db, actor=actor, action="set_registration_question",
                    detail="cleared" if not text else f"question={text!r}",
                )

            await lane.run(_apply_question)
            message = colored(
                "Signup question set." if text else "Signup question removed.", fg_color=SUCCESS_COLOR
            )
            continue
        new_mode = modes[choice]
        if new_mode == current:
            message = colored("Already set to that mode.", fg_color=MUTED_COLOR)
            continue

        def _apply(db: Database) -> None:
            set_registration_mode(db, new_mode)
            record_action(db, actor=actor, action="set_registration_mode", detail=f"mode={new_mode.value}")

        await lane.run(_apply)
        message = colored(
            f"Registration mode is now: {_REGISTRATION_MODE_LABELS[new_mode]}", fg_color=SUCCESS_COLOR
        )


# -- self-update (design doc §17) --


# -- backup status (design doc §13.4, issue #60's first operational slice) --


# -- node-wide display format/timezone -------------------------------------


# -- Link status (issue #60, narrow scope) -----------------------------------


# -- carry decisions: offered and excluded resources (design doc §9.3, --------
# -- issue #683) ---------------------------------------------------------------


# -- node keys: guided operational-key rotation (design doc §4.5, ------------
# -- issue #624) -------------------------------------------------------------


# -- outbox: work-item inspection/replay/cancel (design doc §13.7, ----------
# -- issue #60's second operational slice) ----------------------------------


# -- node management (design doc) -------------------------------------------


# -- maintenance mode and drain (design doc §13.8) --------------------------


# -- banners & mastheads umbrella (issue #178) -----------------------------
#
# Every banner/masthead singleton -- welcome, logoff, new-account
# before/after, main-menu, board list, file areas, chat channel picker --
# reachable through this one Settings entry, grouped into two
# subsections (Banners, Mastheads) that mirror this file's own
# menu-within-menu convention (`_theme_colors_menu` inside `_system_menu`).
# Replaces the flat `[W]elcome banner`/`[M]asthead` top-level rows and
# folds in the "Session banners" (issue #177) and "Section mastheads"
# (issue #176) interim groupings, which named this exact reorg as their
# own eventual destination. Each leaf screen's own enable/disable/
# preview/edit shape is unchanged -- only the navigation path to reach
# it moved.


# -- welcome banner (design doc -- part one of a three-part skinning
# initiative) ----------------------------------------------------------


async def _upload_door_file_screen(session: Session, lane: DatabaseLane, actor: User) -> None:
    """Doors -> [U]pload (issue #728): one file into the node's own doors
    directory, the one [F]rom disk lists. The SysOp names it first; the
    name the sending side reports is not used. Nothing is registered and
    no mode bits change: [F]rom disk runs a script through an interpreter,
    so it needs no execute bit."""
    reason = _upload_unavailable_reason(session)
    if reason is not None:
        _announce(session, reason, error=True)
        return
    directory = await lane.run(custom_doors_dir)
    await write_prompt(session, f"\r\nName for the file in {directory} (blank cancels): ")
    name = (await session.read_line()).strip()
    if not name:
        _announce(session, "Nothing uploaded.")
        return
    error = door_filename_error(name)
    if error is not None:
        _announce(session, error, error=True)
        return
    destination = directory / name
    problem = destination_problem(destination)
    if problem is not None:
        _announce(session, problem, error=True)
        return
    if destination.exists() and not await prompt_yes_no(
        session, f"Replace the existing {name}? A door that runs it uses the new file from its next launch",
        default=False,
    ):
        _announce(session, "Nothing uploaded.")
        return
    target = SysOpUploadTarget(
        kind=SYSOP_UPLOAD_DOOR_FILE, destination=destination, max_bytes=await lane.run(get_max_upload_bytes),
        label=f"door file {name}", audit_action="upload_door_file", replaces=destination.exists(),
    )
    await _receive_sysop_upload(session, lane, actor, target, then="Use [F]rom disk to register it as a door.")


# -- main-menu masthead (issue #161 -- part two of the three-part
# skinning initiative the welcome banner above calls itself part one
# of) ---------------------------------------------------------------


# -- banners (issue #178) ------------------------------------------------
#
# Welcome banner plus the three session-point banners added by issue
# #177 (logoff, new-account before/after), all four independent
# singletons on the same mechanism (colocated .ans file, node_config
# enabled flag, size cap, silent fallback). The three #177 banners
# originally shipped with no [G]allery/[F]rom disk -- issue #169's own
# curated preset library didn't extend to them yet, so preview/enable/
# disable/edit alone covered their acceptance criteria. Dogfood follow-
# up: each surface now has its own curated preset collection. Their
# timing and purpose are different enough that generic main-menu strips
# made misleading and visually repetitive lifecycle banners.


# -- logoff banner --------------------------------------------------------


# -- new-account banner (before signup) ------------------------------------


# -- new-account banner (after signup) -------------------------------------


# -- mastheads (issue #178) -----------------------------------------------
#
# Main-menu masthead (issue #161) plus the three section mastheads added
# by issue #176 (board list, file areas, chat channel picker, via
# netbbs.net.picker.pick_item's own `masthead` parameter), all four
# independent singletons on the same mechanism.


# -- board list masthead --------------------------------------------------


# -- file area masthead ----------------------------------------------------


# -- chat channel picker masthead -------------------------------------------


# -- node colors (issue #162) ------------------------------------------


# -- boards & areas (design doc) ---------------------------------------
#
# Boards and file areas share an identical schema shape and permission
# model (BoardPermission is reused for both object_type='board' and
# 'file_area', see netbbs.moderation.roles) but diverge in terminology
# ("post" vs "file", max_post_age_days vs max_file_age_days) -- written
# as two structurally-parallel but separately-coded sections here,
# matching this file's existing style for user management rather than
# building a shared abstraction for just two call sites.


@located("Content")
async def _content_menu(
    session: Session, lane: DatabaseLane, actor: User, *, link_context: LinkContext | None = None,
    mrc_bridge: MrcBridge | None = None,
    chat_hub: ChatHub | None = None,
    door_services: Any = None,
    transfers: Any = None,
    backup_identity_dir: Path | None = None,
) -> None:
    def _load_stats(db: Database) -> dict[str, Any]:
        all_boards = list_boards(db)
        all_areas = list_file_areas(db)
        channels = list_channels(db)
        doors = list_doors(db)
        communities = list_communities(db)
        # Read only, as on the dashboard (issue #681).
        pending_posts = sum(count_pending_posts(db, b) for b in all_boards)
        pending_files = sum(count_pending_files(db, a) for a in all_areas)
        total_posts = sum(count_listed_posts(db, b)[0] for b in all_boards)
        total_files = sum(count_listed_files(db, a)[0] for a in all_areas)
        return {
            "total_boards": len(all_boards),
            "total_posts": total_posts,
            "total_areas": len(all_areas),
            "total_files": total_files,
            "total_channels": len(channels),
            "total_doors": len(doors),
            "total_communities": len(communities),
            "pending_posts": pending_posts,
            "pending_files": pending_files,
            "description_level": menu_description_level(db, actor),
            "unicode_style": unicode_style_enabled(db, actor),
            "collapsed": breadcrumb_collapsed_enabled(db, actor),
            "redraw_in_place": redraw_in_place_enabled(db, actor),
            "header_color": effective_header_color_256(db),
        }

    stats = await lane.run(_load_stats)
    await _draw_content_menu(session, stats=stats)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "m":
            await session.write_line("")
            await _board_menu(session, lane, actor, link_context=link_context)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "f":
            await session.write_line("")
            await _area_menu(session, lane, actor, link_context=link_context, transfers=transfers)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "d":
            await session.write_line("")
            await _door_menu(session, lane, actor, door_services=door_services,
                             backup_identity_dir=backup_identity_dir)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "l":
            await session.write_line("")
            await _channel_menu(session, lane, actor, link_context=link_context, mrc_bridge=mrc_bridge, chat_hub=chat_hub)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "o":
            from netbbs.net.bot_flow import bot_menu
            await bot_menu(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "c":
            await session.write_line("")
            await _category_menu(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "t":
            await session.write_line("")
            await _community_menu(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "g":
            await session.write_line("")
            await _grant_moderator_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "r":
            await session.write_line("")
            await _revoke_moderator_screen(session, lane, actor)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == "p":
            await session.write_line("")
            await _pending_review_screen(session, lane, actor, link_context=link_context, transfers=transfers)
            stats = await lane.run(_load_stats)
            await _draw_content_menu(session, stats=stats)
        elif choice == HELP_KEY:
            await show_menu_help(
                session, "Content help", _CONTENT_MENU,
                about="Everything callers read, download, play and chat in, who moderates it, and "
                      "what waits for approval.",
                header_color=stats["header_color"], unicode_style=stats["unicode_style"],
            )
            await _draw_content_menu(session, stats=stats)
        else:
            await session.write(reject_unhandled_key(choice))


_CONTENT_MENU = (
    MenuEntry(label=menu_key("M", "essage boards"), brief="Create/edit message boards"),
    MenuEntry(label=menu_key("F", "ile areas"), brief="Create/edit file areas"),
    MenuEntry(label=menu_key("D", "oors"), brief="Register/edit door games"),
    MenuEntry(label=menu_key("L", "ive chat"), brief="Create/edit chat channels"),
    MenuEntry(label=menu_key("O", "pen chat bots"), brief="Bots, status and kill switches"),
    MenuEntry(label=menu_key("C", "ategories"), brief="Group lists of one kind"),
    MenuEntry(label=menu_key("T", "opics"), brief="Communities holding every kind"),
    MenuEntry(label=menu_key("G", "rant moderator"), brief="Grant a moderation scope"),
    MenuEntry(label=menu_key("R", "evoke moderator"), brief="Revoke a moderation scope"),
    MenuEntry(label=menu_key("P", "ending review"), brief="Posts and files awaiting approval"),
    MenuEntry(label=menu_key("B", "ack"), brief="Return to the SysOp console"),
)


async def _draw_content_menu(session: Session, *, stats: dict[str, Any]) -> None:
    unicode_style = stats["unicode_style"]
    collapsed = stats["collapsed"]
    redraw_in_place = stats["redraw_in_place"]
    header_color = stats["header_color"]
    description_level = stats["description_level"]

    await session.write_line(
        "\r\n"
        + screen_title(
            "Manage message boards/file areas/chat channels",
            breadcrumb=crumbs(session, own=True),
            subtitle="Content repositories, categories, and moderation permissions.",
            width=session.terminal_width,
            clear=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            header_color=header_color,
            node_name_gradient=session.node_name_gradient,
        )
    )

    panel: list[str] = []
    if session.terminal_height >= 18:
        compact = session.terminal_height < 28 or session.terminal_width < 72
        box_inner_width = min(session.terminal_width, 78) - 4
        pending_total = stats["pending_posts"] + stats["pending_files"]
        if compact:
            panel.extend(
                _wrap_counts_panel(
                    colored("CONTENT: ", fg_color=LABEL_COLOR, bold=True),
                    [
                        ("Message boards", stats["total_boards"]),
                        ("Posts", stats["total_posts"]),
                        ("File areas", stats["total_areas"]),
                        ("Files", stats["total_files"]),
                        ("Channels", stats["total_channels"]),
                    ],
                    width=box_inner_width,
                )
            )
            if pending_total > 0:
                # No gauge here: there's no real moderation-queue capacity
                # to measure against -- a `max(10, pending_total)`
                # denominator would just make the bar permanently "full"
                # past 10 pending items, the same fake-capacity bug this
                # file already dropped for the active-session gauge.
                # `pending_total` is caller-driven (a real backlog can
                # reach 3+ digits), so route it through the same
                # width-aware wrapping the rest of this panel already
                # uses rather than a bare `panel.append` (Codex
                # follow-up, PR #197 review).
                panel.extend(
                    _wrap_counts_panel(
                        colored("MODERATION QUEUE: ", fg_color=LABEL_COLOR, bold=True),
                        [("Pending review", pending_total)],
                        width=box_inner_width,
                    )
                )
            else:
                gauge = telemetry_gauge(0, 10, unicode_style=unicode_style, tone="capacity")
                panel.append(
                    colored("MODERATION QUEUE: ", fg_color=LABEL_COLOR, bold=True)
                    + counts_row([("Pending review", 0)])
                )
                panel.append(
                    f"  {gauge}  "
                    + colored("All clear", fg_color=SUCCESS_COLOR)
                )
        else:
            panel = [
                colored("CONTENT REPOSITORY", fg_color=LABEL_COLOR, bold=True),
                "  "
                + counts_row([("Message boards", stats["total_boards"]), ("Posts", stats["total_posts"])]),
                "  "
                + counts_row([("File areas", stats["total_areas"]), ("Files", stats["total_files"])]),
                "  "
                + counts_row([("Chat channels", stats["total_channels"]), ("Doors", stats["total_doors"]), ("Communities", stats["total_communities"])]),
                colored("MODERATION QUEUE", fg_color=LABEL_COLOR, bold=True),
            ]
            if pending_total > 0:
                # No gauge here -- see the compact branch above for why.
                panel.append("  " + counts_row([("Pending review", pending_total)]))
                panel.append("    " + counts_row([("Posts", stats["pending_posts"]), ("Files", stats["pending_files"])]))
            else:
                gauge = telemetry_gauge(0, 10, unicode_style=unicode_style, tone="capacity")
                panel.append(f"  {counts_row([('Pending review', 0)])}  {gauge}  " + colored("All clear", fg_color=SUCCESS_COLOR))

        await _write_panel(session, panel, unicode_style=unicode_style, header_color=header_color)

    entries = list(_CONTENT_MENU)
    effective_desc_level, available_menu_height, desc_degraded = _degrade_description_level(
        panel=panel, unicode_style=unicode_style, description_level=description_level,
        entry_count=len(entries), terminal_width=session.terminal_width, terminal_height=session.terminal_height,
    )

    await session.write_line(
        _menu_row(
            entries,
            effective_desc_level,
            width=session.terminal_width,
            height=available_menu_height,
            degraded=desc_degraded,
        )
    )
    await _choice_prompt(session)


# -- FieldSpec adapters (design doc, dogfood feature request) --------
#
# Thin wrappers turning this module's own existing per-type prompt
# helpers (above) into the `netbbs.net.resource_editor.FieldPrompt`
# shape a draft-based create/edit screen needs: read/parse, then
# mutate `draft[key]` in place on success, leave it untouched
# (draft-preserving, not screen-aborting) on a rejected entry -- unlike
# every one of these helpers' own original callers, which used to
# treat a `False` `ok` as "abandon the entire in-progress wizard."


# -- Communities (design doc §16) ------------------


# -- message boards ----------------------------------------------------


# -- What actually applies here (issue #528, Codex review) ------------
#
# A board, area or channel that leaves a gate unset inherits its
# Community's, and enforcement reads the inherited value
# (`get_effective_min_age` / `get_effective_name_requirement`, the same
# functions `netbbs.net.file_flow` already gates entry on). A list that
# rendered the resource's own raw `None` therefore printed "-" against a
# resource callers genuinely cannot enter -- the very failure this issue
# was filed about, reintroduced one level up the cascade.
#
# So every list row is built from *effective* values throughout. A row
# answers "who can get in here"; the editor behind `[E]` is where a
# SysOp sees which of those values this resource sets for itself. Levels
# follow the same rule rather than still saying "inherit", since one
# column saying "inherit" beside another silently resolving its
# inherited value would be the more confusing half-measure.


# -- Columnar resource lists (issue #528) -----------------------------
#
# `_board_description` above, and its file-area/channel/Community
# siblings, are kept as-is and still used: `pick_item` falls back to
# them on a terminal too narrow for a table. The column definitions
# below are the wide-terminal form of the same information, plus the
# gates that never fitted into a one-line sentence at all.


# -- file areas ----------------------------------------------------------


# -- doors (issue #172) ------------------------------------------------------
#
# Deliberately flat -- no categories, no age/name-requirement gates, no
# Community-inheritance for its one level field (see netbbs.doors.
# registry's own docstring for why: doors have neither a stated Link
# future nor a read/write split, so this section is noticeably shorter
# than boards'/areas' own.


@located("Doors")
async def _door_menu(session: Session, lane: DatabaseLane, actor: User, *, door_services: Any = None,
                     backup_identity_dir: Path | None = None) -> None:
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    header_color = await lane.run(effective_header_color_256)
    await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
    while True:
        choice = (await session.read_key()).lower()

        if choice == "b":
            await session.write_line("")
            return
        elif choice == "c":
            await session.write_line("")
            await _door_screen(session, lane, actor)
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "g":
            await session.write_line("")
            await _door_gallery_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed,
                                       door_services=door_services, backup_identity_dir=backup_identity_dir)
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "f":
            await session.write_line("")
            await _door_filesystem_screen(session, lane, actor, description_level, redraw_in_place, unicode_style, collapsed,
                                          door_services=door_services, backup_identity_dir=backup_identity_dir)
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "u":
            await session.write_line("")
            await _upload_door_file_screen(session, lane, actor)
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == "l":
            await session.write_line("")
            await _list_doors_screen(session, lane, actor, door_services=door_services, backup_identity_dir=backup_identity_dir)
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        elif choice == HELP_KEY:
            await show_menu_help(
                session, "Doors help", _DOOR_MENU,
                about="Doors are games and programs callers run from the BBS. Register one by hand, "
                      "from NetBBS's own gallery, or from a script already on this node.",
                header_color=header_color, unicode_style=unicode_style,
            )
            await _draw_door_menu(session, description_level, redraw_in_place, unicode_style, collapsed, header_color)
        else:
            await session.write(reject_unhandled_key(choice))


_DOOR_MENU = (
    MenuEntry(label=menu_key("C", "reate"), brief="Register a new door"),
    MenuEntry(label=menu_key("G", "allery"), brief="Register one of NetBBS's own doors"),
    MenuEntry(label=menu_key("F", "rom disk"), brief="Register a script from this node"),
    MenuEntry(label=menu_key("U", "pload"), brief="Send a door file to this node"),
    MenuEntry(label=menu_key("L", "ist"), brief="Browse and edit doors"),
    MenuEntry(label=menu_key("B", "ack"), brief="Return to the Content menu"),
)


async def _draw_door_menu(
    session: Session, description_level: str, redraw_in_place: bool, unicode_style: bool, collapsed: bool,
    header_color: int | tuple[int, int, int] = HEADER_COLOR,
) -> None:
    await session.write_line("\r\n" + screen_title("Doors",
            breadcrumb=crumbs(session, own=True), width=session.terminal_width, clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed, header_color=header_color, node_name_gradient=session.node_name_gradient))
    await session.write_line(
        menu_grid(
            [(
                "",
                list(_DOOR_MENU),
            )],
            width=session.terminal_width,
            height=session.terminal_height,
            description_level=description_level,
        )
    )
    await _choice_prompt(session)


def _door_field_specs(*, actor: User, levels: LevelContext | None = None) -> list[FieldSpec]:
    """One shared field list drives both create and edit, same "single
    source of truth" precedent as `_area_field_specs`/`_board_field_
    specs`. `args` is edited as one space-separated line and split with
    `shlex.split` -- still always launched via `create_subprocess_exec`'s
    argv-list form (see `netbbs.doors.runtime`), never a shell; this is
    just a friendlier way for a SysOp to type several arguments on one
    line than re-entering the field once per argument."""
    return [
        FieldSpec(
            key="name", label="Name",
            section="Door",
            render=lambda d: d.get("name") or "(blank)",
            prompt=text_field("name", required=True),
            brief="The door's display name",
            help="The door's display name, shown wherever it's listed. Must be non-blank.",
        ),
        FieldSpec(
            key="description", label="Description",
            section="Door",
            render=lambda d: d.get("description") or "(none)",
            prompt=text_field("description"),
            brief="Shown when browsing doors",
            help="A short explanation of what this door is, shown when browsing/selecting it.",
        ),
        FieldSpec(
            key="executable_path", label="Executable path",
            section="Launch",
            render=lambda d: d.get("executable_path") or "(blank)",
            prompt=text_field("executable_path", required=True),
            brief="Path to the program to launch",
            help=(
                "The full filesystem path to the door's own executable. Runs as the same OS "
                "user as NetBBS itself, with CPU/memory/process-count limits enforced "
                "regardless of what the door does -- see the operator guide for the full "
                "sandbox model."
            ),
        ),
        FieldSpec(
            key="args_line", label="Arguments",
            section="Launch",
            render=lambda d: d.get("args_line") or "(none)",
            prompt=text_field("args_line"),
            brief="Fixed command-line arguments",
            help="Fixed arguments always passed to the door, space-separated on one line. Leave blank for none.",
        ),
        FieldSpec(
            key="min_play_level", label="Min play level",
            section="Access",
            render=lambda d: _plain_level_label(levels, d.get("min_play_level")),
            prompt=_int_field("min_play_level", "Minimum play level"),
            brief="Level required to launch it",
            help="The permission level a caller needs to launch/play this door.",
        ),
        FieldSpec(
            key="community_id", label="Community",
            section="Placement",
            render=lambda d: d.get("community_id_label") or "(none)",
            prompt=_community_field(actor=actor),
            brief="Where it's offered from",
            help="The Community this door is offered from, if any. 'none' keeps it outside every Community.",
        ),
        FieldSpec(
            key="pinned", label="Pinned",
            section="Placement",
            render=lambda d: "yes" if d.get("pinned") else "no",
            prompt=bool_field("pinned"), step=bool_step("pinned"),
            brief="Shown at the top of listings",
            help="Shown at the top of door listings, above unpinned doors, regardless of sort order.",
        ),
    ]


def _door_draft(door: Door | None) -> dict:
    if door is None:
        return {
            "name": "", "description": None, "executable_path": "", "args_line": "",
            "min_play_level": 0, "community_id": None, "pinned": False, "community_id_label": None,
        }
    return {
        "name": door.name, "description": door.description,
        "executable_path": door.executable_path, "args_line": " ".join(door.args),
        "min_play_level": door.min_play_level, "community_id": door.community_id,
        "pinned": door.pinned,
    }


async def _door_screen(
    session: Session, lane: DatabaseLane, actor: User, *,
    existing: Door | None = None, prefill: dict | None = None,
    door_services: Any = None, backup_identity_dir: Path | None = None,
) -> Door | None:
    """A door's one screen (issue #1081, design doc §3.5), and the screen
    that registers one -- the same shape as `_board_screen`: its
    compatibility above the fields, its companion service (if it declares
    one) after them, and [C]ompatibility, [L]ast diagnostic, [O]utbound,
    [W]orld, [D]elete and the service actions while nothing waits to be
    saved.

    `prefill` (issue #172's door-gallery follow-up): starting field values
    for a brand-new door, merged over the ordinary blank draft -- the same
    screen, save validation and `create_door` call as manual `[C]reate`,
    just not starting from an all-blank form. Ignored when editing."""
    with entered(session, sanitize_text(existing.name) if existing is not None else "Register door"):
        from netbbs.net.door_profile_flow import edit_door_profile, show_door_diagnostic

        current: dict[str, Door | None] = {"door": existing}

        async def save(draft: dict) -> Door:
            if not draft["name"]:
                raise DoorError("name cannot be blank")
            if not draft["executable_path"]:
                raise DoorError("executable path cannot be blank")
            try:
                args = tuple(shlex.split(draft["args_line"]))
            except ValueError as exc:
                raise DoorError(f"could not parse arguments -- {exc}") from exc
            door = current["door"]
            if door is None:
                created = await lane.run(
                    create_door,
                    draft["name"], draft["executable_path"], description=draft["description"], args=args,
                    min_play_level=draft["min_play_level"], pinned=draft["pinned"],
                    community_id=draft["community_id"], creator=actor,
                )
                _announce_line(session, f"Registered door {created.name!r}.")
                return created
            updated = await lane.run(
                update_door,
                door, name=draft["name"], description=draft["description"],
                executable_path=draft["executable_path"], args=args, min_play_level=draft["min_play_level"],
                pinned=draft["pinned"], community_id=draft["community_id"], changed_by=actor,
            )
            _announce_line(session, f"Updated {updated.name!r}.")
            if door_services is not None:
                # The edit can change the executable a service launches, so it
                # reconciles now; otherwise the old one runs on until a caller
                # arrives.
                await door_services.adopt(updated)
            return updated

        def _act(run: Callable[[Door], Awaitable[bool | None]], *, line: bool = True):
            async def action(session: Session, lane: DatabaseLane) -> bool:
                if line:
                    await session.write_line("")
                return bool(await run(current["door"]))
            return action

        async def _compatibility(door: Door) -> bool:
            updated = await edit_door_profile(session, lane, actor, door, door_services=door_services)
            if updated is not None and door_services is not None:
                # Reconcile straight away. Waiting for the next caller would leave
                # an edited service's old process running, and removing a service
                # hides the very controls which could stop it.
                await door_services.adopt(updated)
            return False

        async def _diagnostic(door: Door) -> bool:
            await show_door_diagnostic(session, lane, door)
            return False

        async def _outbound(door: Door) -> bool:
            await _door_outbound_screen(session, lane, actor, door)
            return False

        async def _world(door: Door) -> bool:
            await _war_dialer_world_screen(session, lane, actor, door, backup_identity_dir=backup_identity_dir)
            return False

        async def _delete(door: Door) -> bool:
            deleted = await _delete_door_screen(session, lane, actor, door, door_services=door_services)
            if deleted and door_services is not None:
                # The registration is gone, so nothing could reach its service
                # again; without this the companion and its restart loop run on
                # until the node stops, with no control left to halt it.
                await door_services.forget(door.id)
            return deleted

        def _service(choice: str):
            async def run(door: Door) -> bool:
                await _door_service_action(session, lane, actor, door, choice, door_services)
                return False
            return run

        async def refresh() -> DetailState:
            door = current["door"]
            if door is None:
                return DetailState(draft={**_door_draft(None), **(prefill or {})})

            def _load(db: Database):
                fresh = next((d for d in list_doors(db) if d.id == door.id), door)
                community = get_community(db, fresh.community_id) if fresh.community_id is not None else None
                return fresh, {"community_id_label": community.name if community is not None else None}

            door, labels = await lane.run(_load)
            current["door"] = door
            header = "Compatibility: " + (door.profile.adapter if door.profile else "NetBBS native API")
            after = ""
            if door.profile and door.profile.service:
                # Issue #466: only a door which declares a service says anything
                # about one, so most doors look exactly as before.
                status = door_services.status(door.id) if door_services is not None else None
                service_rows: list[Field | Note] = [Field(
                    "Service", status.summary() if status is not None else "not supervised by this process",
                    color=VALUE_COLOR if status is not None else MUTED_COLOR,
                )]
                if status is not None and status.last_exit_code is not None:
                    service_rows.append(Field(
                        "Last service exit code", str(status.last_exit_code),
                        color=VALUE_COLOR if status.last_exit_code == 0 else WARNING_COLOR,
                    ))
                after = _sections_text(
                    [Section("Companion service", service_rows)], width=session.terminal_width,
                    unicode_style=await lane.run(unicode_style_enabled, actor),
                )
            actions = [
                DetailAction("c", menu_key("C", "ompatibility"), _act(_compatibility, line=False), brief="Profile, preflight and test launch"),
                DetailAction("l", menu_key("L", "ast diagnostic"), _act(_diagnostic, line=False), brief="View runtime errors"),
                DetailAction("o", menu_key("O", "utbound"), _act(_outbound, line=False), brief="Whether it may post to a board"),
            ]
            if await asyncio.to_thread(_is_war_dialer_door, await lane.run(_node_db_path), door):
                actions.append(DetailAction("w", menu_key("W", "orld"), _act(_world, line=False), brief="Status and maintenance"))
            actions.append(DetailAction("d", menu_key("D", "elete"), _act(_delete), brief="Permanently remove this door"))
            if door.profile and door.profile.service and door_services is not None:
                actions += [
                    DetailAction("s", menu_key("S", "tart service"), _act(_service("s")), brief="Start its companion process"),
                    DetailAction("h", menu_key("H", "alt service"), _act(_service("h")), brief="Stop this door's companion process"),
                    DetailAction("r", menu_key("R", "estart service"), _act(_service("r")), brief="Stop then start it again"),
                    DetailAction("v", menu_key("V", "iew service log"), _act(_service("v")), brief="Recent service stderr"),
                ]
            return DetailState(
                draft={**_door_draft(door), **labels},
                title=sanitize_text(door.name),
                header=colored(header, fg_color=MUTED_COLOR), after_fields=after, actions=actions,
            )

        redraw_in_place, redraw_hint = await lane.run(_resolve_redraw_preference, actor)
        return await edit_resource_draft(
            session, lane,
            title=sanitize_text(existing.name) if existing is not None else "Register door", own=True,
            fields=_door_field_specs(actor=actor, levels=await lane.run(level_context)),
            draft={}, save=save, error_type=DoorError,
            save_menu_text=menu_key("S", "ave"), back_menu_text=menu_key("B", "ack"),
            description_level=await lane.run(menu_description_level, actor),
            redraw_in_place=redraw_in_place, redraw_hint=redraw_hint,
            unicode_style=await lane.run(unicode_style_enabled, actor),
            collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
            accent_color=await lane.run(effective_accent_color_256),
            header_color=await lane.run(effective_header_color_256),
            detail=DetailMode(refresh=refresh, stay_after_save=existing is not None),
        )


def _find_door_by_name(db: Database, name: str) -> Door | None:
    try:
        return get_door_by_name(db, name)
    except DoorError:
        return None


async def _resolve_door_name_collision(
    session: Session, lane: DatabaseLane, actor: User, default_name: str, *, door_services: Any = None,
    backup_identity_dir: Path | None = None,
) -> str | None:
    """Shared by every screen that prefills a *new* door registration
    from a default name it didn't get to choose freely (the bundled-door
    gallery, the SysOp's-own-scripts filesystem picker) -- `name` has a
    real `UNIQUE` constraint (registry.py), so silently handing back a
    name that's already taken would only ever surface as a save-time
    "name already in use" error, or worse, get past that by the SysOp
    incidentally renaming a field they were editing for an unrelated
    reason. Asks explicitly instead: a new instance under a different
    name, editing the existing registration directly, or cancelling.

    Returns the name to prefill a fresh registration with (`default_name`
    unchanged if there was no collision, or whatever the SysOp typed for
    a new instance), or `None` if the caller has nothing left to do --
    cancelled, or the existing door's own detail screen was opened
    directly instead."""
    existing = await lane.run(_find_door_by_name, default_name)
    if existing is None:
        return default_name
    async def _ask() -> None:
        await session.write_line(
            colored(f"\r\n{default_name!r} is already registered as a door.", fg_color=MUTED_COLOR)
        )
        await session.write_line(
            action_bar(
                [
                    menu_key("N", "ew instance under a different name"),
                    menu_key("E", "dit the existing one"),
                    # `B` and Esc leave every screen (issue #1158); this was
                    # `[C]ancel`, which they could not reach.
                    menu_key("B", "ack"),
                ],
                width=session.terminal_width, height=session.terminal_height,
            )
        )
        await _choice_prompt(session)

    await _ask()
    while True:
        choice = (await session.read_key()).lower()
        if choice in ("n", "e", "b"):
            await session.write_line(choice.upper())
            break
        if choice == HELP_KEY:
            await show_menu_help(
                session, "Door name help",
                [MenuEntry(label=menu_key("N", "ew instance under a different name"),
                           brief="Register it again, renamed"),
                 MenuEntry(label=menu_key("E", "dit the existing one"), brief="Open the door already registered")],
                about="Every door needs a name of its own, and this one is taken. Back registers nothing.",
                header_color=await lane.run(effective_header_color_256),
                unicode_style=await lane.run(unicode_style_enabled, actor),
            )
            await _ask()
            continue
        await session.write(reject_unhandled_key(choice))
    if choice == "e":
        await _door_detail_screen(session, lane, actor, existing, door_services=door_services,
                                  backup_identity_dir=backup_identity_dir)
        return None
    if choice != "n":
        return None
    await write_prompt(session, "New name for this instance (blank to cancel): ")
    new_name = sanitize_text((await session.read_line()).strip())
    return new_name or None


async def _door_gallery_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool, *, door_services: Any = None,
    backup_identity_dir: Path | None = None,
) -> None:
    """Browse NetBBS's own first-party doors (issue #172) and register
    one with sensible defaults pre-filled, instead of starting
    `[C]reate`'s editor from an all-blank form. These ship as real
    installed package data (`netbbs.doors.bundled`, the same mechanism
    `netbbs.net.banner_presets` already uses for bundled welcome-banner/
    masthead samples) -- not sample code to point a door at, first-class
    product content NetBBS ships with (Voidrunner alone is a genuinely
    complete persistent game, not a toy). A SysOp's own, separately-
    authored door is untouched by any of this -- still registered by
    hand via `[C]reate`, still a filesystem path the SysOp supplies.

    Unlike the welcome-banner/masthead galleries this otherwise mirrors
    the shape of, there's nothing here to actually preview -- a door is
    a program, not a rendered banner -- so selecting an entry shows its
    details (what it is, and what defaults would be used) rather than
    what it looks like.

    Dogfood follow-up: selecting an entry goes *straight* to the
    prefilled editor now, no confirmation step first. There used to be
    one, copied from the banner galleries' own "preview -> confirm ->
    apply" shape without noticing it doesn't actually fit here:
    confirming there directly writes bytes to disk and enables the
    banner, so a confirmation before that makes sense; confirming here
    only ever opened the very same editor a confirmation-free `[C]reate`
    already lets a SysOp enter freely, which still requires its own
    explicit `[S]ave` and lets `[B]ack` discard the draft with nothing
    persisted -- identical in risk to opening an *existing* door's own
    detail/edit screen from the list, which already has no confirmation
    gate either. The interpreter default is `sys.executable` -- whatever
    Python is currently running NetBBS itself, almost always correct for
    a stdlib-only door -- rather than a new stored "default interpreter"
    setting.

    Dogfood follow-up: if a door is already registered under this
    entry's exact default name, selecting it again no longer silently
    hands back that same name (which `create_door`'s own `UNIQUE`
    constraint on `name` would then reject on save unless the SysOp
    happened to also rename it while editing something unrelated --
    an accidental path to "I meant to update the existing one, not
    create a second" or vice versa). Registering the *same* underlying
    script more than once is a real, legitimate thing to want (the same
    game bound to a different Community, a different tick rate, a
    different universe) -- so this surfaces the collision explicitly and
    asks which was actually meant, rather than leaving it to chance.

    Declining, or backing out of the editor without saving, loops back
    into this same gallery (`pick_item`'s `start_stable_id` re-highlights
    the entry just looked at -- the same dogfood fix applied to the
    banner galleries) rather than exiting, so registering more than one
    bundled door in one visit doesn't mean re-entering `[G]allery` each
    time."""
    available = available_bundled_doors()
    if not available:
        _announce_line(session,
            colored(
                "\r\nNo bundled doors found on this filesystem -- NetBBS's own doors ship as "
                "real installed package data, so this suggests an incomplete install rather "
                "than the normal case.",
                fg_color=MUTED_COLOR,
            )
        )
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(available, start=1)),
            name_of=lambda pair: pair[1][0].name,
            stable_id_of=lambda pair: pair[0],
            description_of=lambda pair: pair[1][0].description,
            title="Door gallery",
            empty_message="No bundled doors found.",
            description_level=description_level,
            redraw_in_place=redraw_in_place,
            unicode_style=unicode_style,
            collapsed=collapsed,
            start_stable_id=last_stable_id,
        )
        if selection is None:
            return
        last_stable_id = selection[0]
        entry, path = selection[1]

        await session.write_line(colored(f"\r\n{entry.name}", fg_color=MUTED_COLOR, bold=True))
        await session.write_line(colored(
            reflow(sanitize_text(entry.description), width=session.terminal_width), fg_color=MUTED_COLOR,
        ))
        await session.write_line(colored(f"  Suggested min level: {entry.suggested_min_play_level}", fg_color=MUTED_COLOR))
        await session.write_line(colored(f"  Interpreter (default, editable next): {sys.executable}", fg_color=MUTED_COLOR))
        await session.write_line(colored(f"  Script: {path}", fg_color=MUTED_COLOR))

        prefill_name = await _resolve_door_name_collision(session, lane, actor, entry.name,
                                                         door_services=door_services,
                                                         backup_identity_dir=backup_identity_dir)
        if prefill_name is None:
            continue

        prefill = {
            "name": prefill_name, "description": entry.description,
            # `.as_posix()`, not `str(path)`: the args field is parsed
            # with `shlex.split` in POSIX mode (see `_door_field_specs`'
            # own docstring -- deliberately, for the same never-a-shell
            # safety reasoning as everywhere else args reach a
            # subprocess argv list), which treats a bare backslash as an
            # escape character and silently eats it -- a raw Windows
            # path (`C:\Users\...`) round-trips through that as mangled
            # garbage (`C:Users...`). Forward slashes are unaffected by
            # that escaping and are accepted as path separators on every
            # platform NetBBS runs on, Windows included.
            "executable_path": sys.executable, "args_line": path.as_posix(),
            "min_play_level": entry.suggested_min_play_level,
        }
        await _door_screen(session, lane, actor, prefill=prefill)


async def _door_filesystem_screen(
    session: Session, lane: DatabaseLane, actor: User, description_level: str,
    redraw_in_place: bool, unicode_style: bool, collapsed: bool, *, door_services: Any = None,
    backup_identity_dir: Path | None = None,
) -> None:
    """A SysOp's *own* door scripts, not NetBBS's -- the direct
    counterpart to `[G]allery` for something a SysOp wrote or downloaded
    themselves rather than one of NetBBS's own bundled doors. Same
    locked design as issue #170's welcome-banner/masthead filesystem
    picker, applied to a different directory: the browsable root is
    `netbbs.doors.custom_doors_dir(db)`, a real, narrow, conventional
    location under the node's own state directory -- not open-ended
    traversal from `/` -- that this screen never creates on its own; it
    simply reports nothing found until a SysOp places something there
    (e.g. via SFTP/SCP). Unfiltered by extension, unlike the `.ans`-only
    banner picker: a door can legitimately be any executable, not one
    well-known format.

    There is no metadata to show here the way a bundled door's own
    catalog entry has (no description, no suggested play level) -- a
    SysOp's own script is just a filename NetBBS knows nothing about.
    The prefilled name defaults to the file's own stem, the interpreter
    defaults to `sys.executable` the same guessed-default way the
    gallery's own prefill does, and both remain fully editable in the
    real create-door editor this opens next -- never auto-registered on
    selection alone. Same duplicate-name handling as the gallery
    (`_resolve_door_name_collision`) and the same cursor-preserving
    loop-back on decline/cancel/save (`pick_item`'s `start_stable_id`)."""

    def _list(db: Database) -> tuple[list[Path], Path]:
        directory = custom_doors_dir(db)
        if not directory.is_dir():
            return [], directory
        # Hidden files are left out: nothing a SysOp registers starts with a
        # dot, and an upload interrupted by a crash leaves its temporary copy
        # here under one (`netbbs.sysop_uploads.install_upload`).
        return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")), directory

    files, directory = await lane.run(_list)
    if not files:
        _announce_line(session, colored(
            f"\r\nNo files found in {directory}. Place your own door script there "
            f"(e.g. via SFTP/SCP), or send one with [U]pload, then browse again.", fg_color=MUTED_COLOR,
        ))
        return

    last_stable_id: int | None = None
    while True:
        selection = await pick_item(
            session,
            list(enumerate(files, start=1)),
            name_of=lambda pair: pair[1].name,
            stable_id_of=lambda pair: pair[0],
            title="Doors -- your own scripts",
            empty_message="No files found.",
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

        await session.write_line(colored(f"\r\n{path.name}", fg_color=MUTED_COLOR, bold=True))
        await session.write_line(colored(f"  Interpreter (default, editable next): {sys.executable}", fg_color=MUTED_COLOR))
        await session.write_line(colored(f"  Script: {path}", fg_color=MUTED_COLOR))

        prefill_name = await _resolve_door_name_collision(session, lane, actor, path.stem,
                                                         door_services=door_services,
                                                         backup_identity_dir=backup_identity_dir)
        if prefill_name is None:
            continue

        prefill = {
            "name": prefill_name, "description": None,
            # `.as_posix()`, not `str(path)` -- see the gallery's own
            # identical comment above for why (shlex-mangled Windows
            # backslashes).
            "executable_path": sys.executable, "args_line": path.as_posix(),
            "min_play_level": 0,
        }
        await _door_screen(session, lane, actor, prefill=prefill)


async def _list_doors_screen(session: Session, lane: DatabaseLane, actor: User, *, door_services: Any = None,
                             backup_identity_dir: Path | None = None) -> None:
    doors = await lane.run(list_doors)
    selected = await pick_item(
        session, doors,
        name_of=lambda d: d.name,
        stable_id_of=lambda d: d.id,
        description_of=lambda d: d.description,
        title="Doors", own=True,
        empty_message="No doors yet.",
        redraw_in_place=await lane.run(redraw_in_place_enabled, actor),
        unicode_style=await lane.run(unicode_style_enabled, actor),
        collapsed=await lane.run(breadcrumb_collapsed_enabled, actor),
        accent_color=await lane.run(effective_accent_color_256),
        header_color=await lane.run(effective_header_color_256),
    )
    if selected is not None:
        await _door_detail_screen(session, lane, actor, selected, door_services=door_services,
                                  backup_identity_dir=backup_identity_dir)


async def _door_detail_screen(session: Session, lane: DatabaseLane, actor: User, door: Door, *,
                              door_services: Any = None, backup_identity_dir: Path | None = None) -> None:
    """A door's screen is its editor (issue #1081): see `_door_screen`."""
    await _door_screen(
        session, lane, actor, existing=door, door_services=door_services, backup_identity_dir=backup_identity_dir,
    )


def _door_target_description(board: Board) -> str | None:
    """What a SysOp needs to know about a board before allowing a door to post.

    A door is not a person: it has no birthdate and no verified name, so an
    age or name requirement cannot apply to it, and the door posts whatever
    the SysOp allowed. That is the intended behaviour -- the allowlist is the
    only gate -- but it is not obvious from a board's name, so it is said here,
    where the choice is actually made, rather than left to be discovered.
    """
    notes = []
    if board.moderated:
        notes.append("moderated — holds this door's posts for approval")
    if board.min_age or board.name_requirement:
        notes.append("age/name gated for callers — a door is not subject to that")
    return "; ".join(notes) if notes else board.description


def _door_channel_candidates(db: Database) -> list[tuple[Channel, bool]]:
    """Every channel a door could be allowed to speak in, and whether it is Linked.

    An MRC-bridged channel is left out rather than shown and refused: it can
    never be allowed, so offering it would only be a dead end.
    """
    return [(channel, is_channel_linked(db, channel)) for channel in list_channels(db)
            if channel_not_allowable(db, channel) is None]


def _door_channel_description(channel: Channel, linked: bool) -> str | None:
    """What a SysOp needs to know about a channel before a door may speak in it."""
    notes = []
    if linked:
        notes.append("Linked — its lines reach every peer and cannot be taken back")
    if channel.min_age or channel.name_requirement:
        notes.append("age/name gated for callers — a door is not subject to that")
    return "; ".join(notes) if notes else channel.description


def _door_channel_summary(db: Database, target) -> str:
    """One allowed channel as the outbound screen lists it."""
    name = f"#{target.channel.name}"
    if not target.suspended:
        return name
    by = f" by {target.suspended_by}" if target.suspended_by else ""
    until = f" until {format_for_display(target.suspended_until, db)}" if target.suspended_until else ""
    return f"{name} (muted{by}{until})"


@located("Outbound")
async def _door_outbound_screen(session: Session, lane: DatabaseLane, actor: User, door: Door) -> None:
    """Switch one door's outbound hook on or off, and curate its allowlist.

    Status plus an action bar, never a chain of questions (design doc §3.5):
    every key acts on what the screen is already showing, and the only
    confirmation is the one that destroys something -- switching the hook
    off releases the door's posting label and its whole allowlist.
    """
    description_level = await lane.run(menu_description_level, actor)
    unicode_style = await lane.run(unicode_style_enabled, actor)
    collapsed = await lane.run(breadcrumb_collapsed_enabled, actor)
    redraw_in_place = await lane.run(redraw_in_place_enabled, actor)
    accent_color = await lane.run(effective_accent_color_256)
    header_color = await lane.run(effective_header_color_256)
    if is_remote(door.profile):
        # A remote registration launches no local process and shares no
        # filesystem with a per-session working directory, so it could never
        # write a request. Offering the switch would let a SysOp turn on
        # something that silently does nothing forever.
        await session.write_line("")
        await session.write_line(reflow(
            "This is a remote service. NetBBS runs no program for it and shares no "
            "files with it, so it has no way to hand anything back to post. Outbound is "
            "for doors which run on this node.", width=session.terminal_width))
        await session.write_line(continue_prompt("Back"))
        await session.read_any_key()
        return
    message, message_failed = "", False
    while True:
        config = await lane.run(outbound_config, door.id)
        allowed = await lane.run(outbound_targets, door.id) if config is not None else []
        channels = await lane.run(channel_targets, door.id) if config is not None else []
        channel_summary = [await lane.run(_door_channel_summary, target) for target in channels]
        await session.write_line(
            "\r\n" + screen_title(f"{sanitize_text(door.name)} — outbound",
                breadcrumb=crumbs(session, own=True), width=session.terminal_width,
                clear=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                header_color=header_color, node_name_gradient=session.node_name_gradient)
        )
        if config is None:
            hook: list[Field | Note | Table] = [
                Field("Outbound", status_badge("OFF", tone="neutral", unicode_style=unicode_style), styled=True),
                Note(
                    "This door cannot post anything. Switching it on lets it post to boards "
                    "and speak in chat channels you allow here, and nothing else — it can never "
                    "read the BBS, send mail, or look up a caller."
                ),
            ]
        else:
            lapsed = config.enabled_by_user_id is None
            hook = [
                Field(
                    "Outbound",
                    status_badge("LAPSED" if lapsed else "ON", tone="warning" if lapsed else "success",
                                 unicode_style=unicode_style),
                    styled=True,
                ),
                Field("Posts appear as", config.label, color=AUTHOR_COLOR),
                Field("Ceiling", f"{config.posts_per_hour} posts per hour"),
                Field(
                    "Allowed boards",
                    ", ".join(board.name for board in allowed) if allowed
                    else "(none yet — it can post nowhere until you allow one)",
                    color=VALUE_COLOR if allowed else WARNING_COLOR,
                ),
                Field("Chat ceiling", f"{config.chat_lines_per_hour} lines per hour"),
                Field(
                    "Allowed channels",
                    ", ".join(channel_summary) if channels else "(none)",
                    color=VALUE_COLOR if channels else MUTED_COLOR,
                ),
            ]
            if lapsed:
                hook.append(Note(
                    "The account which switched this on no longer exists, so the door is "
                    "refused until a SysOp vouches for it again.", color=WARNING_COLOR,
                ))
            if any(board.moderated for board in allowed):
                hook.append(Note("A moderated board holds this door's posts for your approval first."))
            if channels:
                hook.append(Note(
                    "A channel's moderators can /mute this door there, for a time or until they "
                    "/unmute it; that stops it in that channel only."))
        await _write_sections(session, [Section("Outbound hook", hook)], unicode_style=unicode_style)
        if message:
            await session.write_line("")
            await session.write_line(
                colored(sanitize_text(message), fg_color=ERROR_COLOR if message_failed else SUCCESS_COLOR)
            )
            message, message_failed = "", False
        options = [MenuEntry(label=menu_key("T", "urn " + ("off" if config else "on")),
                             brief="Whether this door may post at all")]
        if config is not None and config.enabled_by_user_id is None:
            # Without this the only route out of a lapsed hook is [T]urn off,
            # which releases the label and the whole allowlist -- the screen
            # would be telling the SysOp to do something whose only available
            # action destroys their configuration.
            options.insert(1, MenuEntry(label=menu_key("V", "ouch for it"),
                                        brief="Take responsibility for its posts"))
        if config is not None:
            options += [
                MenuEntry(label=menu_key("A", "llow a board"), brief="Let it post to one more board"),
                MenuEntry(label=menu_key("R", "evoke a board"), brief="Stop it posting to one"),
                MenuEntry(label=menu_key("C", "eiling"), brief="Posts per hour"),
                MenuEntry(label=menu_key("L", "et it chat in a channel"),
                          brief="Let it chat in one more channel"),
                MenuEntry(label=menu_key("D", "rop a channel"), brief="Stop it speaking in one"),
                MenuEntry(label=menu_key("H", "ourly chat lines"), brief="Chat lines per hour"),
            ]
        options.append(MenuEntry(label=menu_key("B", "ack"), brief="Return to the door"))
        await session.write_line(
            "\r\n" + _menu_row(options, description_level, width=session.terminal_width,
                               height=session.terminal_height)
        )
        await _choice_prompt(session)
        choice = (await session.read_key()).lower()
        await session.write_line("")

        if choice == "b":
            return
        elif choice == "t" and config is None:
            config = await lane.run(enable_outbound, door, enabled_by=actor)
            message = (f"On. This door posts as {config.label}. Allow it a board next — "
                       "a moderated one holds its first posts for you to read.")
        elif choice == "t":
            if await prompt_yes_no(session,
                                   f"Turn off outbound for {sanitize_text(door.name)}? "
                                   "This releases its posting name and its whole allowlist.",
                                   default=False):
                await lane.run(disable_outbound, door, disabled_by=actor)
                message = "Off. Posts it already made keep the name they were written under."
        elif choice == "v" and config is not None and config.enabled_by_user_id is None:
            config = await lane.run(enable_outbound, door, enabled_by=actor)
            message = (f"You are now responsible for what {sanitize_text(door.name)} posts. "
                       f"It kept the name {config.label} and its allowed boards.")
        elif choice == "a" and config is not None:
            board = await pick_item(
                session, await lane.run(list_boards, order_by="alphabetical"),
                name_of=lambda b: b.name, stable_id_of=lambda b: b.id,
                description_of=lambda b: _door_target_description(b),
                title="Which board may it post to?", empty_message="No message boards yet.",
                description_level=description_level,
                redraw_in_place=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                accent_color=accent_color, header_color=header_color,
            )
            if board is not None:
                await lane.run(allow_target, door, board, allowed_by=actor)
                message = f"{board.name} allowed."
        elif choice == "r" and config is not None:
            board = await pick_item(
                session, allowed, name_of=lambda b: b.name, stable_id_of=lambda b: b.id,
                title="Stop it posting to which board?",
                empty_message="It cannot post to any board yet.",
                description_level=description_level,
                redraw_in_place=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                accent_color=accent_color, header_color=header_color,
            )
            if board is not None:
                await lane.run(revoke_target, door, board, revoked_by=actor)
                message = f"{board.name} revoked."
        elif choice == "c" and config is not None:
            await session.write_line(f"Posts per hour (1-240, currently {config.posts_per_hour}):")
            raw = (await session.read_line()).strip()
            if raw:
                try:
                    config = await lane.run(set_rate_ceiling, door, int(raw), changed_by=actor)
                    message = f"Ceiling is now {config.posts_per_hour} posts per hour."
                except (ValueError, OutboundError) as exc:
                    message_failed = True
                    message = (str(exc) if isinstance(exc, OutboundError)
                               else "That is not a whole number.")
        elif choice == "l" and config is not None:
            picked = await pick_item(
                session, await lane.run(_door_channel_candidates),
                name_of=lambda item: f"#{item[0].name}", stable_id_of=lambda item: item[0].id,
                description_of=lambda item: _door_channel_description(*item),
                title="Which channel may it speak in?",
                empty_message="No chat channel it could speak in (MRC-bridged ones never can).",
                description_level=description_level,
                redraw_in_place=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                accent_color=accent_color, header_color=header_color,
            )
            if picked is not None:
                channel, linked = picked
                # Asked only for a Linked channel, and only because it cannot
                # be undone: every peer carrying it gets each line, and chat
                # has no way to take one back.
                if not linked or await prompt_yes_no(
                        session,
                        f"#{sanitize_text(channel.name)} is Linked: every line this door sends there "
                        "reaches every node carrying it, and cannot be taken back. Allow it?",
                        default=False):
                    try:
                        await lane.run(allow_channel, door, channel, allowed_by=actor)
                        message = f"#{channel.name} allowed."
                    except OutboundError as exc:
                        message, message_failed = str(exc), True
        elif choice == "d" and config is not None:
            target = await pick_item(
                session, channels, name_of=lambda t: f"#{t.channel.name}",
                stable_id_of=lambda t: t.channel.id,
                title="Stop it speaking in which channel?",
                empty_message="It cannot speak in any channel yet.",
                description_level=description_level,
                redraw_in_place=redraw_in_place, unicode_style=unicode_style, collapsed=collapsed,
                accent_color=accent_color, header_color=header_color,
            )
            if target is not None:
                await lane.run(revoke_channel, door, target.channel, revoked_by=actor)
                message = f"#{target.channel.name} revoked."
        elif choice == "h" and config is not None:
            await session.write_line(f"Chat lines per hour (1-240, currently {config.chat_lines_per_hour}):")
            raw = (await session.read_line()).strip()
            if raw:
                try:
                    config = await lane.run(set_chat_ceiling, door, int(raw), changed_by=actor)
                    message = f"Chat ceiling is now {config.chat_lines_per_hour} lines per hour."
                except (ValueError, OutboundError) as exc:
                    message_failed = True
                    message = (str(exc) if isinstance(exc, OutboundError)
                               else "That is not a whole number.")
        elif choice == HELP_KEY:
            await show_menu_help(
                session, "Outbound help", options,
                about="Whether this door may post to message boards and speak in chat channels, and "
                      "where. It can only post where you allow it, never read the BBS or send mail.",
                header_color=header_color, unicode_style=unicode_style,
            )
        else:
            await session.write(reject_unhandled_key(choice))


async def _door_service_action(session: Session, lane: DatabaseLane, actor: User, door: Door,
                               choice: str, door_services: Any) -> None:
    """Start, halt, restart or inspect one door's companion process.

    Each action that changes the process asks for one confirmation keystroke
    and is audit-logged like every other door action; viewing the log changes
    nothing and asks nothing.
    """
    if choice == "v":
        status = door_services.status(door.id)
        await session.write_line(sanitize_text((status.diagnostic if status else "")
                                               or "No output from this door's service."))
        await session.write_line(continue_prompt("Back"))
        await session.read_any_key()
        return
    verb = {"s": "Start", "h": "Halt", "r": "Restart"}[choice]
    # A door name is not restricted to terminal-safe characters, and this
    # prompt combines it with its own styled confirmation hint.
    if not await prompt_yes_no(session, f"{verb} {sanitize_text(door.name)}'s service now?", default=False):
        return
    service = await door_services.adopt(door)
    if service is None:
        _announce(session, "This door no longer declares a service.", error=True)
        return
    try:
        if choice == "s":
            await service.start()
        elif choice == "h":
            await service.stop()
        else:
            await service.restart()
    except OSError as exc:
        _announce(session, f"Could not {verb.lower()} the service: {exc}", error=True)
        return
    await lane.run(record_action, actor=actor, action="door_service", object_type="door",
                   object_id=door.id, detail=f"door={door.name!r} action={verb.lower()}")
    _announce_line(session, f"{verb} requested. Current state: {door_services.status(door.id).summary()}")


def _war_dialer_world_of(db_path: Path, door: Door) -> tuple[Path | None, str | None]:
    """The world this door plays, or why it cannot say.

    `war_dialer_world_path` and `war_dialer_path_problem` are the resolution
    and preflight the launcher and the backup use, so the world shown here is
    the one callers are actually in, and a path the launcher would refuse (a
    legacy world still waiting to be migrated) is reported, not called unplayed.
    Both touch the filesystem, so callers run this in a thread, never on the
    database lane (as the launcher does).
    """
    from types import SimpleNamespace

    from netbbs.doors.runtime import war_dialer_path_problem, war_dialer_world_path
    try:
        world = war_dialer_world_path(SimpleNamespace(path=db_path), door)
        return world, war_dialer_path_problem(door, world)
    except (ValueError, OSError, RuntimeError) as exc:
        return None, str(exc)


def _is_war_dialer_door(db_path: Path, door: Door) -> bool:
    world, problem = _war_dialer_world_of(db_path, door)
    # A resolution failure only happens for a War Dialer launch (or an explicit
    # world override); the world screen reports the reason.
    return world is not None or problem is not None


def _node_db_path(db: Database) -> Path:
    return db.path


def _war_dialer_world_state(db_path: Path, world: Path) -> tuple[dict | None, str | None]:
    """`war_dialer_admin.world_status`, or the bounded reason it failed.

    `(None, None)` means the world genuinely does not exist yet. Any other
    failure to look at it, a permission error included, is a reason: an
    unreadable directory must not read as "nobody has played".

    Blocking file and SQLite work on a world that may be large, so the caller
    runs it in a thread rather than on the node's database lane.
    """
    from netbbs.doors.bundled import war_dialer as wd
    from netbbs.doors.war_dialer_admin import world_status
    try:
        world.stat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, wd._event_plain(str(exc))[:500]
    try:
        return world_status(db_path, world), None
    except (BackupError, wd.WorldStateError, OSError, sqlite3.Error, ValueError) as exc:
        return None, wd._event_plain(str(exc))[:500]


def _war_dialer_set_maintenance(db_path: Path, world: Path, enabled: bool, operator: str) -> str | None:
    from netbbs.doors.bundled import war_dialer as wd
    from netbbs.doors.war_dialer_admin import set_maintenance
    # Shares the competition lock: switching maintenance off while a season
    # change runs would let a caller in between its backup and its commit,
    # making the backup older than the state it claims to precede.
    if not _WAR_DIALER_COMPETITION_LOCK.acquire(blocking=False):
        return "a War Dialer competition change is running; try again when it finishes"
    try:
        set_maintenance(db_path, world, enabled, operator=operator)
    except (BackupError, wd.WorldStateError, OSError, sqlite3.Error, ValueError) as exc:
        return wd._event_plain(str(exc))[:500]
    finally:
        _WAR_DIALER_COMPETITION_LOCK.release()
    return None


def _war_dialer_operation_rows(db: Database, operations: list) -> list[list[str]]:
    rows = []
    for entry in reversed(operations):
        if not isinstance(entry, dict):
            continue
        at = str(entry.get("at", ""))
        try:
            at = format_for_display(at, db)
        except (ValueError, TypeError):
            pass
        rows.append([at, str(entry.get("action", "")), str(entry.get("operator", "")),
                     str(entry.get("reason", "")) or "—"])
    return rows


# One console competition change at a time on this node. Without it, two
# SysOps could interleave: A's backup verified, B's rollover committed, then
# A's rollover -- leaving A's recorded backup older than the state A changed.
# The CLI needs a stopped node, so it never runs beside the console.
_WAR_DIALER_COMPETITION_LOCK = threading.Lock()


@dataclasses.dataclass(frozen=True)
class _CompetitionOutcome:
    status: dict | None
    failure: str | None = None
    # Whether a directory left at the destination after a failure is a
    # complete, verified backup (a failure after it was taken) or not.
    backup_valid: bool = False
    # False when the change was refused before any backup was attempted, so
    # whatever is at the destination is not this operation's.
    backup_attempted: bool = True


def _war_dialer_change_competition(
    db_path: Path, world: Path, *, identity_dir: Path, destination: Path, reason: str, confirm: str,
    reset: bool, operator: str, root: Path | None = None,
) -> _CompetitionOutcome:
    """`war_dialer_admin.change_competition` from the console, or the bounded
    reason it refused. No stopped-node rule here (issue #726, the maintainer's
    decision): maintenance keeps new callers out, the world's session guard
    refuses anyone still inside, and the backup is the live one the Backup
    screen takes."""
    from netbbs.backup import _validate_backup_source
    from netbbs.doors.bundled import war_dialer as wd
    from netbbs.doors.war_dialer_admin import change_competition
    if not _WAR_DIALER_COMPETITION_LOCK.acquire(blocking=False):
        return _CompetitionOutcome(None, "another SysOp is changing a War Dialer competition right now; "
                                         "try again when that finishes", backup_attempted=False)
    try:
        if root is not None:
            # Re-checked here, immediately before the backup, like
            # `_create_live_backup` does: a disk unmounted since the screen
            # checked must not have its mount point recreated underneath.
            db = Database(db_path)
            try:
                current = check_backup_destination(db, db_path, identity_dir)
            except BackupScheduleError as exc:
                return _CompetitionOutcome(None, str(exc), backup_attempted=False)
            finally:
                db.close()
            if current != root:
                return _CompetitionOutcome(None, "the backup destination changed; nothing was changed",
                                           backup_attempted=False)
        return _CompetitionOutcome(change_competition(
            db_path, world, identity_dir=identity_dir, backup_to=destination, confirm=confirm,
            reason=reason, reset=reset, operator=operator, require_stopped_node=False,
        ))
    except (BackupError, wd.WorldStateError, OSError, sqlite3.Error, ValueError) as exc:
        valid = False
        if destination.exists():
            try:
                _validate_backup_source(destination, allow_migrate=False)
                valid = True
            except (BackupError, OSError, sqlite3.Error, ValueError):
                valid = False
        return _CompetitionOutcome(None, wd._event_plain(str(exc))[:500], valid)
    finally:
        _WAR_DIALER_COMPETITION_LOCK.release()


async def _owned_worker(func, /, *, finish=None, **kwargs):
    """Run blocking `func` in a thread and keep it owned to the end.

    Cancelling an `asyncio.to_thread` awaiter does not stop the thread, so a
    cancellation (the SysOp's session dropping) waits for the worker, still
    runs `finish` on its result -- an audit entry for a change that did
    happen must not be lost with the session -- and only then propagates.
    The same ownership `_create_live_backup_owned` gives a live backup."""
    task = asyncio.create_task(asyncio.to_thread(func, **kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            result = task.result()
        except Exception:
            _logger.exception("%s failed after its SysOp session was cancelled", getattr(func, "__name__", func))
            raise cancelled
        if finish is not None:
            try:
                await _run_owned(finish(result))
            except Exception:
                _logger.exception("recording %s failed after its SysOp session was cancelled",
                                  getattr(func, "__name__", func))
        raise cancelled
    if finish is not None:
        await _run_owned(finish(result))
    return result


async def _run_owned(coro) -> None:
    """Await `coro` to completion even if the caller is cancelled meanwhile
    (a finalizer that records something that already happened), then let
    the cancellation propagate."""
    task = asyncio.create_task(coro)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is not None:
            _logger.error("finalizer failed after its SysOp session was cancelled", exc_info=task.exception())
        raise cancelled


async def _war_dialer_competition_flow(
    session: Session, lane: DatabaseLane, actor: User, door: Door, world: Path, status: dict, *,
    reset: bool, identity_dir: Path, db_path: Path,
) -> None:
    """Next season or Competition reset, behind a reason and the world's typed
    filename. Two values, so prompts rather than a draft (design doc §3.5);
    the filename is the type-the-name confirmation before a destructive act."""
    what = "Competition reset" if reset else "Next season"
    if status["maintenance"] != "on":
        _announce(session, f"{what} needs maintenance on first, so nobody can enter the world "
                  "while it changes. Nothing was changed.", error=True)
        return
    # The configured backup destination (issue #727), checked like the Backup
    # screen checks it: one that has gone or been unmounted is refused.
    try:
        root = await lane.run(check_backup_destination, db_path, identity_dir)
    except (BackupScheduleError, OSError) as exc:
        _announce(session, f"{what} needs a usable backup destination first: {exc} Nothing was changed.",
                  error=True)
        return
    destination = default_backup_destination(db_path, root=root)
    # No season number here: the stored one can be stale until the change
    # settles any natural cutoffs that passed while the world sat idle, so
    # the numbers are reported from the change itself afterwards.
    await session.write_line("")
    await session.write_line(colored(
        f"{what}: settles any season that has already ended, then starts the next one with the "
        "normal competitive reset"
        + (", and also clears every receipt." if reset else ", keeping receipts."), fg_color=VALUE_COLOR))
    await session.write_line(colored(
        "Player identities, handles and account age are kept; nobody gets the newcomer grace period again. "
        "A complete node backup is taken and verified first, and maintenance stays on afterwards.",
        fg_color=MUTED_COLOR))
    await session.write_line(
        colored("Backup: ", fg_color=LABEL_COLOR) + colored(sanitize_text(str(destination)), fg_color=METADATA_COLOR))
    await write_prompt(session, "Reason, recorded in the world's audit (1-240 characters; blank cancels): ")
    reason = (await session.read_line()).strip()
    if not reason:
        _announce_line(session, "Cancelled. Nothing was changed.")
        return
    if len(reason) > 240:
        _announce(session, "The reason is longer than 240 characters. Nothing was changed.", error=True)
        return
    await write_prompt(session, f"Type the world filename {world.name!r} to confirm, or anything else to cancel: ")
    entered = await session.read_line()
    # Exact, or with stray surrounding spaces -- but a name that really has
    # them can still be typed exactly.
    if entered != world.name and entered.strip() != world.name:
        _confirmation_refused(session, entered, world.name, kept="Nothing was changed.")
        return
    latest = await lane.run(get_door, door.id)
    if latest is None or (await asyncio.to_thread(_war_dialer_world_of, db_path, latest))[0] != world:
        _announce(session, "This door's world changed while you were confirming. Nothing was changed.", error=True)
        return
    try:
        still = await lane.run(check_backup_destination, db_path, identity_dir)
    except (BackupScheduleError, OSError) as exc:
        _announce(session, f"The backup destination is no longer usable: {exc} Nothing was changed.", error=True)
        return
    if still != root:
        _announce(session, "The backup destination changed while you were confirming. Nothing was changed.",
                  error=True)
        return
    await session.write_line(colored("Backing up the node, then changing the competition...", fg_color=MUTED_COLOR))
    audit_failure: str | None = None

    async def _record(outcome: _CompetitionOutcome) -> None:
        # Runs even if this session drops while the change is in flight: a
        # change that happened must reach the node's audit log.
        nonlocal audit_failure
        if outcome.status is None:
            return
        entry = outcome.status["recent_operations"][-1] if outcome.status["recent_operations"] else {}
        try:
            await lane.run(
                record_action, actor=actor, action="war_dialer_reset" if reset else "war_dialer_season",
                object_type="door", object_id=door.id,
                detail=f"door={door.name!r} world={str(world)!r} "
                       f"season={entry.get('before_season')}->{entry.get('after_season')} "
                       f"backup={str(destination)!r} reason={reason!r}",
            )
        except (sqlite3.Error, OSError) as exc:
            # The change is committed; say that, and that only the node's
            # own record of it is missing (the world's audit has it).
            _logger.exception("War Dialer %s committed but its node audit entry failed", what)
            audit_failure = sanitize_text(str(exc))[:300]

    outcome = await _owned_worker(
        _war_dialer_change_competition, finish=_record, db_path=db_path, world=world, identity_dir=identity_dir,
        destination=destination, reason=reason, confirm=world.name, reset=reset, operator=actor.username,
        root=root,
    )
    if outcome.failure is not None:
        _announce(session, f"{what} failed: {outcome.failure}", error=True)
        if outcome.backup_attempted and destination.exists():
            if outcome.backup_valid:
                _announce(session, f"The verified backup taken first remains at {destination}.", color=MUTED_COLOR)
            else:
                # Not "remove it": another backup started in the same second
                # could own this path.
                _announce(session, f"{destination} is not a verified backup; check what it is before relying on "
                          "or removing it.", error=True)
        return
    _announce(session, f"{what} done: season {outcome.status['stored_season']} has started. Backup: {destination}. "
              "Maintenance is still on; switch it off when you have checked the world.")
    if audit_failure is not None:
        _announce(session, f"The node's Audit log entry for it could not be written ({audit_failure}); "
                  "the world's own audit records the change.", error=True)


async def _war_dialer_world_screen(session: Session, lane: DatabaseLane, actor: User, door: Door, *,
                                   backup_identity_dir: Path | None = None) -> None:
    """A War Dialer door's world: what `war_dialer_admin status` shows, the
    maintenance switch, and the season controls (issue #726).

    Status plus an action bar (design doc §3.5), paged through `show_detail`
    because ten retained operations do not fit a 24-row terminal. Maintenance
    is a toggle and needs no confirmation: it only closes the world to new
    callers, and it refuses while anyone is inside, the same guard the CLI
    meets. Next season and Competition reset need a live node (for the
    identity directory the backup takes), maintenance on, a reason and the
    typed world filename; unlike the CLI they do not need the node stopped.
    """
    page = 0
    db_path = await lane.run(_node_db_path)
    while True:
        chrome = await _load_chrome(lane, actor)
        # Re-read the registration every time: another SysOp may have pointed
        # this door at a different world since the screen last drew.
        current = await lane.run(get_door, door.id)
        if current is None:
            _announce(session, "Cannot show the world: this door was deleted.", error=True)
            return
        door = current
        world, problem = await asyncio.to_thread(_war_dialer_world_of, db_path, door)
        status = None
        if world is not None and problem is None:
            status, problem = await asyncio.to_thread(_war_dialer_world_state, db_path, world)
        rows: list[Field | Note | Table] = []
        if world is not None:
            rows.append(Field("Path", str(world)))
        if problem is not None:
            rows.append(Note(f"Cannot read this world: {problem}", color=ERROR_COLOR))
        elif status is None:
            rows.append(Note("No world yet. It is created the first time someone plays."))
        else:
            maintenance = status["maintenance"] == "on"
            rows += [
                Field("Maintenance",
                      status_badge("ON", tone="warning", unicode_style=chrome.unicode_style) if maintenance
                      else status_badge("OFF", tone="neutral", unicode_style=chrome.unicode_style), styled=True),
                Field("Season", str(status["stored_season"])),
                Field("Schema", str(status["schema"])),
                Field("Owner", str(status["owner"] or "(unbound)"),
                      color=VALUE_COLOR if status["owner"] else WARNING_COLOR),
                Field("Players", str(status["players"])),
                Field("Exchanges", str(status["exchanges"])),
                Field("Events", str(status["events"])),
            ]
            if maintenance:
                rows.append(Note("Closed to new callers until you switch maintenance off."))
            if backup_identity_dir is None:
                rows.append(Note(
                    "Next season and Competition reset run from a live node's console, or with "
                    "python -m netbbs.doors.war_dialer_admin while the node is stopped."))
            elif not maintenance:
                rows.append(Note("Next season and Competition reset need maintenance on first."))
        sections = [Section("World", rows)]
        if status is not None:
            operations = await lane.run(_war_dialer_operation_rows, status["recent_operations"])
            sections.append(Section("Recent operations", [
                Table(["When", "Action", "Operator", "Reason"], operations, flex=3) if operations
                else Note("None recorded.")
            ]))
        actions: list[tuple[str, str]] = []
        if status is not None:
            actions.append(("m", menu_key("M", "aintenance " + ("off" if status["maintenance"] == "on" else "on"))))
            if backup_identity_dir is not None:
                actions += [("n", menu_key("N", "ext season")), ("c", menu_key("C", "ompetition reset"))]
        actions += [("r", menu_key("R", "efresh")), _BACK_ACTION]
        choice, page = await show_detail(
            session,
            title=_detail_title(session, chrome, f"{sanitize_text(door.name)} — world"),
            sections=sections, actions=actions, page=page,
            redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
            help_title="War Dialer world help",
            help_about=(
                "The state of this War Dialer world. Maintenance closes it to new callers; a new season or a "
                "competition reset needs maintenance on and asks for a reason."
            ),
        )

        if choice == "b":
            return
        elif choice == "m" and status is not None:
            enable = status["maintenance"] != "on"
            latest = await lane.run(get_door, door.id)
            if latest is None or (await asyncio.to_thread(_war_dialer_world_of, db_path, latest))[0] != world:
                _announce(session, "This door's world changed while the screen was open; showing it now. "
                          "Nothing was switched.", error=True)
                continue
            failure = await asyncio.to_thread(_war_dialer_set_maintenance, db_path, world, enable, actor.username)
            if failure is not None:
                _announce(session, f"Could not switch maintenance {'on' if enable else 'off'}: {failure}",
                          error=True)
                continue
            await lane.run(record_action, actor=actor, action="war_dialer_maintenance", object_type="door",
                           object_id=door.id,
                           detail=f"door={door.name!r} world={str(world)!r} maintenance={'on' if enable else 'off'}")
            _announce(session, "Maintenance on. New callers are told the world is closed."
                      if enable else "Maintenance off. The world is open to callers again.")
        elif choice in ("n", "c") and status is not None and backup_identity_dir is not None:
            await _war_dialer_competition_flow(
                session, lane, actor, door, world, status, reset=choice == "c",
                identity_dir=backup_identity_dir, db_path=db_path,
            )


async def _delete_door_screen(session: Session, lane: DatabaseLane, actor: User, door: Door,
                              *, door_services: Any = None) -> bool:
    await session.write_line(
        colored("\r\nThis permanently removes the door from the catalogue. This cannot be undone.", fg_color=MUTED_COLOR)
    )
    if door.profile and door.profile.service and door_services is None:
        # Standalone administration cannot reach a running node's supervisor,
        # so the row goes but the companion keeps running and restarting with
        # no catalogue entry and no control left to stop it.
        await session.write_line(colored(
            "This door has a companion service. This console cannot stop it: if a node is running, "
            "it will keep supervising and restarting that process until the node is restarted.",
            fg_color=ALERT_COLOR))
    await write_prompt(
        session, f"Type the door name {door.name!r} to confirm, or anything else to cancel: "
    )
    confirmation = (await session.read_line()).strip()
    if confirmation != door.name:
        _confirmation_refused(session, confirmation, door.name)
        return False
    await lane.run(delete_door, door, deleted_by=actor)
    _announce_line(session, f"{door.name!r} deleted.")
    return True


# -- channels (design doc) --------------------------------------------------
#
# Mirrors the board/area sections above, structurally, but with no
# pending-queue equivalent: channels have no moderated-content/approval
# workflow the way boards/file areas do (see netbbs.chat.channels'
# module docstring — chat messages aren't even persisted beyond bounded
# scrollback). Membership admin (invite/kick/mute/ban) is also
# deliberately not duplicated here — it's already fully reachable via
# in-chat commands (/invite, /kick, /mute, /ban, /members) for anyone
# holding the relevant ChannelPermission grant, so there's no existing-
# but-UI-less gap to close for it the way there was for post/file
# approval.


# -- categories ----------------------------------------------------------


# -- moderator grants -----------------------------------------------------
