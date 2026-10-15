"""
Link Communities (design doc §6.5; decided in §16, issue #1308; built in
issue #1310).

A Link Community is the same `communities` row as a local one, announced by
its origin in a signed `community_genesis`, with its membership in a signed
`community_members` list and its end in a terminal `community_closure`
(`netbbs.link.events`). The row carries the Link columns: `link_id` (the
content-addressed id on the wire), `link_genesis_json` (the Link marker),
`link_origin_fingerprint`, `link_members_json` (the origin's own latest list)
and `link_closure_json`.

**At the origin.** A local Community is promoted from its screen
(`promote_community`): the genesis plus a membership list of its members that
are already linked and originate here. Promotion links nothing: filing a
private board under a Link Community never publishes it. Linking, unlinking
or moving one of this node's own linked resources changes what the list
should say, and `refresh_own_community_members` -- run every sync pass and
right after promotion -- signs a new list whenever it does. Unlinking or
deleting the Community signs a closure (`close_own_community`).

**At a carrier.** A received genesis is always *offered* (kind
`communities` in `link_carry_decisions`): subscribing is a standing rule
about future content, so a SysOp makes it, whatever the node-wide intake
does for boards. Accepting the offer is the subscription
(`subscribe_community`): it creates the local copy -- a name clash resolved
as #671 resolves a board's -- and from then on every member the Community
lists is accepted as if the SysOp had accepted its offer, within the
per-type carry caps; a member past a cap waits in the offered list with
reason `cap`, and a member already put in front of the SysOp is not
silently taken later (#561 Decision 4): only members new to a list, or
listed when the subscription starts, are accepted. Members move into the
copy unless the SysOp placed them (`community_placed_locally`); a member the
origin removes, or whose origin moves away (§9.4), moves back out, and stays
carried. Excluding a member keeps it out; deleting the copy is leaving the
Community: it accepts nothing more and sheds nothing. A closure turns the
copy into a plain local Community marked closed at its origin.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass

from netbbs.auth.users import User
from netbbs.link.events import (
    COMMUNITY_CLOSURE_OBJECT_TYPE,
    COMMUNITY_GENESIS_OBJECT_TYPE,
    COMMUNITY_MEMBER_KINDS,
    COMMUNITY_MEMBERS_OBJECT_TYPE,
    MAX_COMMUNITY_MEMBERS_PER_KIND,
    CommunityClosure,
    CommunityGenesis,
    CommunityMembers,
    build_community_closure,
    build_community_genesis,
    build_community_members,
    event_content_id,
)
from netbbs.link.node_identity import NodeIdentity
from netbbs.link.store import save_event
from netbbs.moderation.log import record_action_without_commit
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_iso, utc_now_iso

_logger = logging.getLogger(__name__)

KIND = "communities"
"""The carry-decision and inventory kind of a Link Community."""

_LOCAL = {"boards": ("boards", "board_id"), "channels": ("channels", "channel_id"), "file_areas": ("file_areas", "area_id")}
_EVENT_CLASSES = {
    COMMUNITY_GENESIS_OBJECT_TYPE: CommunityGenesis,
    COMMUNITY_MEMBERS_OBJECT_TYPE: CommunityMembers,
    COMMUNITY_CLOSURE_OBJECT_TYPE: CommunityClosure,
}


class LinkCommunitiesError(Exception):
    """A Link Community action the SysOp asked for cannot be done."""


@dataclass(frozen=True)
class CarryCaps:
    """What a subscription's automatic acceptance is bounded by: the node's
    per-type carry caps (§9.3), and this node's own fingerprint, which says
    which carried resources count against them. `None` caps nothing."""

    boards: int | None
    channels: int | None
    file_areas: int | None
    own_fingerprint: str | None
    max_remote_files_per_area: int | None = None

    def for_kind(self, kind: str) -> int | None:
        return {"boards": self.boards, "channels": self.channels, "file_areas": self.file_areas}[kind]


@dataclass(frozen=True)
class CommunityLink:
    """What a Community's screen says about its Link side."""

    link_id: str
    origin_fingerprint: str
    own: bool
    closed: bool
    members: dict[str, tuple[str, ...]]
    left: bool = False

    @property
    def member_count(self) -> int:
        return sum(len(ids) for ids in self.members.values())


