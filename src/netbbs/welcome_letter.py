"""
The welcome letter (issue #1352; design doc §9, "Welcome letter"): a
letter the SysOp writes once, which every new account finds unread in its
mailbox from the moment it can sign in.

It is an ordinary letter from a real staff account the SysOp chose, so a
reply reaches a person. Each copy is written at delivery, with `{user}`,
`{node}` and `{date}` filled in, and is frozen from then on: editing the
template changes only the letters still to come. Delivery happens where an
account becomes usable -- open self-registration over Telnet, the web or
SSH, the approval of a pending account, and an account created on the
SysOp console -- never for an existing account, and never twice for one
account.

Delivery never stands in the way of an account: a sender that is gone or
can no longer write mail, or a template that no longer validates, leaves
the account created, the failure logged and kept for the console screen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from netbbs.auth.users import SYSOP_LEVEL, User, get_user_by_id
from netbbs.config import get_config, get_node_display_name, set_config, set_config_without_commit
from netbbs.guest import guest_is_eligible
from netbbs.mail import mail_access_refusal, send_mail_without_commit
from netbbs.storage.database import Database
from netbbs.timeutil import format_for_display, resolve_display_preferences, utc_now_iso

_logger = logging.getLogger(__name__)

ENABLED_KEY = "welcome_letter_enabled"
SENDER_KEY = "welcome_letter_sender_id"
SUBJECT_KEY = "welcome_letter_subject"
BODY_KEY = "welcome_letter_body"
_SENT_COUNT_KEY = "welcome_letter_sent"
_LAST_SENT_KEY = "welcome_letter_last_sent"
_LAST_FAILURE_KEY = "welcome_letter_last_failure"
#: Per-account mark that its letter went out, so no path sends a second.
_RECEIVED_KEY = "welcome_letter_received"

#: What the template holds until the SysOp writes their own.
SAMPLE_SUBJECT = "Welcome to {node}, {user}!"
SAMPLE_BODY = (
    "Hi {user},\n"
    "\n"
    "Welcome to {node} -- glad you found us.\n"
    "\n"
    "Have a look around the message boards and say hello in live chat. If "
    "anything is unclear, or you have an idea for the board, reply to this "
    "letter: it comes straight to me.\n"
    "\n"
    "See you around!"
)

#: The words a template may use, filled in for each new account.
PLACEHOLDERS = ("{user}", "{node}", "{date}")


@dataclass(frozen=True)
class WelcomeLetter:
    """The welcome letter's settings and what has become of it."""

    enabled: bool
    sender_id: int | None
    sender: User | None
    subject: str
    body: str
    sent: int
    last_recipient: str | None
    last_sent_at: str | None
    last_failure: str | None
    last_failure_at: str | None


def _json(db: Database, key: str) -> dict:
    try:
        value = json.loads(get_config(db, key) or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def load_welcome_letter(db: Database) -> WelcomeLetter:
    raw_sender = get_config(db, SENDER_KEY)
    sender_id = int(raw_sender) if raw_sender and raw_sender.isdigit() else None
    # `None` once the account it named is deleted.
    sender = get_user_by_id(db, sender_id) if sender_id is not None else None
    last_sent = _json(db, _LAST_SENT_KEY)
    failure = _json(db, _LAST_FAILURE_KEY)
    sent = get_config(db, _SENT_COUNT_KEY) or "0"
    return WelcomeLetter(
        enabled=get_config(db, ENABLED_KEY) == "1",
        sender_id=sender_id,
        sender=sender,
        subject=get_config(db, SUBJECT_KEY) or SAMPLE_SUBJECT,
        body=get_config(db, BODY_KEY) or SAMPLE_BODY,
        sent=int(sent) if sent.isdigit() else 0,
        last_recipient=last_sent.get("user"),
        last_sent_at=last_sent.get("at"),
        last_failure=failure.get("text"),
        last_failure_at=failure.get("at"),
    )


def sender_problem(db: Database, sender: User | None) -> str | None:
    """Why `sender` cannot send the welcome letter, or `None` when it can:
    a staff account that exists, may sign in and may write mail."""
    if sender is None:
        return "No sender is set, or the account it named is gone."
    if sender.disabled_at is not None:
        return f"{sender.username} is disabled."
    if sender.pending_approval:
        return f"{sender.username} is still waiting for approval."
    if guest_is_eligible(db, sender):
        return "The guest account can't send letters."
    if sender.user_level < SYSOP_LEVEL and not sender.staff_permissions:
        return f"{sender.username} isn't staff; the letter comes from the SysOp or a staff member."
    refusal = mail_access_refusal(db, sender)
    if refusal is not None:
        return f"{sender.username} can't write mail: {refusal}"
    return None


def fill_placeholders(db: Database, text: str, recipient_name: str) -> str:
    """`text` with `{user}`, `{node}` and `{date}` filled in. Nothing else
    in braces is touched, so a letter can still mention `{this}`."""
    _fmt, tz_name = resolve_display_preferences(db)
    date = format_for_display(utc_now_iso(), override_format="%Y-%m-%d", override_timezone=tz_name)
    return (
        text.replace("{user}", recipient_name)
        .replace("{node}", get_node_display_name(db))
        .replace("{date}", date)
    )


def _record_failure(db: Database, recipient: User, text: str) -> None:
    _logger.warning("Welcome letter to %s not sent: %s", recipient.username, text)
    set_config(db, _LAST_FAILURE_KEY, json.dumps({"text": text, "at": utc_now_iso(), "user": recipient.username}))


def send_welcome_letter(db: Database, recipient: User) -> bool:
    """Send `recipient` the welcome letter if it is on and they haven't had
    one; whether a letter went out. Called where an account becomes usable.
    Never raises for a letter that can't go: the account stands regardless,
    and the reason is logged and kept for the console."""
    letter = load_welcome_letter(db)
    if not letter.enabled or recipient.pending_approval or guest_is_eligible(db, recipient):
        return False
    received = db.connection.execute(
        "SELECT 1 FROM user_preferences WHERE user_id = ? AND key = ?", (recipient.id, _RECEIVED_KEY)
    ).fetchone()
    if received is not None:
        return False
    problem = sender_problem(db, letter.sender)
    if problem is not None:
        _record_failure(db, recipient, problem)
        return False
    assert letter.sender is not None
    subject = fill_placeholders(db, letter.subject, recipient.username)
    body = fill_placeholders(db, letter.body, recipient.username)
    now = utc_now_iso()
    try:
        db.connection.execute("BEGIN IMMEDIATE")
        try:
            send_mail_without_commit(db, letter.sender, recipient, subject, body)
            db.connection.execute(
                "INSERT INTO user_preferences (user_id, key, value) VALUES (?, ?, ?)",
                (recipient.id, _RECEIVED_KEY, now),
            )
            set_config_without_commit(db, _SENT_COUNT_KEY, str(letter.sent + 1))
            set_config_without_commit(db, _LAST_SENT_KEY, json.dumps({"user": recipient.username, "at": now}))
            # A letter sent means whatever stopped the last one is fixed
            # (Claude review of #1353: the warning outlived its cause).
            db.connection.execute("DELETE FROM node_config WHERE key = ?", (_LAST_FAILURE_KEY,))
        except BaseException:
            db.connection.rollback()
            raise
        db.connection.commit()
    except Exception as exc:  # the account must stand whatever the letter does
        _record_failure(db, recipient, str(exc) or type(exc).__name__)
        return False
    return True
