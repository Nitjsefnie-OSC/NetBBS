"""
Link moderation's roster (design doc §16, issue #1357, Decision 1): who an
origin delegates the moderation of its linked boards and channels to.

An origin keeps its grants in `link_moderator_grants` and signs them, whole,
as one `link_moderators` event whenever they change. Every roster this node
holds -- its own and other origins' -- is one row of `link_moderator_rosters`,
keyed by origin: a received roster replaces the one in force only when it is
newer by effective time (Decision 8), and superseded rosters are not kept, so
what a node stores and serves per origin stays one event.

A roster travels with its origin's resources. `netbbs.link.store` serves it
as one of each linked board's and channel's events, so whoever carries or
relays a board can answer for the roster that decides who may moderate it,
and a carrier pulls it where it pulls the board. It goes only to peers that
advertise `LINK_MODERATION_CAPABILITY` (Decision 9).

Which actions a delegate may then take, and the events they sign, are the
later slices' (#1359); `_delegated_permissions` is the question they will ask.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass

from netbbs.auth.users import User
from netbbs.link.events import (
    LINK_EVENT_FUTURE_SKEW_SECONDS,
    LINK_MODERATOR_PERMISSIONS,
    MAX_LINK_MODERATORS,
    LinkModerators,
    build_link_moderators,
    link_moderator_entry_problem,
)
from netbbs.moderation.log import record_action
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_iso, utc_now_iso


class LinkModeratorsError(Exception):
    """A grant this node cannot make, said to the SysOp."""


SCOPE_ORIGIN = "origin"
"""A grant over every linked resource the origin originates."""


@dataclass(frozen=True)
class OwnGrant:
    """One delegation this node, as an origin, makes."""

    id: int
    home_node_fingerprint: str
    local_user_id: str
    scope_kind: str  # "board", "channel", "community" or "origin"
    scope_id: str | None
    permissions: tuple[str, ...]
    granted_at: str


def effective_time(created_at: object, arrived_at: str) -> str:
    """When a received roster (or restriction, or topic) counts as made
    (Decision 8): its signed `created_at`, or its arrival when that is more
    than `LINK_EVENT_FUTURE_SKEW_SECONDS` ahead of this node's clock, or not a
    timestamp at all. A signer's clock cannot win the future."""
    if not isinstance(created_at, str):
        return arrived_at
    try:
        made = parse_utc_iso(created_at)
    except ValueError:
        return arrived_at
    if made > parse_utc_iso(arrived_at) + datetime.timedelta(seconds=LINK_EVENT_FUTURE_SKEW_SECONDS):
        return arrived_at
    return utc_iso(made)


def _row(db: Database, origin_fingerprint: str):
    return db.connection.execute(
        "SELECT * FROM link_moderator_rosters WHERE origin_fingerprint = ?", (origin_fingerprint,)
    ).fetchone()


def roster_in_force(db: Database, origin_fingerprint: str) -> dict | None:
    """The roster `origin_fingerprint` has in force here, as its raw signed
    event, or `None` when this node holds none."""
    row = _row(db, origin_fingerprint)
    return json.loads(row["envelope_json"]) if row is not None else None


def _roster_entries(db: Database, origin_fingerprint: str) -> list[dict]:
    raw = roster_in_force(db, origin_fingerprint)
    return list(raw["envelope"]["payload"]["entries"]) if raw is not None else []


def all_roster_content_ids(db: Database) -> set[str]:
    """Every roster's content id: what is withheld from a peer that does not
    understand rosters."""
    return {row["content_id"] for row in db.connection.execute("SELECT content_id FROM link_moderator_rosters")}


def roster_events_for(db: Database, origin_fingerprint: str | None) -> dict[str, dict]:
    """`{content_id: raw}` for the roster of the resource's origin, or empty
    -- what a linked board's or channel's event set includes."""
    if origin_fingerprint is None:
        return {}
    row = _row(db, origin_fingerprint)
    if row is None:
        return {}
    return {row["content_id"]: json.loads(row["envelope_json"])}


