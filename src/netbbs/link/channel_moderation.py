"""
Mutes and bans in linked channels (design doc §16 issue #1357, Decision 3;
issue #1359 slice 3).

A `channel_restriction` signed by a linked channel's origin, or by a
delegated moderator's home node, mutes or bans one participant from any
node in that channel everywhere it is carried. Every node keeps the one in
force per channel, participant and kind in `link_channel_restrictions`
(scope `link`): the origin's newest, else a delegate's newest, by effective
time (Decision 8) -- a delegate never outranks the origin. A carrier keeps
the restricted participant's messages as signed events, so relay and
inventory are unaffected, but shows none of them. The participant's own
node also enforces it on its caller (`netbbs.chat.moderation`).

A carrier may add (Decision 6): a local moderator's mute or ban of a remote
participant is a row of scope `local`, with local effect only and nothing
signed. It never lifts a Link one.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass

from netbbs.link.events import ChannelRestriction
from netbbs.link.moderators import effective_time
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_now_iso

LINK_SCOPE = "link"
LOCAL_SCOPE = "local"

#: How many signed restrictions one linked channel may hold here (AGENTS.md:
#: bound remotely influenced resources). At the cap, lifted and expired ones
#: make room; a channel full of ones in force refuses more, with a warning.
MAX_RESTRICTIONS_PER_CHANNEL = 500

logger = logging.getLogger(__name__)


class RestrictionRefused(ValueError):
    """A mute or ban signed here that was not kept, so changed nothing:
    `outcome` is `"stale"` (the channel's origin's own, still in force,
    outranks a delegate's) or `"full"`."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


@dataclass(frozen=True)
class ChannelRestrictionState:
    """One restriction in force on this node."""

    channel_id: str
    home_node_fingerprint: str
    local_user_id: str
    kind: str
    scope: str
    until: str | None
    reason: str | None


def _active(row, now: datetime.datetime) -> bool:
    if not row["active"]:
        return False
    if row["until"] is None:
        return True
    try:
        return parse_utc_iso(row["until"]) > now
    except ValueError:
        return True


def _replaces(current, *, is_origin: int, effective_at: str, content_id: str, now: datetime.datetime) -> bool:
    """Whether a signed restriction replaces the one stored for its channel,
    participant and kind (Decision 8): the newer by effective time, then by
    content id -- except that a delegate's never replaces the origin's own
    while that is in force (review of #1363: a lifted or expired one no
    longer holds a delegate off)."""
    if current is None:
        return True
    if current["is_origin"] and not is_origin and _active(current, now):
        return False
    return (effective_at, content_id) > (current["effective_at"], current["content_id"])


def _make_room(db: Database, channel_id: str, now: datetime.datetime) -> bool:
    """Free a slot for one more signed restriction in `channel_id`, dropping
    lifted, then expired ones, oldest first; whether there is room."""
    rows = db.connection.execute(
        "SELECT home_node_fingerprint, local_user_id, kind, active, until, effective_at "
        "FROM link_channel_restrictions WHERE channel_id = ? AND scope = ?",
        (channel_id, LINK_SCOPE),
    ).fetchall()
    if len(rows) < MAX_RESTRICTIONS_PER_CHANNEL:
        return True
    spent = sorted((row for row in rows if not _active(row, now)), key=lambda row: row["effective_at"])
    for row in spent[: len(rows) - MAX_RESTRICTIONS_PER_CHANNEL + 1]:
        db.connection.execute(
            "DELETE FROM link_channel_restrictions WHERE channel_id = ? AND home_node_fingerprint = ? "
            "AND local_user_id = ? AND kind = ? AND scope = ?",
            (channel_id, row["home_node_fingerprint"], row["local_user_id"], row["kind"], LINK_SCOPE),
        )
    return len(rows) - len(spent) < MAX_RESTRICTIONS_PER_CHANNEL


def store_received_restriction(
    db: Database, *, envelope: dict, content_id: str, own_fingerprint: str | None = None,
    arrived_at: str | None = None,
) -> str:
    """Keep a received (or this node's own) `channel_restriction` if it ranks
    above the one in force for its channel, target and kind. Returns
    `"stored"`, `"stale"`, `"not carried"` (no such linked channel here: a
    genesis alone, for a channel offered but not carried, holds nothing) or
    `"full"` (`MAX_RESTRICTIONS_PER_CHANNEL`)."""
    payload = envelope["envelope"]["payload"]
    target = payload["target"]
    is_origin = 0 if "moderator" in payload else 1
    arrived = arrived_at or utc_now_iso()
    effective = effective_time(payload.get("created_at"), arrived)
    # A username is unique ignoring case on its node: one row per participant.
    key = (payload["channel_id"], target["home_node_fingerprint"], target["local_user_id"].casefold(), payload["kind"])
    now = datetime.datetime.now(datetime.timezone.utc)
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        carried = db.connection.execute(
            "SELECT 1 FROM channels WHERE channel_id = ? AND link_genesis_json IS NOT NULL AND link_hidden_at IS NULL",
            (payload["channel_id"],),
        ).fetchone()
        if carried is None:
            db.connection.rollback()
            return "not carried"
        current = db.connection.execute(
            "SELECT is_origin, effective_at, content_id, active, until FROM link_channel_restrictions "
            "WHERE channel_id = ? AND home_node_fingerprint = ? AND local_user_id = ? AND kind = ? AND scope = ?",
            (*key, LINK_SCOPE),
        ).fetchone()
        if not _replaces(current, is_origin=is_origin, effective_at=effective, content_id=content_id, now=now):
            db.connection.rollback()
            return "stale"
        if current is None and not _make_room(db, payload["channel_id"], now):
            db.connection.rollback()
            logger.warning(
                "Link: channel %s already holds %s mutes and bans in force; refusing another",
                payload["channel_id"], MAX_RESTRICTIONS_PER_CHANNEL,
            )
            return "full"
        db.connection.execute(
            """
            INSERT INTO link_channel_restrictions
                (channel_id, home_node_fingerprint, local_user_id, kind, scope, active, until, reason,
                 is_origin, effective_at, content_id, envelope_json, target_local)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, home_node_fingerprint, local_user_id, kind, scope) DO UPDATE SET
                active = excluded.active, until = excluded.until, reason = excluded.reason,
                is_origin = excluded.is_origin, effective_at = excluded.effective_at,
                content_id = excluded.content_id, envelope_json = excluded.envelope_json,
                target_local = excluded.target_local
            """,
            (
                *key, LINK_SCOPE, 1 if payload["action"] == "impose" else 0, payload.get("until"),
                payload.get("reason"), is_origin, effective, content_id, json.dumps(envelope),
                1 if own_fingerprint is not None and target["home_node_fingerprint"] == own_fingerprint else 0,
            ),
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return "stored"


def set_local_restriction(
    db: Database, *, channel_id: str, home_node_fingerprint: str, local_user_id: str, kind: str,
    impose: bool, until: str | None = None, reason: str | None = None,
) -> None:
    """A carrier's own mute or ban of a remote participant (Decision 6): this
    node only, nothing signed."""
    local_user_id = local_user_id.casefold()
    if not impose:
        db.connection.execute(
            "DELETE FROM link_channel_restrictions WHERE channel_id = ? AND home_node_fingerprint = ? "
            "AND local_user_id = ? AND kind = ? AND scope = ?",
            (channel_id, home_node_fingerprint, local_user_id, kind, LOCAL_SCOPE),
        )
    else:
        db.connection.execute(
            """
            INSERT INTO link_channel_restrictions
                (channel_id, home_node_fingerprint, local_user_id, kind, scope, active, until, reason,
                 is_origin, effective_at, content_id, envelope_json, target_local)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, 0, ?, NULL, NULL, 0)
            ON CONFLICT(channel_id, home_node_fingerprint, local_user_id, kind, scope) DO UPDATE SET
                active = 1, until = excluded.until, reason = excluded.reason, effective_at = excluded.effective_at
            """,
            (channel_id, home_node_fingerprint, local_user_id, kind, LOCAL_SCOPE, until, reason, utc_now_iso()),
        )
    db.connection.commit()


def restriction_in_force(
    db: Database, channel_id: str, home_node_fingerprint: str, local_user_id: str, *, kinds=("mute", "ban"),
) -> ChannelRestrictionState | None:
    """A mute or ban in force here on that participant in that linked
    channel, of either scope, or `None`. A ban outranks a mute."""
    now = datetime.datetime.now(datetime.timezone.utc)
    found = None
    for row in db.connection.execute(
        "SELECT * FROM link_channel_restrictions WHERE channel_id = ? AND home_node_fingerprint = ? "
        "AND local_user_id = ?",
        (channel_id, home_node_fingerprint, local_user_id.casefold()),
    ):
        if row["kind"] not in kinds or not _active(row, now):
            continue
        state = ChannelRestrictionState(
            row["channel_id"], row["home_node_fingerprint"], row["local_user_id"], row["kind"], row["scope"],
            row["until"], row["reason"],
        )
        if found is None or (state.kind == "ban" and found.kind != "ban"):
            found = state
    return found


def local_user_restricted(db: Database, channel_id: str, username: str, kind: str) -> ChannelRestrictionState | None:
    """A Link restriction in force on one of this node's own users in a
    linked channel: what their own node enforces on them (Decision 3)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for row in db.connection.execute(
        "SELECT * FROM link_channel_restrictions WHERE channel_id = ? AND local_user_id = ? "
        "AND kind = ? AND scope = ? AND target_local = 1",
        (channel_id, username.casefold(), kind, LINK_SCOPE),
    ):
        if _active(row, now):
            return ChannelRestrictionState(
                row["channel_id"], row["home_node_fingerprint"], row["local_user_id"], row["kind"], row["scope"],
                row["until"], row["reason"],
            )
    return None


def restriction_events_for(db: Database, channel_id: str) -> dict[str, dict]:
    """`{content_id: raw}` of the signed restrictions in force for one linked
    channel: what its event set includes for inventory and relay."""
    return {
        row["content_id"]: json.loads(row["envelope_json"]) for row in db.connection.execute(
            "SELECT content_id, envelope_json FROM link_channel_restrictions WHERE channel_id = ? AND scope = ? "
            "AND content_id IS NOT NULL",
            (channel_id, LINK_SCOPE),
        )
    }


def load_restriction_events(db: Database) -> list[dict]:
    """Every signed restriction in force here, for the running node's memory
    at startup."""
    return [json.loads(row["envelope_json"]) for row in db.connection.execute(
        "SELECT envelope_json FROM link_channel_restrictions WHERE envelope_json IS NOT NULL"
    )]


def load_own_restrictions(db: Database, own_fingerprint: str) -> list[ChannelRestriction]:
    """The signed restrictions in force here that this node signed -- as the
    channel's origin or for a delegated moderator of its own -- for push."""
    own = []
    for raw in load_restriction_events(db):
        payload = raw["envelope"]["payload"]
        moderator = payload.get("moderator")
        if moderator is not None:
            if moderator.get("home_node_fingerprint") == own_fingerprint:
                own.append(ChannelRestriction.from_dict(raw))
            continue
        row = db.connection.execute(
            "SELECT link_genesis_json FROM channels WHERE channel_id = ?", (payload["channel_id"],)
        ).fetchone()
        if row is not None and row["link_genesis_json"] is not None and json.loads(row["link_genesis_json"])[
            "envelope"
        ]["payload"].get("origin_fingerprint") == own_fingerprint:
            own.append(ChannelRestriction.from_dict(raw))
    return own


def restriction_reach(db: Database, channel, *, node_identity, actor) -> dict | None:
    """Whether `actor`'s mute or ban in linked `channel` reaches every node
    carrying it, and as whom: `{}` when this node is the channel's origin
    (it signs as the origin), the delegated-moderator reference when the
    origin's roster lets `actor` moderate it here (Decision 2), `None` when
    it stays on this node."""
    row = db.connection.execute(
        "SELECT link_genesis_json FROM channels WHERE id = ?", (channel.id,)
    ).fetchone()
    if row is None or row["link_genesis_json"] is None:
        return None
    origin = json.loads(row["link_genesis_json"])["envelope"]["payload"].get("origin_fingerprint")
    if origin == node_identity.fingerprint:
        return {}
    from netbbs.link.moderators import holds_link_permission
    from netbbs.moderation.roles import ChannelPermission

    if holds_link_permission(db, actor, object_type="channel", object_id=channel.id,
                             permission=ChannelPermission.MODERATE):
        return {"kind": "node_vouched_user", "home_node_fingerprint": node_identity.fingerprint,
                "local_user_id": actor.username}
    return None


def _bounded_reason(reason: str | None) -> str | None:
    """`reason` cut to what a signed restriction carries (review of #1363):
    it only explains, so a long one is shortened, never refused."""
    from netbbs.link.events import MAX_CHANNEL_RESTRICTION_REASON_BYTES

    if not reason:
        return None
    data = reason.encode("utf-8")[:MAX_CHANNEL_RESTRICTION_REASON_BYTES]
    return data.decode("utf-8", errors="ignore") or None


def sign_channel_restriction(
    db: Database, channel, *, node_identity, actor, home_node_fingerprint: str, local_user_id: str,
    kind: str, impose: bool, until: str | None = None, reason: str | None = None,
) -> dict | None:
    """Sign and store a mute or ban (or its lift) in linked `channel` for
    everyone carrying it, when `actor`'s moderation reaches that far
    (`restriction_reach`); return the raw event for the running node, or
    `None` when it stays on this node."""
    from netbbs.link.events import build_channel_restriction

    reach = restriction_reach(db, channel, node_identity=node_identity, actor=actor)
    if reach is None:
        return None
    event = build_channel_restriction(
        signing_identity=node_identity.signing_key, channel_id=channel.channel_id,
        target={"kind": "node_vouched_user", "home_node_fingerprint": home_node_fingerprint,
                "local_user_id": local_user_id},
        kind=kind, action="impose" if impose else "lift", created_at=utc_now_iso(), until=until,
        reason=_bounded_reason(reason), moderator=reach or None,
    )
    raw = event.to_dict()
    outcome = store_received_restriction(db, envelope=raw, content_id=event.content_id,
                                         own_fingerprint=node_identity.fingerprint)
    if outcome != "stored":
        raise RestrictionRefused(outcome)
    return raw
