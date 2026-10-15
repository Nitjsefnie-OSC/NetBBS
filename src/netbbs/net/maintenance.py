"""
Node-wide maintenance-mode gate (design doc §13.8): once activated, new connections are refused before
login even begins (see `netbbs.net.login_flow.handle_session`) — the
piece a deliberate shutdown sequence needs to stop admitting new users
while it broadcasts a warning and disconnects everyone already
connected. Deliberately its own tiny module, not folded into
`netbbs.net.session_registry`: gating *new* connections and tracking
*existing* ones are related but distinct concerns.

**Lockdown** (design doc §13.8) is a second, independent gate added to
this same class: a SysOp-toggleable `[M]aintenance mode` that blocks
new *non-SysOp* logins only, checked *after* credentials verify (so a
SysOp can still reach the menu that turns it back off), and reversible
-- unlike `activate()`/`is_active()` above, which is shutdown's
one-way, unconditional, pre-login gate with no bypass and no way back
*once a shutdown actually reaches its disconnect step*, since the whole
node is going away regardless of who's asking. Deliberately named and
checked differently rather than sharing one flag: the two answer
genuinely different questions ("is this node about to disappear" vs.
"should ordinary users be kept out for now"), and conflating them would
either weaken shutdown's hard guarantee or deny a SysOp their own
reason for toggling maintenance mode on in the first place.

`deactivate()` (design doc -- node management) is the one exception to
`activate()`'s "no way back" framing above: a *scheduled* graceful
shutdown now has a countdown a SysOp can cancel before it fires
(`netbbs.net.shutdown.SequenceScheduler`) -- if they do, new-login
admission must reopen too, or a cancelled shutdown would leave the node
silently unreachable forever. Once a shutdown has actually reached
`disconnect_all()`, there is no calling `deactivate()` anymore,
matching the rest of this docstring's claim exactly for that point
onward.

Issue #107 adds one narrow ownership rule to that reversible window.
Each `activate()` remembers the asyncio task which most recently claimed
the hard shutdown gate. A cancelled *older* shutdown task may therefore
only reopen admission if it still owns that gate; if a replacement
shutdown has already activated, the stale task's cleanup is ignored.
The explicit SysOp cancel path remains valid because it first requests
cancellation of the owning shutdown task and then calls `deactivate()`
from the admin-session task -- an external caller may release the gate
once its owner is already cancelling.

Issue #109 adds the analogous ownership concept to *lockdown*, not just
the hard shutdown gate: `enable_lockdown()` now records who turned it on
(`source`, e.g. `"maintenance"` for the plain `[M]aintenance mode`
toggle, `"lock_and_drain"` for the composite `[L]ock & drain` command).
Unlike the shutdown gate's task-based ownership, this is a plain string
tag -- lockdown is only ever toggled by discrete, synchronous SysOp
actions, never something a background task itself later reverses on its
own cancellation, so there is no analogous race to guard against here.
The tag exists purely so `netbbs.net.admin_sessions._lock_and_drain_screen`
can tell "lockdown is on because *I* (the composite command) turned it
on" from "lockdown is on for some other, independent reason" -- and
therefore never silently claims ownership of (or offers to undo) state
it didn't create.
"""

from __future__ import annotations

import asyncio

MAINTENANCE_MESSAGE = "This node is shutting down for maintenance. Please try again shortly."

LOCKDOWN_MESSAGE = "This node is in maintenance mode. Only SysOps may connect right now. Please try again later."

# Design doc -- node management, Thiesi's own dogfood-testing report:
# shown to *every* connecting client, right after the banner and before
# the username prompt, regardless of what account (if any) they're
# about to log in as -- SysOp-ness isn't known until credentials verify,
# so this can't be targeted any more narrowly. Deliberately distinct
# wording from LOCKDOWN_MESSAGE above: that one is the hard rejection a
# non-SysOp actually gets turned away with; this is a heads-up shown to
# someone who may well still get in (a SysOp), so "please try again
# later" would be actively misleading here.
LOCKDOWN_NOTICE = "Note: this node is currently in maintenance mode. Only SysOps may log in right now."


def _current_task() -> asyncio.Task | None:
    """Return the current asyncio task, or ``None`` outside a running loop.

    `MaintenanceMode` is exercised directly by some synchronous unit
    tests as a plain state object, so ownership tracking must stay a
    no-op rather than raising when there is no event loop.
    """
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class MaintenanceMode:
    """One instance per running node (constructed once in
    `netbbs.__main__`, threaded down through `handle_session` the same
    way `throttle`/`presence` already are). Plain flags, not
    `asyncio.Event`s — nothing ever needs to *wait* for either to flip,
    only check current state at the relevant checkpoint (pre-login for
    `is_active`, post-authentication for `is_lockdown_active`).

    `_active_owner_task` is deliberately limited to the hard shutdown
    gate. The separate SysOp-toggleable lockdown remains ordinary state
    and is unaffected by shutdown replacement/cancellation semantics.
    """

    def __init__(self) -> None:
        self._active = False
        self._active_owner_task: asyncio.Task | None = None
        self._lockdown = False
        self._lockdown_source: str | None = None

    def activate(self) -> None:
        self._active = True
        self._active_owner_task = _current_task()

    def deactivate(self) -> None:
        """Reopen new-login admission when the caller still has authority.

        A shutdown task may always undo the gate it most recently
        activated. An external controller (today, the SysOp cancel path)
        may also undo it after it has requested cancellation of the owner
        task. A stale cancelled task from a replaced shutdown cannot undo
        a gate now owned by a newer, still-running shutdown.
        """
        if not self._active:
            return

        owner = self._active_owner_task
        current = _current_task()
        if owner is not None and current is not owner and owner.cancelling() == 0:
            return

        self._active = False
        self._active_owner_task = None

    def is_active(self) -> bool:
        return self._active

    def enable_lockdown(self, *, source: str = "maintenance") -> None:
        self._lockdown = True
        self._lockdown_source = source

    def disable_lockdown(self) -> None:
        self._lockdown = False
        self._lockdown_source = None

    def is_lockdown_active(self) -> bool:
        return self._lockdown

    def lockdown_source(self) -> str | None:
        """Who turned lockdown on (issue #109) -- `None` if it's not
        currently active at all. Lets a caller like `_lock_and_drain_
        screen` distinguish lockdown it itself established from lockdown
        active for some other, independent reason, without needing a
        second boolean of its own."""
        return self._lockdown_source if self._lockdown else None
