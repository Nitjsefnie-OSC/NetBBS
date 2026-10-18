"""Explicit, bounded node queries whose results are safe for public replies."""

from netbbs.auth.users import AuthError, get_user_by_id, get_user_by_username
from netbbs.bots.registry import BotError, allowed_channel
from netbbs.config import get_node_display_name
from netbbs.link.enforcement import LinkPolicyAction, decide_node_action, decide_user_authorship
from netbbs.rendering import sanitize_text
from netbbs.session_history import list_recent_sessions, session_history_name_visible
from netbbs.timeutil import format_for_display


def run_query(db, bot_id, channel_name, query, target, *, caller, online, uptime_seconds):
    bot, _channel = allowed_channel(db, bot_id, channel_name)
    if query not in bot.queries:
        raise BotError("The SysOp has not granted this bot that node query.")
    # Identity comes from the host's pending request, never the bot action.
    identity = caller["id"]
    if isinstance(identity, int):
        current = get_user_by_id(db, identity)
        if current is None or current.disabled_at or current.pending_approval or current.username != caller["name"]:
            raise BotError("This caller is no longer available for node queries.")
    else:
        _prefix, fingerprint, username = identity.split(":", 2)
        if (not decide_node_action(db, fingerprint, LinkPolicyAction.REALTIME).allowed or
                not decide_user_authorship(db, fingerprint, username).allowed):
            raise BotError("This caller is no longer permitted to query the node.")
    if query == "stats":
        name = sanitize_text(get_node_display_name(db))
        minutes = max(0, uptime_seconds) // 60
        return {"node": name, "online_humans": len(online), "uptime_seconds": uptime_seconds,
                "text": f"{name}: {len(online)} human accounts online; uptime {minutes // 60}h {minutes % 60}m."}
    if query == "top":
        return _top(db, target)
    if query != "seen":
        raise BotError("Unknown node query.")
    if not isinstance(target, str) or not target.strip() or len(target) > 128 or "@" in target:
        raise BotError("Seen needs a local account name on this bot's node.")
    unavailable = {"available": False, "text": "No public last-seen information for that name."}
    try:
        user = get_user_by_username(db, target.strip())
    except AuthError:
        return unavailable
    if user.disabled_at or user.pending_approval:
        return unavailable
    label = sanitize_text(user.username)
    if user.username in online:
        return {"available": True, "username": label, "online": True, "text": f"{label} is online now."}
    # Replies are public. Even a SysOp's request must not publish another
    # caller's hidden session history; no channel/activity query exists here.
    if not session_history_name_visible(db, user):
        return unavailable
    candidates = [(user.last_login_at, "login")] if user.last_login_at else []
    for entry in list_recent_sessions(db, user_id=user.id, limit=20):
        candidates.append((entry.connected_at, "login"))
        if entry.disconnected_at:
            candidates.append((entry.disconnected_at, "logout"))
    if not candidates:
        return unavailable
    timestamp, basis = max(candidates, key=lambda item: item[0])
    try:
        # Replies are read by people: the node's one display clock (§6.3).
        shown = format_for_display(timestamp, db)
    except ValueError:
        return unavailable
    return {"available": True, "username": label, "online": False, "at": timestamp, "basis": basis,
            "text": f"{label}: last recorded {basis} {shown}."}


def _top(db, target):
    """A door's standings snapshot (issue #1408, §16 #1378 Decision 4),
    with the door gate and each row's opt-out applied. No target lists the
    doors that have standings to ask for."""
    from netbbs.doors.events import door_events_refusal, public_standings
    from netbbs.doors.registry import list_doors

    target = target or ""
    if not isinstance(target, str) or len(target) > 128:
        raise BotError("Top needs a door name.")
    available = []
    for door in list_doors(db):
        if door_events_refusal(db, door) is None and public_standings(db, door) is not None:
            available.append(door)
    names = [" ".join(sanitize_text(d.name).split()) for d in available]
    wanted = " ".join(target.split()).casefold()
    if not wanted:
        if not names:
            return {"available": False, "doors": [], "text": "No door has published standings yet."}
        return {"available": False, "doors": names,
                "text": "Standings: " + ", ".join(names) + ". Ask for one by door name."}
    matches = [d for d in available if d.name.casefold() == wanted] or [
        d for d in available if d.name.casefold().startswith(wanted)]
    if len(matches) != 1:
        text = "No standings for that door." if not matches else (
            "Which door: " + ", ".join(" ".join(sanitize_text(d.name).split()) for d in matches) + "?")
        return {"available": False, "doors": names, "text": text}
    door = matches[0]
    title, rows, updated_at = public_standings(db, door)
    name = " ".join(sanitize_text(door.name).split())
    shown = [{"rank": r.rank, "name": r.name, "value": r.value, "label": r.label} for r in rows]
    if not rows:
        text = f"{name} {title}: nobody to list yet."
    else:
        text = f"{name} {title}: " + "; ".join(f"{r.rank} {r.name} {r.value}" for r in rows) + "."
    return {"available": True, "door": name, "title": title, "rows": shown, "updated_at": updated_at,
            "text": text[:500]}