def store_received_roster(db: Database, *, envelope: dict, content_id: str, arrived_at: str | None = None) -> str:
    """Keep a received roster if it is newer than the one in force for its
    origin, by effective time and then content id, replacing it. Returns
    `"stale"` or `"stored"`."""
    payload = envelope["envelope"]["payload"]
    origin = payload["origin_fingerprint"]
    arrived = arrived_at or utc_now_iso()
    effective = effective_time(payload.get("created_at"), arrived)
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        current = _row(db, origin)
        if current is not None and (current["effective_at"], current["content_id"]) >= (effective, content_id):
            db.connection.rollback()
            return "stale"
        db.connection.execute(
            """
            INSERT INTO link_moderator_rosters (origin_fingerprint, content_id, envelope_json, effective_at, received_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(origin_fingerprint) DO UPDATE SET
                content_id = excluded.content_id, envelope_json = excluded.envelope_json,
                effective_at = excluded.effective_at, received_at = excluded.received_at
            """,
            (origin, content_id, json.dumps(envelope), effective, arrived),
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return "stored"


def load_own_roster(db: Database, own_fingerprint: str) -> list[LinkModerators]:
    """This node's own signed roster, for push; empty when it delegates
    nobody and never did."""
    raw = roster_in_force(db, own_fingerprint)
    return [LinkModerators.from_dict(raw)] if raw is not None else []


# -- the origin's own grants -------------------------------------------------


def _own_grant(row) -> OwnGrant:
    return OwnGrant(
        id=row["id"], home_node_fingerprint=row["home_node_fingerprint"], local_user_id=row["local_user_id"],
        scope_kind=row["scope_kind"], scope_id=row["scope_id"],
        permissions=tuple(json.loads(row["permissions"])), granted_at=row["granted_at"],
    )


def list_own_grants(db: Database) -> list[OwnGrant]:
    return [_own_grant(row) for row in db.connection.execute(
        "SELECT * FROM link_moderator_grants ORDER BY local_user_id COLLATE NOCASE, scope_kind, scope_id"
    )]


def _entry(grant: OwnGrant) -> dict:
    if grant.scope_kind == "origin":
        scope: object = SCOPE_ORIGIN
    elif grant.scope_kind == "community":
        scope = {"community": grant.scope_id}
    else:
        scope = {"kind": grant.scope_kind, "id": grant.scope_id}
    return {
        "moderator": {
            "kind": "node_vouched_user",
            "home_node_fingerprint": grant.home_node_fingerprint,
            "local_user_id": grant.local_user_id,
        },
        "scope": scope,
        "permissions": sorted(grant.permissions),
    }


def _later_than(previous: str | None) -> str:
    """Now, or just after `previous` when the clock has not moved past it,
    so this node's own rosters always order after one another."""
    now = utc_now_iso()
    if previous is None or now > previous:
        return now
    return utc_iso(parse_utc_iso(previous) + datetime.timedelta(milliseconds=1))


def sign_own_roster(db: Database, node_identity) -> dict:
    """Sign this node's grants as its roster, store it as the one in force,
    and return the raw event for the running node to register."""
    entries = [_entry(grant) for grant in list_own_grants(db)]
    own = node_identity.fingerprint
    current = _row(db, own)
    created_at = _later_than(current["effective_at"] if current is not None else None)
    event = build_link_moderators(
        signing_identity=node_identity.signing_key, origin_fingerprint=own, entries=entries, created_at=created_at,
    )
    raw = event.to_dict()
    store_received_roster(db, envelope=raw, content_id=event.content_id, arrived_at=created_at)
    return raw


def default_permissions(scope_kind: str) -> list[str]:
    """Everything a scope can carry: what a new grant gives unless narrowed."""
    if scope_kind in LINK_MODERATOR_PERMISSIONS:
        return list(LINK_MODERATOR_PERMISSIONS[scope_kind])
    return sorted({p for kind in LINK_MODERATOR_PERMISSIONS.values() for p in kind})


def grant_link_moderator(
    db: Database,
    node_identity,
    *,
    home_node_fingerprint: str,
    local_user_id: str,
    scope_kind: str,
    scope_id: str | None,
    permissions: list[str] | None = None,
    actor: User,
    label: str,
) -> dict:
    """Delegate the moderation of `scope` to `local_user_id@home_node_fingerprint`
    and sign the new roster. `label` names the scope in the audit log.
    Raises `LinkModeratorsError` for a grant no receiver would accept."""
    if home_node_fingerprint == node_identity.fingerprint:
        raise LinkModeratorsError(
            "That account is on this node: give it an ordinary moderator grant instead."
        )
    permissions = sorted(set(permissions or default_permissions(scope_kind)))
    grant = OwnGrant(0, home_node_fingerprint, local_user_id, scope_kind, scope_id, tuple(permissions), "")
    problem = link_moderator_entry_problem(_entry(grant))
    if problem is not None:
        raise LinkModeratorsError(problem)
    existing = db.connection.execute(
        "SELECT id FROM link_moderator_grants WHERE home_node_fingerprint = ? AND local_user_id = ? "
        "AND scope_kind = ? AND scope_id IS ?",
        (home_node_fingerprint, local_user_id, scope_kind, scope_id),
    ).fetchone()
    if existing is None and len(list_own_grants(db)) >= MAX_LINK_MODERATORS:
        raise LinkModeratorsError(f"A node can delegate at most {MAX_LINK_MODERATORS} Link moderator grants.")
    now = utc_now_iso()
    if existing is not None:
        db.connection.execute(
            "UPDATE link_moderator_grants SET permissions = ?, granted_at = ? WHERE id = ?",
            (json.dumps(permissions), now, existing["id"]),
        )
    else:
        db.connection.execute(
            """
            INSERT INTO link_moderator_grants
                (home_node_fingerprint, local_user_id, scope_kind, scope_id, permissions, granted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (home_node_fingerprint, local_user_id, scope_kind, scope_id, json.dumps(permissions), now),
        )
    db.connection.commit()
    record_action(
        db, actor=actor, action="grant_link_moderator",
        detail=f"{local_user_id}@{home_node_fingerprint}: {', '.join(permissions)} on {label}",
    )
    return sign_own_roster(db, node_identity)


def revoke_link_moderator(db: Database, node_identity, grant_id: int, *, actor: User, label: str) -> dict | None:
    """Remove one delegation and sign the new roster; `None` when it was
    already gone. Takes effect on each carrier as the roster arrives."""
    row = db.connection.execute("SELECT * FROM link_moderator_grants WHERE id = ?", (grant_id,)).fetchone()
    if row is None:
        return None
    db.connection.execute("DELETE FROM link_moderator_grants WHERE id = ?", (grant_id,))
    db.connection.commit()
    record_action(
        db, actor=actor, action="revoke_link_moderator",
        detail=f"{row['local_user_id']}@{row['home_node_fingerprint']} on {label}",
    )
    return sign_own_roster(db, node_identity)


# -- what a roster grants ------------------------------------------------------


def _delegated_permissions(
    entries: list[dict],
    *,
    home_node_fingerprint: str,
    local_user_id: str,
    resource_kind: str,
    resource_id: str,
    community_ids: set[str] = frozenset(),
) -> set[str]:
    """What `local_user_id@home_node_fingerprint` may do on one of the
    origin's resources under its roster `entries`: the union over every
    entry naming them whose scope covers the resource -- the resource
    itself, a Community among `community_ids` (those the origin lists it
    in), or the whole origin -- narrowed to what applies to that kind."""
    applicable = set(LINK_MODERATOR_PERMISSIONS.get(resource_kind, ()))
    granted: set[str] = set()
    for entry in entries:
        moderator = entry.get("moderator", {})
        if (moderator.get("home_node_fingerprint"), moderator.get("local_user_id")) != (
            home_node_fingerprint, local_user_id,
        ):
            continue
        scope = entry.get("scope")
        if scope == SCOPE_ORIGIN:
            covered = True
        elif isinstance(scope, dict) and "community" in scope:
            covered = scope["community"] in community_ids
        elif isinstance(scope, dict):
            covered = (scope.get("kind"), scope.get("id")) == (resource_kind, resource_id)
        else:
            covered = False
        if covered:
            granted |= set(entry.get("permissions", ())) & applicable
    return granted


def grants_held_here(db: Database, own_fingerprint: str) -> list[tuple[str, dict]]:
    """`(origin, entry)` for every roster entry naming one of this node's own
    users: the Link grants this node's moderators hold elsewhere."""
    held = []
    for row in db.connection.execute("SELECT origin_fingerprint, envelope_json FROM link_moderator_rosters"):
        if row["origin_fingerprint"] == own_fingerprint:
            continue
        for entry in json.loads(row["envelope_json"])["envelope"]["payload"]["entries"]:
            if entry["moderator"]["home_node_fingerprint"] == own_fingerprint:
                held.append((row["origin_fingerprint"], entry))
    return held
