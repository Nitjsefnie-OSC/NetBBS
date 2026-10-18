"""Host-held bot lines: said for one caller on their next join (issues #1375, #1376).

A bot asks the host to say a line *for* a caller. If that caller is in the
channel, the runtime publishes it at once; otherwise a row here holds it until
the caller's next join of that channel, or its expiry. A tell is the token-bound
form: the host takes the sender from its own pending request, checks the
recipient and the §6.4 block list when the tell is made, and again when it is
delivered. Synchronous and db-first; the runtime owns presence and publishing.
"""

from __future__ import annotations

import datetime as dt
import re

from netbbs.auth.users import AuthError, User, get_user_by_id, get_user_by_username
from netbbs.bots.registry import Bot, BotError, allowed_channel
from netbbs.chat.channels import Channel
from netbbs.messaging_preferences import LIVE_BLOCK_REFUSAL, live_message_refusal
from netbbs.rendering import sanitize_text
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_iso

# A held line ("say for a caller"): the bounds are Baud's reminder limits,
# the one feature that holds lines, and a held reminder waits at most 7 days.
LINES_PER_BOT = 48
LINES_PER_CALLER = 8
LINE_MIN_SECONDS = 60
LINE_MAX_SECONDS = 7 * 24 * 3600
# Tells (decision #1376, Decision 2).
TELLS_PER_SENDER = 3
TELLS_PER_RECIPIENT = 5
TELLS_PER_BOT = 32
TELL_SECONDS = 14 * 24 * 3600
TELL_MAX_TEXT = 160
MAX_BODY = 500
_LINK_ID = re.compile(r"link:([0-9A-Za-z]{1,128}):([^\s:@]{1,64})")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def caller_columns(caller) -> tuple[int | None, str | None]:
    """A bot's opaque caller id as `(local account id, Link identity)`."""
    if isinstance(caller, bool):
        raise BotError("A caller id is a local account id or a link: identity.")
    if isinstance(caller, int):
        if caller <= 0:
            raise BotError("A caller id is a local account id or a link: identity.")
        return caller, None
    if isinstance(caller, str) and _LINK_ID.fullmatch(caller):
        return None, caller
    raise BotError("A caller id is a local account id or a link: identity.")


