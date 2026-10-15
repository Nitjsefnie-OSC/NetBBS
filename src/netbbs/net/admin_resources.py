"""Listing and detail helpers shared by the SysOp resource screens.

Boards, file areas, chat channels and Communities list and describe
themselves the same way: effective access, the level and gate columns, Link
reach, carried-copy hiding and removal. Split out of `netbbs.net.admin_flow`
(issue #1311).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from netbbs.age_requirement import VERIFIED as VERIFIED_AGE
from netbbs.auth.users import User, get_user_by_id
from netbbs.chat.channels import Channel
from netbbs.communities import (
    Community,
    get_effective_age_requirement,
    get_effective_min_age,
    get_effective_min_read_level,
    get_effective_min_write_level,
    get_effective_name_requirement,
)
from netbbs.files.areas import FileArea
from netbbs.link.boards import LinkContext
from netbbs.link.enforcement import node_transport_state
from netbbs.link.node_profiles import identity_for_peer, latest_identity_observation
from netbbs.link.carry import KIND_LABELS, CarryDecisionError, remove_linked_or_local
from netbbs.net.node_map_flow import own_content_at_peer
from netbbs.moderation.roles import BoardPermission, list_grants_for_object
from netbbs.net.picker import ListColumn
from netbbs.net.session import Session, write_prompt
from netbbs.rendering import (
    ERROR_COLOR,
    GATE_COLOR,
    MUTED_COLOR,
    VALUE_COLOR,
    WARNING_COLOR,
    colored,
    sanitize_text,
)
from netbbs.rendering.detail import Field, Note, Section, render_sections
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.net.admin_ui import _announce_line, _confirmation_refused


_MAX_REACH_ROWS = 6


async def _peer_reach_rows(lane: DatabaseLane, link_context: LinkContext, genesis) -> list[Field | Note]:
    """Where one of this node's own linked resources has got to (issue
    #844): per verified peer, whether it holds it, refused it, or is not
    sent it while on probation here. Nothing for a resource another node
    originated -- its origin pushes it, not this node."""
    if genesis is None or genesis.payload.get("origin_fingerprint") != link_context.node_identity.fingerprint:
        return []
    node = link_context.link_node
    peers = list(node.peers)
    if not peers:
        return [Field("At peers", "no verified peers yet", color=MUTED_COLOR)]
    states = await lane.run(lambda db: {fp: node_transport_state(db, fp).value for fp in peers})
    labelled = sorted(((_linked_node_label(link_context, fp), fp) for fp in peers), key=lambda item: item[0].casefold())
    rows: list[Field | Note] = []
    for label, fingerprint in labelled[:_MAX_REACH_ROWS]:
        text, color = own_content_at_peer(states[fingerprint], node.peer_exchange.get(fingerprint), genesis.content_id)
        rows.append(Field(f"At {sanitize_text(label)}", text, color=color))
    if len(labelled) > _MAX_REACH_ROWS:
        rows.append(Note(f"... and {len(labelled) - _MAX_REACH_ROWS} more; Link status -> Peers shows each node."))
    return rows


def _linked_announcement(name: str) -> str:
    """What `[L]ink` says once saved (issue #844): who gets it, and where
    to look later, since the push itself happens on a later sync pass."""
    return (
        f"Linked {name!r}. Peers you have established get it on the next sync pass; "
        "its NetBBS Link rows here show which ones hold it."
    )


def _sections_text(sections: Sequence[Section], *, width: int, unicode_style: bool) -> str:
    """Detail sections as one block of text, for a resource's own screen
    to show after its fields (issue #1081): the same rendering
    `_write_sections` writes, a blank row before each group."""
    lines: list[str] = []
    for block in render_sections(sections, width=width, unicode_style=unicode_style):
        lines.append("")
        lines.extend(block.lines)
    return "\r\n".join(lines)


@dataclass(frozen=True)
class _Effective:
    """The post-cascade values for one resource. `read`/`write` are
    `None` for a chat channel, which has a single `min_level` and no
    read/write split to inherit."""

    read: int | None
    write: int | None
    min_age: int | None
    name_requirement: str | None
    # Whether the age must be verified (issue #1082), after the cascade.
    age_requirement: str | None = None


def _effective_for(db: Database, resource, *, levels: bool = True) -> _Effective:
    return _Effective(
        read=get_effective_min_read_level(db, resource) if levels else None,
        write=get_effective_min_write_level(db, resource) if levels else None,
        min_age=get_effective_min_age(db, resource),
        name_requirement=get_effective_name_requirement(db, resource),
        age_requirement=get_effective_age_requirement(db, resource),
    )


def _effective_by_id(db: Database, resources, *, levels: bool = True) -> dict[int, _Effective]:
    """Resolved for the whole list in the caller's existing lane pass,
    not per row: `column_values_of` and `description_of` are pure
    synchronous callbacks with no database of their own (the same
    contract `FieldSpec.render` keeps), so the cascade has to be walked
    here, where a `db` is already in hand."""
    return {r.id: _effective_for(db, r, levels=levels) for r in resources}


def _describe_resource(read, write, status: str, effective: _Effective) -> str:
    """Gates lead, deliberately.

    This string is what a narrow terminal shows, and a narrow terminal
    is exactly where it gets truncated -- appending the gates put them
    first under the knife, so a 50-column row read
    "... - read 10/write 0, op..." and lost the only field this issue
    was filed about. Whoever can enter is the least recoverable fact in
    the row and the least guessable from context, so it goes first and
    the levels take the truncation instead."""
    gates, _ = _gate_cell(effective.min_age, effective.name_requirement, effective.age_requirement)
    tail = f"read {read}/write {write}, {status}"
    return tail if gates == "-" else f"{gates}, {tail}"


def _level_cell(value: int | None) -> str:
    """A Community's own default-level cell.

    Only Communities use this now: a leaf resource's columns resolve
    through the cascade and always land on a number. A Community sits
    at the top with nothing above it, so `None` there means "sets no
    default", not "inherits one" -- the word matters, since "inherit"
    on a Community row would point at a parent that does not exist."""
    return str(value) if value is not None else "none"


def _gate_cell(
    min_age: int | None, name_requirement: str | None, age_requirement: str | None = None,
) -> tuple[str, SegmentColor]:
    """The gates column, and the point of the exercise (issue #528).

    A resource can be gated on caller age, on identity attestation, or
    both, and none of it appeared anywhere in a list row -- so an area
    that turns away most of the node read exactly like one that turns
    away nobody. Shown as compact tags rather than prose because this
    is a column, and colored `GATE_COLOR` only when a gate is actually
    present: an ungated row should stay quiet, and a gated one should
    catch the eye while scanning.

    `name` is attestation required; `name+` additionally displays the
    attested real name alongside the caller's posts
    ("verified_and_displayed"). An age that must be verified (issue
    #1082) reads `18+ verified`.
    """
    tags: list[str] = []
    # Truthiness, not `is not None` (Codex review): `meets_age` opens
    # with `if not min_age: return True` and documents "unset/0 -> always
    # passes (no gate)", so an explicit 0 -- a supported way to override
    # an inherited age gate -- admits everyone. Rendering "0+" would
    # advertise a restriction enforcement does not apply, which is the
    # same class of lie as omitting a gate that it does.
    if min_age:
        tags.append(f"{min_age}+ verified" if age_requirement == VERIFIED_AGE else f"{min_age}+")
    if name_requirement == "verified_and_displayed":
        tags.append("name+")
    elif name_requirement:
        tags.append("name")
    if not tags:
        return ("-", MUTED_COLOR)
    return (" ".join(tags), GATE_COLOR)


# Read/write levels are right-aligned so the numbers form a column that
# can be compared down the page; the words beside them are not.
_LEVEL_COLUMNS = [
    ListColumn("read", 7, VALUE_COLOR, align_right=True),
    ListColumn("write", 7, VALUE_COLOR, align_right=True),
]


_STATUS_COLUMN = ListColumn("status", 9, VALUE_COLOR)


# Eleven, not nine or ten: `_prompt_min_age` parses a bare `int()`
# with no upper bound at all, so "1000+ name+" is reachable from the
# UI today, and a gates column that truncates the gate is worse than
# no column. Eleven covers every age a human could plausibly be typed
# as, including a fat-fingered one. The real defect behind this --
# that an unbounded age is accepted at all, and a typo'd 1000 makes
# `meets_age` refuse every caller and silently lock the resource --
# is an input-validation problem, filed separately rather than
# papered over with a wider column.
_GATES_COLUMN = ListColumn("gates", 11, GATE_COLOR)


# Issue #681: how much is there, and how much waits. "12 +3" is 12 posts
# readers see and 3 held for a moderator; a pinned resource says so in
# its status. At 80 columns the name still keeps the table's minimum.
_BOARD_STATUS_COLUMN = ListColumn("status", 12, VALUE_COLOR)


def _listing_status(moderated: bool, pinned: bool, to_review: bool = False) -> str | tuple[str, SegmentColor]:
    """Moderated or open, and pinned to the top of its list -- or, for a
    resource Link carried in that the SysOp has not opened yet, that it
    waits for a look (issue #681)."""
    if to_review:
        return ("to review", WARNING_COLOR)
    if pinned:
        return "mod., pinned" if moderated else "open, pinned"
    return "moderated" if moderated else "open"


def _count_cell(listed: int, pending: int) -> tuple[str, SegmentColor]:
    """"12 +3": listed, and held for a moderator (in warning color)."""
    return (f"{listed} +{pending}", WARNING_COLOR) if pending else (str(listed), VALUE_COLOR)


def _area_columns(
    area: FileArea, effective: _Effective, counts: tuple[int, int] = (0, 0), to_review: bool = False,
) -> list[str | tuple[str, SegmentColor]]:
    return [
        str(effective.read),
        str(effective.write),
        _listing_status(area.moderated, area.pinned, to_review),
        _count_cell(*counts),
        _gate_cell(effective.min_age, effective.name_requirement, effective.age_requirement),
    ]


def _channel_access(channel: Channel) -> str:
    access = ["members"] if channel.members_only else []
    if channel.hidden:
        access.append("hidden")
    return "+".join(access) if access else "open"


def _channel_columns(
    channel: Channel, effective: _Effective, to_review: bool = False,
) -> list[str | tuple[str, SegmentColor]]:
    # `min_level` is a plain non-nullable int with no Community default
    # behind it, so unlike a board's read/write there is nothing to
    # resolve -- only the gates cascade for a channel.
    return [
        str(channel.min_level),
        ("to review", WARNING_COLOR) if to_review else _channel_access(channel),
        _gate_cell(effective.min_age, effective.name_requirement, effective.age_requirement),
    ]


def _community_columns(community: Community) -> list[str | tuple[str, SegmentColor]]:
    """A Community's own columns are its *defaults* -- the floor every
    board, area and channel inside it inherits unless it sets its own.
    Exactly as invisible as a leaf resource's gates were, and with
    wider consequences, since one edit here moves every child at once."""
    return [
        _level_cell(community.default_min_read_level),
        _level_cell(community.default_min_write_level),
        "no" if community.hidden else "yes",
        _gate_cell(community.default_min_age, community.default_name_requirement, community.default_age_requirement),
    ]


def _place_label(place: int, total: int, *, pinned: bool) -> str:
    """Where a board or area sits in the callers' list (issue #839): among
    those in the same category and Community, pinned ones apart. It replaced
    the "Pinned" row, which it now says, so the screen gained no row."""
    return f"{place + 1} of {total}" + (", pinned first" if pinned else "")


async def _warn_about_changed_node_identity(
    session: Session,
    lane: DatabaseLane,
    fingerprint: str,
    *,
    role: str,
) -> None:
    identity_notice = await lane.run(latest_identity_observation, fingerprint)
    if identity_notice is None or identity_notice.severity != "security":
        return
    await session.write_line(
        colored(
            "Caution: this familiar node name now has a different cryptographic identity. "
            f"{role} technical identity is {sanitize_text(fingerprint)}.",
            fg_color=MUTED_COLOR,
            bold=True,
        )
    )


def _linked_node_label(link_context, fingerprint) -> str:
    """Present a linked node by its admitted profile, else by its signed
    fingerprint -- never by a shared placeholder that would make two
    unavailable origins indistinguishable."""
    peer = link_context.link_node.peers.get(fingerprint) if isinstance(fingerprint, str) else None
    if peer is not None:
        return identity_for_peer(peer).label
    if isinstance(fingerprint, str) and fingerprint:
        return fingerprint
    return "an unknown linked node"


# How many moderators the moderation screen names; the rest are counted.
_MODERATORS_SHOWN = 5


def _moderator_lines(db: Database, object_type: str, object_id: int) -> list[str]:
    """Who may moderate this board or file area, and with what (issue
    #678): every grant that applies to it, its own and blanket ones, as
    `list_grants_for_object` answers, one line each. Shown above its
    history rather than on its detail screen, which at 80x24 has no row to
    spare (Codex review on #797). Sanitized: a username is its owner's
    own text."""
    grants = list_grants_for_object(db, object_type=object_type, object_id=object_id)
    if not grants:
        return ["none: only SysOps moderate here"]
    lines = []
    for grant in grants[:_MODERATORS_SHOWN]:
        user = get_user_by_id(db, grant.user_id)
        name = user.username if user is not None else "(deleted account)"
        permissions = ", ".join(
            flag.name.lower() for flag in BoardPermission if flag & grant.permissions and flag.name
        )
        if grant.object_id is not None:
            scope = ""
        elif grant.community_id is not None:
            scope = " (Community-wide)"
        else:
            scope = " (all local ones)"
        lines.append(sanitize_text(f"{name}: {permissions}{scope}"))
    if len(grants) > _MODERATORS_SHOWN:
        lines.append(f"...and {len(grants) - _MODERATORS_SHOWN} more (see [R]evoke moderator)")
    return lines


_EXCLUDED_AFTER_DELETE = " It is excluded from Link here and will not come back; Link status lists it."


async def _hide_carried_screen(
    session: Session, lane: DatabaseLane, actor: User, kind: str, resource_id: str, name: str,
    *, own_fingerprint: str | None,
) -> bool:
    """Deleting a carried Link resource whose origin is another node (issue
    #683, decided 2026-09-26): it is hidden from callers and no longer carried,
    and everything in it is kept, so Restore brings it back exactly as it was;
    Purge under Link status -> Excluded deletes it for real."""
    label = KIND_LABELS[kind]
    await session.write_line(
        colored(
            f"\r\nThis {label} is carried from another node. Deleting it here excludes it: "
            "callers stop seeing it and this node stops carrying it, but everything in it is "
            "kept. Link status -> Excluded restores it exactly as it was, or purges it for good.",
            fg_color=MUTED_COLOR,
        )
    )
    await write_prompt(session, f"Type the {label} name {name!r} to confirm, or anything else to cancel: ")
    confirmation = (await session.read_line()).strip()
    if confirmation != name:
        _confirmation_refused(session, confirmation, name)
        return False
    return await _remove_resource(
        session, lane, actor, kind, resource_id, name, own_fingerprint=own_fingerprint, delete=None,
    )


async def _remove_resource(
    session: Session, lane: DatabaseLane, actor: User, kind: str, resource_id: str, name: str, *,
    own_fingerprint: str | None, delete,
) -> bool:
    """Carry out a confirmed delete as one lane job (`remove_linked_or_local`),
    and say what actually happened -- which is the reversible hide if the
    resource's origin moved away while the SysOp was confirming."""
    def _job(db: Database) -> str:
        return remove_linked_or_local(
            db, kind, resource_id, actor=actor, own_fingerprint=own_fingerprint,
            delete=(lambda: delete(db)) if delete is not None else None,
        )

    try:
        outcome = await lane.run(_job)
    except CarryDecisionError as exc:
        _announce_line(session, colored(f"Could not delete {name!r}: {exc}", fg_color=ERROR_COLOR))
        return False
    if outcome == "hidden":
        _announce_line(session, f"{name!r} excluded from this node; Link status -> Excluded restores or purges it.")
    else:
        _announce_line(session, f"{name!r} deleted." + (_EXCLUDED_AFTER_DELETE if outcome == "deleted-excluded" else ""))
    return True
