"""Renames a peer announces for its users (design doc §16 #1111, issue #1306).

A node lists its recent renames in its signed endpoint descriptor as
`user_renames`: `{from, to, at}` entries, at most 64, none older than 400 days,
newest first (`netbbs.auth.renames.announced_renames` builds the list). The
descriptor is what every peer already receives, from every hello and every
introduction, so the notice reaches each node that holds anything about the
user without a new route.

A peer **keeps** what it learns, in `link_user_aliases`, keyed by the node's
fingerprint and the old name: entries age out of the descriptor, the alias does
not. From then on `new@node` is the same user as `old@node`:

- trust resolves through the alias (`netbbs.link.trust`): the old subject's
  state moves to the new one when the alias is learned, and anything said
  about the old name afterwards lands on the new one;
- mail blocks recorded against the old address cover the new one, and a letter
  to the old address is sent to the new one;
- content newly signed as `old@node` is refused as a retired identity.

A peer accepts a rename only when it grants the home node nothing new
(Decision 5). It ignores an entry whose new name it already holds as a subject
with history from before `at` (a merge of two users), and one that renames to a
name the same node renamed away from earlier (names are retired at home, so an
honest node never does). Each refusal is logged for the SysOp, under the
`netbbs.link` logger whose warnings the Link diagnostic log keeps.

The reader never refuses a hello over `user_renames`: an entry that does not
validate is skipped, and so is the rest of a list that is not a list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from netbbs.identity.addressing import is_valid_user_part
from netbbs.storage.database import Database
from netbbs.timeutil import utc_now_iso

_logger = logging.getLogger(__name__)

#: What a descriptor may carry (§16 #1111 Decision 4).
MAX_DESCRIPTOR_RENAMES = 64
MAX_RENAME_AGE = timedelta(days=400)
_FUTURE_TOLERANCE = timedelta(minutes=5)
#: Kept per node: a node renames its users, and this bounds how much of this
#: node's storage that can take. The oldest go first.
MAX_ALIASES_PER_NODE = 4096
#: Longest chain followed; the rules below keep chains acyclic, this keeps a
#: corrupted table from looping.
_MAX_CHAIN = 64


@dataclass(frozen=True)
class UserRename:
    old: str
    new: str
    at: str


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def descriptor_user_renames(payload: object, *, now: datetime | None = None) -> list[UserRename]:
    """The valid `user_renames` entries of an endpoint descriptor's
    `payload`, oldest first (the order to apply them in). Never raises.

    An entry is `{"from", "to", "at"}` and nothing else: two different names
    the address grammar allows, and a time that is neither in the future nor
    older than 400 days. At most the first 64 entries are read."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("user_renames")
    if not isinstance(entries, list):
        return []
    moment = now or datetime.now(timezone.utc)
    renames: list[UserRename] = []
    for entry in entries[:MAX_DESCRIPTOR_RENAMES]:
        if not isinstance(entry, dict) or set(entry) != {"from", "to", "at"}:
            continue
        old, new, at = entry["from"], entry["to"], _parse(entry["at"])
        if not (is_valid_user_part(old) and is_valid_user_part(new)) or old.casefold() == new.casefold():
            continue
        if at is None or at > moment + _FUTURE_TOLERANCE or at < moment - MAX_RENAME_AGE:
            continue
        renames.append(UserRename(old, new, _stamp(at)))
    renames.sort(key=lambda rename: rename.at)
    return renames


def _aliases_table_exists(db: Database) -> bool:
    return db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'link_user_aliases'"
    ).fetchone() is not None


def current_user_name(db: Database, node_fingerprint: str, user: str) -> str:
    """The name `user@node` goes by now, following the chain of renames this
    node has learned. `user` itself when nothing renamed it."""
    seen = {user}
    current = user
    for _ in range(_MAX_CHAIN):
        row = db.connection.execute(
            "SELECT new_user_id FROM link_user_aliases WHERE node_fingerprint = ? AND old_user_id = ?",
            (node_fingerprint, current),
        ).fetchone()
        if row is None or row["new_user_id"] in seen:
            return current
        current = row["new_user_id"]
        seen.add(current)
    return current


def renamed_at(db: Database, node_fingerprint: str, user: str) -> str | None:
    """When `user@node` was renamed away from, or `None` if it was not."""
    row = db.connection.execute(
        "SELECT renamed_at FROM link_user_aliases WHERE node_fingerprint = ? AND old_user_id = ?",
        (node_fingerprint, user),
    ).fetchone()
    return None if row is None else row["renamed_at"]


def former_user_names(db: Database, node_fingerprint: str, user: str) -> list[str]:
    """Every name `user@node` was renamed away from, most recent first."""
    found: list[str] = []
    frontier = [user]
    seen = {user}
    while frontier and len(found) < _MAX_CHAIN:
        name = frontier.pop(0)
        for row in db.connection.execute(
            """SELECT old_user_id FROM link_user_aliases WHERE node_fingerprint = ? AND new_user_id = ?
               ORDER BY renamed_at DESC""",
            (node_fingerprint, name),
        ).fetchall():
            if row["old_user_id"] not in seen:
                seen.add(row["old_user_id"])
                found.append(row["old_user_id"])
                frontier.append(row["old_user_id"])
    return found