def _event_from_raw(raw: dict):
    return _EVENT_CLASSES[raw["envelope"]["object_type"]].from_dict(raw)


def _row_by_link_id(db: Database, link_id: str):
    return db.connection.execute(
        "SELECT * FROM communities WHERE link_id = ? AND link_genesis_json IS NOT NULL", (link_id,)
    ).fetchone()


def _stored(db: Database, link_id: str, object_type: str) -> list[dict]:
    return [
        json.loads(row["envelope_json"])
        for row in db.connection.execute(
            "SELECT envelope_json FROM link_events WHERE community_id = ? AND object_type = ? ORDER BY received_at",
            (link_id, object_type),
        )
    ]


def stored_genesis(db: Database, link_id: str) -> CommunityGenesis | None:
    """The Community's genesis: on its row, else as received."""
    row = _row_by_link_id(db, link_id)
    if row is not None:
        return CommunityGenesis.from_dict(json.loads(row["link_genesis_json"]))
    held = _stored(db, link_id, COMMUNITY_GENESIS_OBJECT_TYPE)
    return CommunityGenesis.from_dict(held[0]) if held else None


def is_closed(db: Database, link_id: str) -> bool:
    row = _row_by_link_id(db, link_id)
    if row is not None and row["link_closure_json"] is not None:
        return True
    return bool(_stored(db, link_id, COMMUNITY_CLOSURE_OBJECT_TYPE))


def _order_key(raw: dict) -> tuple[str, str]:
    return str(raw["envelope"]["payload"].get("created_at", "")), event_content_id(raw["envelope"])


def members_in_force_event(db: Database, link_id: str) -> dict | None:
    """The membership list in force for `link_id`: the newest by
    `created_at` (ties by content id, so every node picks the same one), from
    the origin's own row or from what was received."""
    candidates = _stored(db, link_id, COMMUNITY_MEMBERS_OBJECT_TYPE)
    row = _row_by_link_id(db, link_id)
    if row is not None and row["link_members_json"] is not None:
        candidates.append(json.loads(row["link_members_json"]))
    return max(candidates, key=_order_key) if candidates else None


def members_in_force(db: Database, link_id: str) -> dict[str, tuple[str, ...]]:
    raw = members_in_force_event(db, link_id)
    members = raw["envelope"]["payload"]["members"] if raw is not None else {}
    return {kind: tuple(members.get(kind, ())) for kind in COMMUNITY_MEMBER_KINDS}


def community_link(db: Database, community_id: int, own_fingerprint: str | None) -> CommunityLink | None:
    """The Link side of local Community `community_id`, or `None` for a
    Community that is not, and never was, a Link Community here."""
    row = db.connection.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    if row is None or row["link_genesis_json"] is None:
        return None
    link_id = row["link_id"]
    left = db.connection.execute(
        "SELECT 1 FROM link_carry_decisions WHERE kind = ? AND resource_id = ? AND state = 'excluded'",
        (KIND, link_id),
    ).fetchone() is not None
    return CommunityLink(
        link_id=link_id,
        origin_fingerprint=row["link_origin_fingerprint"],
        # Promoted here: only an origin keeps its own list on the row.
        own=row["link_members_json"] is not None or (
            own_fingerprint is not None and row["link_origin_fingerprint"] == own_fingerprint
        ),
        closed=row["link_closure_json"] is not None,
        members=members_in_force(db, link_id),
        left=left,
    )


