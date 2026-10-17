# Issue #1223 — Retro Trivia and War Dialer as standalone doors — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The issue asked whether the other two bundled games can follow Voidrunner
(#1214, #1216): each in its own repository, vendored into NetBBS as tagged
releases, and runnable under other BBS software. The maintainer decided on
2026-10-11.

**Starting point.**
- **Retro Trivia** imports nothing outside the standard library, and NetBBS
  imports nothing from it. It keeps no state between launches, so there are
  no saves, backups or migrations to carry.
- **War Dialer** is standard-library only too, but it is coupled to NetBBS in
  three places:
  - It refuses to launch without `war_dialer_owner` and binds its world to
    that ID.
  - Its SysOp administration and backup checks live in NetBBS
    (`netbbs.doors.war_dialer_admin`, `netbbs.backup`), and both import its
    internals.
  - Its idle detection uses `select()`, which Windows pipes do not support.

  Its shared SQLite world (WAL, `WAR_DIALER_DB_PATH`) works unchanged under
  any host whose nodes run on one machine.

**Decision 1 — one repository per game.** The games live in Thiesi/RetroTrivia
and Thiesi/WarDialer, shaped like Thiesi/Voidrunner: own issues, CI, tags and
releases. Each stays one self-contained file. The drop-file parser and the
CP437 table are copied into each one rather than shared (#1214 Decision 4).
Rejected: one shared repository for the doors, where releases and issues would
have to say which game they mean.

**Decision 2 — Retro Trivia gets full portability.** It is extracted and
vendored, then gains DOOR32.SYS/DOOR.SYS, CP437 and Windows socket mode,
following Voidrunner's implementation.

**Decision 3 — outside NetBBS, a War Dialer world owns itself.** The first
launch against a new world, without `war_dialer_owner`, writes a random owner
ID into it and records the world as *self-owned*. Later launches trust that ID
only for a self-owned world. Each BBS points `WAR_DIALER_DB_PATH` at its own
world, so the file's location already keeps one BBS's user numbers out of
another's world. A world NetBBS created stays *host-bound*: every launch must
present the matching `war_dialer_owner`, exactly as the world ownership rule
above requires. A launch without one is refused, never quietly trusted, so a
foreign BBS pointed at a NetBBS world by mistake cannot create or open rows in
that node's user-ID namespace. Under NetBBS, `war_dialer_owner` still wins.

A world that is neither, an *unbound* world, is adopted on its next launch,
because nothing in it belongs to a host. Today's standalone launches create
such worlds; any NetBBS launch would already have bound them.
- **A launch without `war_dialer_owner`** marks it self-owned and writes its
  owner ID.
- **A launch with `war_dialer_owner`** binds it to that host, exactly as a
  first host launch does now.

Either way the adoption is one transaction before any player row is read, and
an unbound world never stays unbound past its first launch. Rejected:
- an ID the SysOp must set, which is one more setup step for no added safety;
- an ID derived from the drop file's BBS name, which a rename would turn into
  a lockout.

**Decision 4 — administration moves into the door, and NetBBS wraps it.**
`war_dialer.py --admin` offers `status`, `maintenance`, `close-season`
and `reset`. Its backup is a SQLite-consistent snapshot taken with SQLite's
backup API under the door's own maintenance lock, never a copy of the `.db`
file alone, whose latest committed pages may still be in the `-wal` file.
This is the same rule as the node backup's world component above.
NetBBS's `war_dialer_admin` calls these commands and keeps its own
requirement of a verified node backup before a destructive operation.
The documented `administer_world` function is the in-process equivalent of
the CLI; NetBBS uses it so the console and CLI share the same transactions.
`status` remains read-only. Every mutation records an audit entry, and reset
retains player identities, account age, ownership and the latest 100 entries.
Destructive commands require maintenance on, the exact world filename and a
reason; maintenance stays on afterward. Snapshots default to a fresh file
beside the world, or `--snapshot` names a new file; existing files are refused.
`--world` overrides `WAR_DIALER_DB_PATH`, and `--json` selects machine-readable
status/errors. Backup inspection uses `inspect_world`; hosts still enforce
their own path and ownership policy and use `world_session` for maintenance.
Rejected: administration that only NetBBS provides, which would leave
another BBS's SysOp no season or reset tools.

**Decision 5 — narrow the boundary, then pin what is left.** NetBBS goes
through the `--admin` commands wherever it can. The few names it still
imports, such as the world inspection backup needs, are pinned by a test,
as Voidrunner's three are (#1216).

**Decision 6 — Retro Trivia first.** Retro Trivia is extracted, vendored
and made portable first. War Dialer follows in three steps: the admin
entry point, then extraction and vendoring, then portability.
