# Issue #1081 — a console resource opens on its own fields — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The first field test's SysOp had to press `[E]dit` on every board, area and
channel before changing anything, one extra screen on the most common path.
Normative description: §3.5.

**Decision 1 — one screen, still a draft** (the maintainer's decision).
Changes wait for `[S]ave`. Rejected: saving each field as it changes, the way
the Profile does. A resource's fields are checked together (a blank name, a
door's executable against its arguments), a Linked board's or area's name and
description travel to its peers once per save, the moderation log keeps one
entry per save, and with the cursor resting on a live field a stray Enter or
arrow key would change a resource at once.

**Decision 2 — fields without letters, actions by their keys.** On every one
of these screens a field letter collided with an action letter (on a board,
`D` was Description and Down, `R` Read level and Remove, `P` Pinned and
Pending posts; on a door, `D` was Description and Delete). Fields were chosen
by the cursor alone until issue #1158 numbered every editor's fields (its
Decision 8); numbers cannot collide with the action letters. Rejected:
keeping field letters and moving the actions to a second page, which adds a
keystroke to every action instead of removing one from every edit.

**Decision 3 — a changed draft hides the actions.** While the draft differs
from what is stored only `[S]ave` and `[B]ack` are offered. Otherwise `[U]p`,
`[L]ink` or `[S]tart service` would act on a resource whose screen shows
values that are not stored yet. It also settles the one clash between two
actions: on a door, `[S]ave` and `[S]tart service` are never offered together.

**Decision 4 — six screens, no more.** Accounts keep their screen: each key
there is a separate, confirmed operation with its own log entry, not a field
of one form. The settings screens already open into their editors, and the
network and login limits screen is a hub of six groups that would not fit
80x24 as one form.