def linked_community_ids(db: Database) -> set[int]:
    """Every local Community that is a live Link Community -- promoted here or
    a carried copy, not closed -- for the linked marker (#1104)."""
    return {
        row["id"] for row in db.connection.execute(
            "SELECT id FROM communities WHERE link_genesis_json IS NOT NULL AND link_closure_json IS NULL"
        )
    }


def _later(previous: str | None) -> str:
    """Now, or just after `previous` if the clock has not moved past it: a
    new membership list must sort after the one it replaces."""
    now = utc_now_iso()
    if previous is None or now > previous:
        return now
    try:
        return utc_iso(parse_utc_iso(previous) + datetime.timedelta(microseconds=1))
    except ValueError:
        return now


# -- at the origin -------------------------------------------------------------


def _resource_origin(kind: str, row) -> str | None:
    """A linked resource's current origin: a board's moves (§9.4)."""
    if kind == "boards" and row["link_origin_fingerprint"]:
        return row["link_origin_fingerprint"]
    origin = json.loads(row["link_genesis_json"])["envelope"]["payload"].get("origin_fingerprint")
    return origin if isinstance(origin, str) else None


def own_member_ids(db: Database, community_id: int, own_fingerprint: str) -> dict[str, list[str]]:
    """The members a Community promoted here lists (Decision 1 and 6): its
    boards, channels and file areas that are linked, not hidden, and
    originate here now. At most `MAX_COMMUNITY_MEMBERS_PER_KIND` per kind, the
    lowest ids first; past that a member is left out, and logged."""
    members: dict[str, list[str]] = {}
    for kind, (table, column) in _LOCAL.items():
        ids = sorted(
            row[column]
            for row in db.connection.execute(
                f"SELECT * FROM {table} WHERE community_id = ? AND link_genesis_json IS NOT NULL "
                "AND link_hidden_at IS NULL",
                (community_id,),
            )
            if _resource_origin(kind, row) == own_fingerprint
        )
        if len(ids) > MAX_COMMUNITY_MEMBERS_PER_KIND:
            _logger.warning(
                "Link Community %d has %d linked %s; its membership list names the first %d",
                community_id, len(ids), kind, MAX_COMMUNITY_MEMBERS_PER_KIND,
            )
            ids = ids[:MAX_COMMUNITY_MEMBERS_PER_KIND]
        members[kind] = ids
    return members


def _sign_members(db: Database, row, node_identity: NodeIdentity, previous: dict | None) -> CommunityMembers:
    previous_at = previous["envelope"]["payload"]["created_at"] if previous is not None else None
    return build_community_members(
        signing_identity=node_identity.signing_key,
        origin_fingerprint=node_identity.fingerprint,
        community_id=row["link_id"],
        members=own_member_ids(db, row["id"], node_identity.fingerprint),
        created_at=_later(previous_at),
    )


def promote_community(db: Database, community_id: int, *, node_identity: NodeIdentity, actor: User | None = None) -> list[dict]:
    """Put a local Community into Link scope (Decision 6): sign its genesis,
    with its name, description and defaults as recommendations, and a
    membership list of its members already linked from here. Links nothing.
    Returns the two events, for the caller to register with the running
    `LinkNode` (on the event loop, as `link_board`'s caller does)."""
    row = db.connection.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    if row is None:
        raise LinkCommunitiesError("that Community no longer exists")
    if row["link_genesis_json"] is not None and row["link_closure_json"] is None:
        if row["link_origin_fingerprint"] == node_identity.fingerprint:
            raise LinkCommunitiesError(f"Community {row['name']!r} is already Linked")
        raise LinkCommunitiesError(
            f"Community {row['name']!r} is another node's Link Community; only its origin announces it"
        )
    genesis = build_community_genesis(
        signing_identity=node_identity.signing_key,
        origin_fingerprint=node_identity.fingerprint,
        name=row["name"],
        created_at=utc_now_iso(),
        description=row["description"],
        default_min_read_level=row["default_min_read_level"],
        default_min_write_level=row["default_min_write_level"],
        default_min_age=row["default_min_age"],
        default_name_requirement=row["default_name_requirement"],
        default_age_requirement=row["default_age_requirement"],
    )
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        # A closed copy of another node's Community promoted here becomes this
        # node's own: what it held of the old one stays in `link_events`.
        db.connection.execute(
            """UPDATE communities SET link_id = ?, link_genesis_json = ?, link_origin_fingerprint = ?,
                      link_members_json = NULL, link_closure_json = NULL
                WHERE id = ?""",
            (genesis.payload["community_id"], json.dumps(genesis.to_dict()), node_identity.fingerprint, community_id),
        )
        row = db.connection.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
        members = _sign_members(db, row, node_identity, None)
        db.connection.execute(
            "UPDATE communities SET link_members_json = ? WHERE id = ?", (json.dumps(members.to_dict()), community_id)
        )
        if actor is not None:
            record_action_without_commit(
                db, actor=actor, action="link_community", object_type="community", object_id=community_id,
                detail=f"Linked Community {row['name']!r} as {genesis.payload['community_id']}",
            )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return [genesis.to_dict(), members.to_dict()]


