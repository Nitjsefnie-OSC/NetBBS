"""
Per-user manual override for truecolor rendering (design doc --
`netbbs.rendering.gradient`): lets a caller force truecolor on or off
regardless of what `netbbs.net.session.Session.supports_truecolor` was
negotiated (or defaulted) to. A thin typed wrapper over
`netbbs.user_preferences`' generic per-user key-value store, the exact
same shape `netbbs.net.editor_preference` already established for the
fullscreen-editor toggle.

Exists because auto-detection is fundamentally unreliable on Telnet in
particular: many Telnet clients never implement NEW-ENVIRON (RFC 1572)
at all, so a caller whose client genuinely does support truecolor could
otherwise never get it without a manual escape hatch. Defaults to
`"auto"` -- the negotiated/default `Session.supports_truecolor` value
governs until a caller explicitly opts into an override, so no existing
account's rendering changes on its own.

Only applies once a `User` exists -- the anonymous pre-login welcome
banner (`netbbs.net.welcome_banner.load_welcome_banner`) has no `User`
to look an override up for, and can only ever use the negotiated/default
`Session.supports_truecolor`.
"""

from __future__ import annotations

from netbbs.auth.users import User
from netbbs.net.session import Session
from netbbs.storage.database import Database
from netbbs.user_preferences import get_user_preference, set_user_preference

_PREFERENCE_KEY = "color_depth"
_VALID_VALUES = ("auto", "truecolor", "256", "16")


def color_depth_override(db: Database, user: User) -> str | None:
    """`None` means no override -- use `Session.supports_truecolor` as
    negotiated. Otherwise `"truecolor"`, `"256"` or `"16"` (issue #1348),
    an explicit forced choice."""
    value = get_user_preference(db, user, _PREFERENCE_KEY, default="auto")
    return None if value == "auto" else value


def set_color_depth_override(db: Database, user: User, value: str) -> None:
    if value not in _VALID_VALUES:
        raise ValueError(f"value must be one of {_VALID_VALUES}, got {value!r}")
    set_user_preference(db, user, _PREFERENCE_KEY, value)


def effective_truecolor(session: Session, db: Database, user: User) -> bool:
    """The single function every post-login `gradient_text` call site
    should use for its `truecolor` argument -- override-wins-over-
    negotiated logic lives here once, not duplicated per call site."""
    override = color_depth_override(db, user)
    return getattr(session, "supports_truecolor", False) if override is None else override == "truecolor"


def sixteen_colors_for(session: Session, override: str | None) -> bool:
    """Whether `session` gets only the 16 colours (issue #1348) under
    `override` (`color_depth_override`'s answer): what its terminal type
    said under auto, and otherwise the caller's own choice."""
    if override is None:
        return getattr(session, "detected_sixteen_colors", False)
    return override == "16"


def apply_color_depth(session: Session, override: str | None) -> None:
    """Carry the caller's colour depth into `session`, from the next write
    on: at login, and when they change it on their profile."""
    session.sixteen_colors = sixteen_colors_for(session, override)