def _line_body(text, name: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_BODY:
        raise BotError(f"A held line's {name} must contain 1-500 characters.")
    if any(c in text for c in "\r\n\t") or sanitize_text(text) != text:
        raise BotError("Bot lines must be plain text without terminal controls.")
    return text


def _usable(user: User | None) -> bool:
    return user is not None and not user.disabled_at and not user.pending_approval


def _prune(db: Database, now: dt.datetime) -> list[tuple[int, int, str]]:
    rows = db.connection.execute(
        "SELECT id, bot_id, kind FROM bot_held_lines WHERE expires_at <= ?", (utc_iso(now),)
    ).fetchall()
    if rows:
        db.connection.execute("DELETE FROM bot_held_lines WHERE expires_at <= ?", (utc_iso(now),))
    return [(r["bot_id"], r["id"], r["kind"]) for r in rows]


def prune_expired(db: Database) -> list[tuple[int, int, str]]:
    """Drop every expired row, silently for callers; `(bot_id, id, kind)` of
    each, so the runtime can tell a running bot its line expired."""
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        dropped = _prune(db, _now())
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return dropped


def hold_line(db: Database, bot_id: int, channel_name: str, caller, text, seconds) -> dict:
    """Store a line for `caller`, who is not in the channel now."""
    user_id, link = caller_columns(caller)
    text = _line_body(text, "text")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not (
            LINE_MIN_SECONDS <= seconds <= LINE_MAX_SECONDS):
        raise BotError("A held line expires after 60 seconds to 7 days.")
    _bot, channel = allowed_channel(db, bot_id, channel_name)
    if user_id is not None and not _usable(get_user_by_id(db, user_id)):
        raise BotError("That caller is no longer an account on this node.")
    now = _now()
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        _prune(db, now)
        total, mine = db.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(caller_user_id IS ? AND caller_link IS ?), 0) "
            "FROM bot_held_lines WHERE bot_id = ? AND kind = 'line'",
            (user_id, link, bot_id),
        ).fetchone()
        if total >= LINES_PER_BOT:
            raise BotError(f"This bot already holds {LINES_PER_BOT} lines.")
        if mine >= LINES_PER_CALLER:
            raise BotError(f"This bot already holds {LINES_PER_CALLER} lines for that caller.")
        expires = utc_iso(now + dt.timedelta(seconds=seconds))
        cursor = db.connection.execute(
            "INSERT INTO bot_held_lines(bot_id, channel_id, kind, caller_user_id, caller_link, text, "
            "created_at, expires_at) VALUES (?, ?, 'line', ?, ?, ?, ?, ?)",
            (bot_id, channel.id, user_id, link, text, utc_iso(now), expires),
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return {"held": True, "held_id": cursor.lastrowid, "expires_at": expires}


def list_lines(db: Database, bot_id: int) -> list[dict]:
    """This bot's own held lines that have not expired (never tells)."""
    rows = db.connection.execute(
        "SELECT h.id, c.name, h.caller_user_id, h.caller_link, h.created_at, h.expires_at "
        "FROM bot_held_lines h JOIN channels c ON c.id = h.channel_id "
        "WHERE h.bot_id = ? AND h.kind = 'line' AND h.expires_at > ? ORDER BY h.id",
        (bot_id, utc_iso(_now())),
    ).fetchall()
    return [{"held_id": r[0], "channel": r[1], "caller": r[2] if r[2] is not None else r[3],
             "created_at": r[4], "expires_at": r[5]} for r in rows]


def cancel_line(db: Database, bot_id: int, held_id) -> dict:
    if isinstance(held_id, bool) or not isinstance(held_id, int):
        raise BotError("held.cancel needs a held_id.")
    cursor = db.connection.execute(
        "DELETE FROM bot_held_lines WHERE id = ? AND bot_id = ? AND kind = 'line'", (held_id, bot_id))
    db.connection.commit()
    return {"cancelled": cursor.rowcount > 0}


def may_enter(db: Database, channel: Channel, user: User) -> bool:
    """Whether `user` could enter `channel` now -- `_authorize_channel_entry`'s
    gates without its side effect of accepting a pending invitation."""
    from netbbs.attestation import meets_name_requirement
    from netbbs.chat import has_pending_invitation, is_banned, is_member
    from netbbs.communities import get_effective_name_requirement, resource_age_gate
    from netbbs.permissions import meets_level
    if is_banned(db, channel, user) is not None:
        return False
    if not meets_level(user, channel.min_level) or resource_age_gate(db, user, channel) != "pass":
        return False
    if not meets_name_requirement(db, user, get_effective_name_requirement(db, channel)):
        return False
    return not channel.members_only or is_member(db, channel, user) or has_pending_invitation(db, channel, user)


def _sender_columns(sender: dict) -> tuple[int | None, str | None, str]:
    """The pending request's caller -- never anything the bot supplied."""
    user_id, link = caller_columns(sender.get("id"))
    return user_id, link, sanitize_text(str(sender.get("name", "")))[:96]


def _sender_refusal(db: Database, recipient: User, sender_user_id, sender_link) -> bool:
    """Whether the recipient's opt-out or block list, or this node's policy
    for a Link sender, now refuses this sender's tell."""
    if sender_user_id is not None:
        sender = get_user_by_id(db, sender_user_id)
        if not _usable(sender):
            return True
        return live_message_refusal(db, recipient, sender=sender) is not None
    from netbbs.link.enforcement import LinkPolicyAction, decide_node_action, decide_user_authorship
    _prefix, fingerprint, username = sender_link.split(":", 2)
    if (not decide_node_action(db, fingerprint, LinkPolicyAction.REALTIME).allowed or
            not decide_user_authorship(db, fingerprint, username).allowed):
        return True
    return live_message_refusal(db, recipient, sender_address=f"{username}@{fingerprint}") is not None


def hold_tell(db: Database, bot_id: int, channel_name: str, sender: dict, to, text,
              present: frozenset[str] = frozenset()) -> dict:
    """A tell from the request's caller to a local account, held until that
    account next joins this channel. `present` is the usernames in the
    channel now: a recipient already here is told nothing and nothing is
    stored."""
    _bot, channel = allowed_channel(db, bot_id, channel_name)
    if not isinstance(to, str) or not to.strip() or len(to) > 128:
        raise BotError("A tell needs a recipient: a local account name.")
    to = to.strip()
    if "@" in to:
        raise BotError("Tells reach local accounts on this node only, not Link names.")
    if not isinstance(text, str) or not text.strip() or len(text) > TELL_MAX_TEXT:
        raise BotError(f"A tell must contain 1-{TELL_MAX_TEXT} characters.")
    text = sanitize_text(text).replace("\t", " ").strip()
    if not text:
        raise BotError(f"A tell must contain 1-{TELL_MAX_TEXT} characters.")
    sender_user_id, sender_link, sender_label = _sender_columns(sender)
    try:
        recipient = get_user_by_username(db, to)
    except AuthError:
        recipient = None
    if not _usable(recipient):
        raise BotError(f"There is no account named {sanitize_text(to)} on this node.")
    if recipient.id == sender_user_id:
        raise BotError("A tell is for someone else; you are here already.")
    name = sanitize_text(recipient.username)
    if recipient.username in present:
        return {"held": False, "present": True, "name": name}
    if not may_enter(db, channel, recipient):
        raise BotError(f"{name} cannot enter this channel, so a tell here would never reach them.")
    if _sender_refusal(db, recipient, sender_user_id, sender_link):
        raise BotError(LIVE_BLOCK_REFUSAL.format(name=name))
    now = _now()
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        _prune(db, now)
        total, mine, theirs = db.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(sender_user_id IS ? AND sender_link IS ?), 0), "
            "COALESCE(SUM(caller_user_id = ?), 0) FROM bot_held_lines WHERE bot_id = ? AND kind = 'tell'",
            (sender_user_id, sender_link, recipient.id, bot_id),
        ).fetchone()
        if mine >= TELLS_PER_SENDER:
            raise BotError(f"You have {TELLS_PER_SENDER} tells waiting already; cancel one first.")
        if theirs >= TELLS_PER_RECIPIENT:
            raise BotError(f"{name} has {TELLS_PER_RECIPIENT} tells waiting already; try after their next visit.")
        if total >= TELLS_PER_BOT:
            raise BotError(f"This bot is holding {TELLS_PER_BOT} tells already; try again later.")
        expires = utc_iso(now + dt.timedelta(seconds=TELL_SECONDS))
        cursor = db.connection.execute(
            "INSERT INTO bot_held_lines(bot_id, channel_id, kind, caller_user_id, sender_user_id, sender_link, "
            "sender_label, text, created_at, expires_at) VALUES (?, ?, 'tell', ?, ?, ?, ?, ?, ?, ?)",
            (bot_id, channel.id, recipient.id, sender_user_id, sender_link, sender_label, text,
             utc_iso(now), expires),
        )
    except BaseException:
        db.connection.rollback()
        raise
    db.connection.commit()
    return {"held": True, "tell_id": cursor.lastrowid, "name": name, "expires_at": expires}