def refresh_own_community_members(db: Database, node_identity: NodeIdentity) -> list[dict]:
    """Sign a new membership list for every Community promoted here whose
    members have changed since its last one (Decision 6: linking, unlinking
    or moving a member). Run every sync pass, so every path that changes a
    member -- the board, channel and file area screens, the CLI, an origin
    transfer -- is covered without each having to remember. Returns the new
    lists, for the caller to register with the running `LinkNode`."""
    signed: list[dict] = []
    for row in db.connection.execute(
        "SELECT * FROM communities WHERE link_genesis_json IS NOT NULL AND link_closure_json IS NULL "
        "AND link_origin_fingerprint = ?",
        (node_identity.fingerprint,),
    ).fetchall():
        previous = json.loads(row["link_members_json"]) if row["link_members_json"] is not None else None
        wanted = own_member_ids(db, row["id"], node_identity.fingerprint)
        if previous is not None and previous["envelope"]["payload"]["members"] == wanted:
            continue
        members = _sign_members(db, row, node_identity, previous)
        db.connection.execute(
            "UPDATE communities SET link_members_json = ? WHERE id = ?", (json.dumps(members.to_dict()), row["id"])
        )
        db.connection.commit()
        signed.append(members.to_dict())
    return signed


def close_own_community(
    db: Database, community_id: int, *, node_identity: NodeIdentity, actor: User | None = None
) -> dict | None:
    """Unlink a Community promoted here (Decision 7): sign its terminal
    closure, keep the genesis, the last membership list and the closure in
    `link_events` -- where inventory goes on serving them, so the closure
    reaches every carrier -- and make the row a plain local Community again.
    Returns the closure, or `None` for a Community that is not Linked from
    here. Deleting the Community afterwards is the caller's to do."""
    row = db.connection.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    if (
        row is None or row["link_genesis_json"] is None or row["link_closure_json"] is not None
        or row["link_origin_fingerprint"] != node_identity.fingerprint
    ):
        return None
    closure = build_community_closure(
        signing_identity=node_identity.signing_key,
        origin_fingerprint=node_identity.fingerprint,
        community_id=row["link_id"],
        created_at=utc_now_iso(),
    )
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        for column in ("link_genesis_json", "link_members_json"):
            if row[column] is not None:
                raw = json.loads(row[column])
                save_event(
                    db, sender_fingerprint=node_identity.fingerprint, content_id=event_content_id(raw["envelope"]),
                    object_type=raw["envelope"]["object_type"], envelope=raw, commit=False,
                )
        save_event(
            db, sender_fingerprint=node_identity.fingerprint, content_id=closure.content_id,
            object_type=COMMUNITY_CLOSURE_OBJECT_TYPE, envelope=closure.to_dict(), commit=False,
        )
        db.connection.execute(
            """UPDATE communities SET link_id = NULL, link_genesis_json = NULL, link_origin_fingerprint = NULL,
                      link_members_json = NULL, link_closure_json = NULL
                WHERE id = ?""",
            (community_id,),
        )
        if actor is not None:
            record_action_without_commit(
                db, actor=actor, action="unlink_community", object_type="community", object_id=community_id,
                detail=f"closed Link Community {row['name']!r} ({row['link_id']})",
            )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return closure.to_dict()


