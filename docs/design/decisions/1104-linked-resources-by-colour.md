# Issue #1104 — Linked resources by colour — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The pre-release check of the first field test's fixes asked to tell Linked
boards, areas and channels apart without another column. Normative
description: §3.6.

**Decision 1 — the name's colour, not a word or a column.** Rejected: the
`[LINK]` word the board list's "about" text led with, which a narrow terminal,
a short description level and SysOp list art all dropped, and a column, which
costs every other column width. The colour is the one a linked node already
has after a chat speaker's `@`, so "from the wider network" reads one way
everywhere.

**Decision 2 — a marker where colour can't carry it.** A plain-ASCII caller
and a row inside list art get ` ~` after the name. Rejected: a legend row
above the list, which would cost a list row on every page of a full list;
Ctrl-H carries the explanation instead.