def address_with_current_name(db: Database, address: str) -> str:
    """`user@<fingerprint>` with the user's current name (a reply to a letter
    from an old address goes to the new one). Anything else unchanged."""
    user, at, fingerprint = address.rpartition("@")
    if not at or not user or not fingerprint:
        return address
    return f"{current_user_name(db, fingerprint, user)}@{fingerprint}"


def addresses_of_same_user(db: Database, address: str) -> list[str]:
    """`address` and every former address of the same user, so a block made
    against an old address still covers the new one."""
    user, at, fingerprint = address.rpartition("@")
    if not at or not user or not fingerprint:
        return [address]
    current = current_user_name(db, fingerprint, user)
    names = [current, *former_user_names(db, fingerprint, current)]
    if user not in names:
        names.insert(0, user)
    return [f"{name}@{fingerprint}" for name in names]


def is_retired_identity(db: Database, node_fingerprint: str, user: str, signed_at: object) -> bool:
    """Whether content signed as `user@node` at `signed_at` came after the
    node renamed `user` away: such content is refused (§16 #1111 Decision 5).
    Content signed before the rename keeps its old name and is accepted."""
    at = renamed_at(db, node_fingerprint, user)
    if at is None:
        return False
    signed = _parse(signed_at)
    # A time that cannot be read is no proof of coming before the rename.
    return signed is None or _stamp(signed) >= at


def learn_user_renames(db: Database, node_fingerprint: str, payload: object) -> list[UserRename]:
    """Keep the renames a verified descriptor of `node_fingerprint` announces,
    and move each renamed user's trust state to the new name. Returns the
    renames newly learned. Never raises: a descriptor is never refused for
    what it says here."""
    renames = descriptor_user_renames(payload)
    if not renames or not _aliases_table_exists(db):
        return []
    learned: list[UserRename] = []
    try:
        for rename in renames:
            if _learn_one(db, node_fingerprint, rename):
                learned.append(rename)
        if learned:
            _bound_aliases(db, node_fingerprint)
            db.connection.commit()
    except Exception:  # noqa: BLE001 -- a notice must never break the hello it rode in on
        db.connection.rollback()
        _logger.exception("Could not apply the user renames announced by %s", node_fingerprint)
        return []
    return learned


def _learn_one(db: Database, node_fingerprint: str, rename: UserRename) -> bool:
    from netbbs.link.trust import TrustSubject, merge_renamed_user_subject, subject_history_starts

    known = db.connection.execute(
        "SELECT new_user_id FROM link_user_aliases WHERE node_fingerprint = ? AND old_user_id = ?",
        (node_fingerprint, rename.old),
    ).fetchone()
    if known is not None:
        if known["new_user_id"] != rename.new:
            _logger.warning(
                "Ignored a rename announced by %s: %r -> %r, but %r was already renamed to %r",
                node_fingerprint, rename.old, rename.new, rename.old, known["new_user_id"],
            )
        return False
    renamed_away = db.connection.execute(
        "SELECT renamed_at FROM link_user_aliases WHERE node_fingerprint = ? AND old_user_id = ?",
        (node_fingerprint, rename.new),
    ).fetchone()
    if renamed_away is not None:
        _logger.warning(
            "Refused a rename announced by %s: %r -> %r renames back to a name it renamed away from "
            "on %s, which a node that retires names never does",
            node_fingerprint, rename.old, rename.new, renamed_away["renamed_at"][:10],
        )
        return False
    new_subject = TrustSubject.user(node_fingerprint, rename.new)
    history = subject_history_starts(db, new_subject)
    if history is not None and history < rename.at:
        _logger.warning(
            "Refused a rename announced by %s: %r -> %r would merge two users -- %r has history "
            "here from %s, before the rename on %s",
            node_fingerprint, rename.old, rename.new, rename.new, history[:10], rename.at[:10],
        )
        return False
    db.connection.execute(
        """INSERT INTO link_user_aliases (node_fingerprint, old_user_id, new_user_id, renamed_at, learned_at)
           VALUES (?, ?, ?, ?, ?)""",
        (node_fingerprint, rename.old, rename.new, rename.at, utc_now_iso()),
    )
    merge_renamed_user_subject(db, TrustSubject.user(node_fingerprint, rename.old), new_subject)
    _logger.info("Learned that %s renamed %r to %r on %s", node_fingerprint, rename.old, rename.new, rename.at[:10])
    return True


def _bound_aliases(db: Database, node_fingerprint: str) -> None:
    db.connection.execute(
        """DELETE FROM link_user_aliases WHERE node_fingerprint = ? AND old_user_id NOT IN (
               SELECT old_user_id FROM link_user_aliases WHERE node_fingerprint = ?
               ORDER BY renamed_at DESC LIMIT ?
           )""",
        (node_fingerprint, node_fingerprint, MAX_ALIASES_PER_NODE),
    )