def load_own_community_events(db: Database, own_fingerprint: str) -> list:
    """Every community event this node signed that inventory still serves --
    what `netbbs.link.sync._push_own_events` may push, to a peer advertising
    `LINK_COMMUNITIES_CAPABILITY` only."""
    from netbbs.link.store import _all_community_events, served_community_ids

    events = []
    for link_id in served_community_ids(db):
        for raw in _all_community_events(db, link_id).values():
            if raw["envelope"]["payload"].get("origin_fingerprint") == own_fingerprint:
                events.append(_event_from_raw(raw))
    return events


# -- at a carrier ---------------------------------------------------------------


def _decision(db: Database, kind: str, resource_id: str):
    return db.connection.execute(
        "SELECT state, reason FROM link_carry_decisions WHERE kind = ? AND resource_id = ?", (kind, resource_id)
    ).fetchone()


def accept_community_genesis(db: Database, *, envelope: dict, sender_fingerprint: str, content_id: str) -> str:
    """Save a received `community_genesis` and its carry outcome in one
    transaction (Decision 4): it is *offered*, never carried on arrival,
    whatever the node-wide intake does for boards. Returns `"offered"`, or
    `"held"` when it already has a copy here or a decision about it, or is
    closed (nothing to subscribe to)."""
    link_id = envelope["envelope"]["payload"]["community_id"]
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        save_event(
            db, sender_fingerprint=sender_fingerprint, content_id=content_id,
            object_type=COMMUNITY_GENESIS_OBJECT_TYPE, envelope=envelope, commit=False,
        )
        if _row_by_link_id(db, link_id) is not None or _decision(db, KIND, link_id) is not None or is_closed(db, link_id):
            outcome = "held"
        else:
            from netbbs.link.carry import OFFERED, record_carry_decision

            record_carry_decision(db, KIND, link_id, OFFERED, "community", commit=False)
            outcome = "offered"
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return outcome


def subscribed_copy(db: Database, link_id: str):
    """The local copy of a Link Community this node subscribes to: carried
    from another node, not closed, and not left (excluded). A Community
    promoted here always keeps its own membership list on its row, and a copy
    never does, which is what tells the two apart."""
    row = _row_by_link_id(db, link_id)
    if row is None or row["link_closure_json"] is not None or row["link_members_json"] is not None:
        return None
    decision = _decision(db, KIND, link_id)
    if decision is not None and decision["state"] == "excluded":
        return None
    return row