def _sender_tells(db, bot_id, channel_name, sender):
    _bot, channel = allowed_channel(db, bot_id, channel_name)
    sender_user_id, sender_link, _label = _sender_columns(sender)
    return channel, sender_user_id, sender_link


def list_tells(db: Database, bot_id: int, channel_name: str, sender: dict) -> list[dict]:
    """The request caller's own waiting tells in this channel only, so a
    public answer never shows a tell made in a private channel."""
    channel, sender_user_id, sender_link = _sender_tells(db, bot_id, channel_name, sender)
    rows = db.connection.execute(
        "SELECT h.id, u.username, h.text, h.created_at FROM bot_held_lines h "
        "JOIN users u ON u.id = h.caller_user_id WHERE h.bot_id = ? AND h.kind = 'tell' "
        "AND h.channel_id = ? AND h.sender_user_id IS ? AND h.sender_link IS ? AND h.expires_at > ? "
        "ORDER BY h.id",
        (bot_id, channel.id, sender_user_id, sender_link, utc_iso(_now())),
    ).fetchall()
    return [{"tell_id": r[0], "to": sanitize_text(r[1]), "text": r[2], "created_at": r[3]} for r in rows]


def cancel_tell(db: Database, bot_id: int, channel_name: str, sender: dict, tell_id) -> dict:
    if isinstance(tell_id, bool) or not isinstance(tell_id, int):
        raise BotError("tell.cancel needs a tell_id.")
    channel, sender_user_id, sender_link = _sender_tells(db, bot_id, channel_name, sender)
    cursor = db.connection.execute(
        "DELETE FROM bot_held_lines WHERE id = ? AND bot_id = ? AND kind = 'tell' AND channel_id = ? "
        "AND sender_user_id IS ? AND sender_link IS ?",
        (tell_id, bot_id, channel.id, sender_user_id, sender_link),
    )
    db.connection.commit()
    return {"cancelled": cursor.rowcount > 0}