def store_received_members(
    db: Database, *, envelope: dict, sender_fingerprint: str, content_id: str, caps: CarryCaps,
) -> str:
    """Keep a received `community_members` if it is newer than the list in
    force, replacing it -- superseded lists are not kept, so what this node
    stores and serves per Community stays bounded -- and, for a Community
    this node subscribes to, accept the members new to the list and place
    every member. Returns `"stale"`, `"stored"` or `"applied"`."""
    link_id = envelope["envelope"]["payload"]["community_id"]
    current = members_in_force_event(db, link_id)
    if current is not None and _order_key(current) >= _order_key(envelope):
        return "stale"
    previous = members_in_force(db, link_id)
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        db.connection.execute(
            "DELETE FROM link_events WHERE community_id = ? AND object_type = ?",
            (link_id, COMMUNITY_MEMBERS_OBJECT_TYPE),
        )
        save_event(
            db, sender_fingerprint=sender_fingerprint, content_id=content_id,
            object_type=COMMUNITY_MEMBERS_OBJECT_TYPE, envelope=envelope, commit=False,
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    if subscribed_copy(db, link_id) is None:
        return "stored"
    listed = members_in_force(db, link_id)
    accept_listed_members(
        db, link_id, {kind: set(listed[kind]) - set(previous[kind]) for kind in COMMUNITY_MEMBER_KINDS}, caps,
    )
    place_members(db, link_id)
    return "applied"


def store_received_closure(db: Database, *, envelope: dict, sender_fingerprint: str, content_id: str) -> None:
    """Keep a received `community_closure` (Decision 7). A copy here becomes
    a plain local Community marked closed at its origin; its members stay
    carried and where they are. An offer of it is withdrawn: there is nothing
    left to subscribe to."""
    link_id = envelope["envelope"]["payload"]["community_id"]
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        save_event(
            db, sender_fingerprint=sender_fingerprint, content_id=content_id,
            object_type=COMMUNITY_CLOSURE_OBJECT_TYPE, envelope=envelope, commit=False,
        )
        db.connection.execute(
            "UPDATE communities SET link_closure_json = ? WHERE link_id = ? AND link_genesis_json IS NOT NULL",
            (json.dumps(envelope), link_id),
        )
        db.connection.execute(
            "DELETE FROM link_carry_decisions WHERE kind = ? AND resource_id = ? AND state = 'offered'",
            (KIND, link_id),
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()


def _level(value) -> int | None:
    """A recommended level from a remote origin, as this node can store it."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255 else None


def _age(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 150 else None


def subscribe_community(db: Database, link_id: str, *, actor: User | None, caps: CarryCaps) -> int:
    """Accept a Community's offer, which *is* the subscription (Decision 4),
    or take back one this node left (Restore): create the local copy -- the
    origin's name, or #671's suffixed one if a local Community has it -- with
    the origin's recommended defaults, then accept and place its members.
    Returns the copy's local id."""
    from netbbs.age_requirement import carried_age_requirement
    from netbbs.link.local_names import free_local_name

    db.connection.execute("BEGIN IMMEDIATE")
    try:
        decision = _decision(db, KIND, link_id)
        if decision is None:
            raise LinkCommunitiesError("that Community is no longer on offer")
        if is_closed(db, link_id):
            raise LinkCommunitiesError("that Community was closed at its origin; there is nothing to subscribe to")
        if _row_by_link_id(db, link_id) is not None:
            # Left, with the copy kept: taking it back is lifting the exclusion.
            local_id = _row_by_link_id(db, link_id)["id"]
        else:
            genesis = stored_genesis(db, link_id)
            if genesis is None:
                raise LinkCommunitiesError("this node no longer holds that Community's genesis")
            payload = genesis.payload
            local_name = free_local_name(db, "communities", str(payload["name"]), link_id)
            if local_name is None:
                raise LinkCommunitiesError(f"no free local name for {payload['name']!r}")
            name_requirement = payload.get("default_name_requirement")
            db.connection.execute(
                """INSERT INTO communities
                       (name, description, hidden, default_min_read_level, default_min_write_level,
                        default_min_age, default_name_requirement, created_at, position,
                        link_id, link_genesis_json, link_origin_fingerprint)
                   VALUES (?, ?, 0, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position) + 1, 0) FROM communities), ?, ?, ?)""",
                (
                    local_name,
                    payload.get("description") if isinstance(payload.get("description"), str) else None,
                    _level(payload.get("default_min_read_level")),
                    _level(payload.get("default_min_write_level")),
                    _age(payload.get("default_min_age")),
                    name_requirement if name_requirement in ("verified", "verified_and_displayed") else None,
                    utc_now_iso(),
                    link_id,
                    json.dumps(genesis.to_dict()),
                    payload["origin_fingerprint"],
                ),
            )
            local_id = _row_by_link_id(db, link_id)["id"]
            age_requirement = carried_age_requirement(payload)
            if age_requirement is not None:
                db.connection.execute(
                    "UPDATE communities SET default_age_requirement = ? WHERE id = ?", (age_requirement, local_id)
                )
        db.connection.execute(
            "DELETE FROM link_carry_decisions WHERE kind = ? AND resource_id = ?", (KIND, link_id)
        )
        if actor is not None:
            record_action_without_commit(
                db, actor=actor, action="subscribe_link_community", object_type="community", object_id=local_id,
                detail=f"subscribed to Link Community {link_id}",
            )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    listed = members_in_force(db, link_id)
    accept_listed_members(db, link_id, {kind: set(ids) for kind, ids in listed.items()}, caps)
    place_members(db, link_id)
    return local_id


def leave_community(db: Database, community_id: int, *, actor: User | None, delete) -> None:
    """Delete a carried copy, which is leaving the Community (#561 Decision 8):
    it is recorded as excluded, so it accepts nothing more and is not offered
    again, and its members stay carried. A copy already closed at its origin
    is simply deleted. `delete` is the ordinary Community delete."""
    from netbbs.link.carry import EXCLUDED, record_carry_decision

    row = db.connection.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    recorded = False
    if row is not None and row["link_genesis_json"] is not None and row["link_closure_json"] is None:
        record_carry_decision(
            db, KIND, row["link_id"], EXCLUDED, "deleted", actor_user_id=actor.id if actor is not None else None,
        )
        recorded = True
    try:
        delete()
    except BaseException:
        if recorded:
            db.connection.execute(
                "DELETE FROM link_carry_decisions WHERE kind = ? AND resource_id = ?", (KIND, row["link_id"])
            )
            db.connection.commit()
        raise


def _carried_count(db: Database, kind: str, own_fingerprint: str | None) -> int:
    from netbbs.link.boards import carried_board_count
    from netbbs.link.channels import carried_channel_count
    from netbbs.link.files import carried_file_area_count

    return {
        "boards": carried_board_count, "channels": carried_channel_count, "file_areas": carried_file_area_count,
    }[kind](db, own_fingerprint)


def accept_listed_members(db: Database, link_id: str, ids_by_kind: dict[str, set[str]], caps: CarryCaps) -> int:
    """Accept the offered members among `ids_by_kind` as if the SysOp had
    (Decision 4), each within its per-type carry cap: one past it stays
    offered with reason `cap`. Members already carried, excluded, or not yet
    heard of are left alone -- the last are placed when their genesis
    arrives, and carried then only within the cap, as any genesis is. A
    member whose genesis names another origin is ignored (Decision 2).
    Returns how many were accepted. Each acceptance is its own transaction,
    and is idempotent."""
    from netbbs.link.carry import CarryDecisionError, _stored_genesis, accept_offer

    genesis = stored_genesis(db, link_id)
    if genesis is None:
        return 0
    origin = genesis.payload["origin_fingerprint"]
    own = caps.own_fingerprint
    if own is None:
        from netbbs.link.carry import resolve_own_fingerprint

        own = resolve_own_fingerprint(db, None)
    accepted = 0
    for kind in COMMUNITY_MEMBER_KINDS:
        for resource_id in sorted(ids_by_kind.get(kind, ())):
            decision = _decision(db, kind, resource_id)
            if decision is None or decision["state"] != "offered" or decision["reason"] == "accepting":
                continue
            member_genesis = _stored_genesis(db, kind, resource_id)
            if member_genesis is None or member_genesis["envelope"]["payload"].get("origin_fingerprint") != origin:
                continue
            cap = caps.for_kind(kind)
            if cap is not None and _carried_count(db, kind, own) >= cap:
                db.connection.execute(
                    "UPDATE link_carry_decisions SET reason = 'cap' WHERE kind = ? AND resource_id = ?",
                    (kind, resource_id),
                )
                db.connection.commit()
                continue
            try:
                accept_offer(db, kind, resource_id, actor=None, max_remote_files_per_area=caps.max_remote_files_per_area)
            except CarryDecisionError as exc:
                _logger.info("Link Community %s: could not accept member %s %s: %s", link_id, kind, resource_id, exc)
                continue
            accepted += 1
    return accepted


def place_members(db: Database, link_id: str) -> None:
    """Put a subscribed Community's carried members into its copy, and take
    out the ones it no longer lists (Decision 5). A member the SysOp placed
    (`community_placed_locally`) is never moved, either way; one in a local
    Community of this node's own was placed by its SysOp before the flag
    existed, and is not moved either. A member moving in drops the explicit
    level 0 a carried board or file area was given when its genesis named
    none, so it inherits the Community's defaults. Commits."""
    copy = subscribed_copy(db, link_id)
    if copy is None:
        return
    genesis = stored_genesis(db, link_id)
    origin = genesis.payload["origin_fingerprint"]
    listed = members_in_force(db, link_id)
    other_copies = {
        row["id"] for row in db.connection.execute(
            "SELECT id FROM communities WHERE link_genesis_json IS NOT NULL AND link_origin_fingerprint = ? AND id != ?",
            (origin, copy["id"]),
        )
    } | {
        row["id"] for row in db.connection.execute(
            "SELECT id FROM communities WHERE link_genesis_json IS NOT NULL AND link_closure_json IS NOT NULL"
        )
    }
    for kind, (table, column) in _LOCAL.items():
        wanted = set(listed[kind])
        for row in db.connection.execute(
            f"SELECT * FROM {table} WHERE community_id = ? AND community_placed_locally = 0", (copy["id"],)
        ).fetchall():
            if row[column] not in wanted or row["link_genesis_json"] is None or _resource_origin(kind, row) != origin:
                db.connection.execute(f"UPDATE {table} SET community_id = NULL WHERE id = ?", (row["id"],))
        for resource_id in sorted(wanted):
            row = db.connection.execute(
                f"SELECT * FROM {table} WHERE {column} = ? AND link_genesis_json IS NOT NULL AND link_hidden_at IS NULL",
                (resource_id,),
            ).fetchone()
            if row is None or row["community_placed_locally"] or row["community_id"] == copy["id"]:
                continue
            if _resource_origin(kind, row) != origin:
                continue
            if row["community_id"] is not None and row["community_id"] not in other_copies:
                continue
            db.connection.execute(f"UPDATE {table} SET community_id = ? WHERE id = ?", (copy["id"], row["id"]))
            if kind in ("boards", "file_areas"):
                member_payload = json.loads(row["link_genesis_json"])["envelope"]["payload"]
                for level in ("min_read_level", "min_write_level"):
                    if f"default_{level}" not in member_payload and row[level] == 0:
                        db.connection.execute(f"UPDATE {table} SET {level} = NULL WHERE id = ?", (row["id"],))
    db.connection.commit()


def subscribed_link_ids(db: Database) -> list[str]:
    """Every Link Community this node subscribes to (its own promoted ones
    are not subscriptions, and have nothing to place)."""
    return [
        row["link_id"] for row in db.connection.execute(
            "SELECT link_id FROM communities WHERE link_genesis_json IS NOT NULL AND link_closure_json IS NULL "
            "AND link_members_json IS NULL"
        ).fetchall()
        if subscribed_copy(db, row["link_id"]) is not None
    ]


def place_listed_member(db: Database, kind: str, resource_id: str) -> None:
    """A resource newly carried here -- on arrival, or accepted by the SysOp
    -- goes into the copy of every subscribed Community that lists it."""
    for link_id in subscribed_link_ids(db):
        if resource_id in members_in_force(db, link_id).get(kind, ()):
            place_members(db, link_id)


def place_all_subscriptions(db: Database) -> None:
    """Re-place every subscribed Community's members: after a board's origin
    moves (§9.4), a member whose origin is no longer the Community's drops
    out of its copy (Decision 1)."""
    for link_id in subscribed_link_ids(db):
        place_members(db, link_id)