def join_candidates(db: Database, channel: Channel, caller) -> list[tuple[int, int, str]]:
    """`(id, bot_id, kind)` of the lines waiting for `caller` in `channel`."""
    user_id, link = caller_columns(caller)
    rows = db.connection.execute(
        "SELECT id, bot_id, kind FROM bot_held_lines WHERE channel_id = ? AND caller_user_id IS ? "
        "AND caller_link IS ? AND expires_at > ? ORDER BY id",
        (channel.id, user_id, link, utc_iso(_now())),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _ago(created_at: str, now: dt.datetime) -> str:
    seconds = max(0, int((now - parse_utc_iso(created_at)).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


class Dropped(Exception):
    """A held line that will never be delivered; its row is already deleted."""


def claim(db: Database, held_id: int, bot: Bot, channel: Channel) -> str:
    """Inside the publication transaction: the body to publish for held line
    `held_id`, its row deleted. `BotError` when another join already took it;
    `Dropped` (row deleted, for the caller to commit) when it expired or a
    tell's recipient now refuses its sender -- silently either way."""
    row = db.connection.execute(
        "SELECT * FROM bot_held_lines WHERE id = ? AND bot_id = ? AND channel_id = ?",
        (held_id, bot.id, channel.id),
    ).fetchone()
    if row is None:
        raise BotError("That held line was already delivered.")
    db.connection.execute("DELETE FROM bot_held_lines WHERE id = ?", (held_id,))
    now = _now()
    if row["expires_at"] <= utc_iso(now):
        raise Dropped()
    if row["kind"] == "line":
        return row["text"]
    recipient = get_user_by_id(db, row["caller_user_id"])
    if not _usable(recipient) or _sender_refusal(db, recipient, row["sender_user_id"], row["sender_link"]):
        raise Dropped()
    if row["sender_user_id"] is not None:
        sender_label = sanitize_text(get_user_by_id(db, row["sender_user_id"]).username)
    else:
        sender_label = row["sender_label"]
    body = (f"{sanitize_text(recipient.username)}: {sender_label} asked me to tell you "
            f"({_ago(row['created_at'], now)}): {row['text']}")
    return body[:MAX_BODY]
