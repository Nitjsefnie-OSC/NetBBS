# Issue #200 — War Dialer: async multiplayer BBS-crew door game — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**War Dialer is vendored, and its design lives with it.** The game is developed
in its own repository, Thiesi/WarDialer. Its rules, Rank, seasons, economy,
crews, world schema, administration and presentation contract are specified in
that repository's `docs/design.md`, which carries this section's former text
(issues #1223, #1228). NetBBS ships one tagged release of its single file,
unchanged: `scripts/vendor_door.py war_dialer <tag>` fetches it and records its
origin in `src/netbbs/doors/bundled/VENDORED.json`, and the test suite refuses
a copy that differs from that record. A fix to the game goes upstream and
arrives with the next vendored release.

NetBBS owns how it hosts the door, as below. Its backup and SysOp console
reach into the door through five names: `inspect_world`, `world_session`,
`administer_world`, `WorldStateError` and `_event_plain`. A release that
renamed them would break NetBBS's backups and console rather than the game, so
NetBBS's suite pins them.

**World paths (issue #362, slice 4).** The native runtime supplies the bundled
War Dialer with `<resolved-node-db-filename>.doors/war-dialer.db` beside that node
database. This derives from the persistent database locator, never mutable node
or door display names. Distinct database paths get distinct defaults; renaming or
relocating the node database requires moving its companion world or an explicit
override. Standalone execution keeps the legacy home-directory default.

A profile's `WAR_DIALER_DB_PATH` overrides the process setting, which overrides
the node default. Overrides expand to absolute paths before entering the door's
temporary/installation cwd. Only the bundled entrypoint/module or an explicit
profile override receives this setting; the full parent environment stays private.
SysOps can inspect the effective path through Compatibility setup / Check setup.

When the node default is absent and a legacy home-directory world exists, launch
requires explicit migration or override rather than silently replacing player data.
Migration is manual, with stopped sessions, a SQLite-consistent backup and verified
node-local user-ID ownership. An explicit override is an operator choice and must
not point independent nodes at the same world.

**World ownership and node backup.** First host launch binds the world to an opaque
random namespace retained in the node database as `war_dialer_owner` and forwarded
only in War Dialer metadata. Subsequent launches must match it; a bound world
cannot be entered as a standalone Guest. Legacy adoption requires the documented
manual user-ID check. A standalone launch outside NetBBS owns its world itself
(§16, issue #1223). Backups preserve the node namespace with all discovered
existing worlds: the node-default companion and registered profile/process
overrides, deduplicated by resolved path. A world component contains
SQLite-consistent snapshots, supported schema versions and checksums, with a
limit of 64 worlds and 512 MiB per world.

Backup rejects active game sessions; restore requires stopped node services,
explicit destinations for every archived world and the paired node database.
Read-only verification checks schema, integrity and namespace ownership. World
staging/rollback stays on each destination filesystem; old WAL/SHM/journal files
participate in the switch and rollback. The door's session guard holds shared
read leases during play and an exclusive lease during maintenance; backup and
restore take it through `world_session`. Restore retains the prior generation
and journals unresolved failures. Service/profile path activation and
old-binary exclusion are manual SysOp actions, as documented in the door guide.

**SysOp competition controls.** The door administers its world itself
(`--admin`, issue #1227); NetBBS wraps it. The SysOp console (a War Dialer
door's **[W]orld** screen) and `python -m netbbs.doors.war_dialer_admin` provide
read-only status, persistent maintenance and confirmed season/reset operations
through the door's `administer_world`. NetBBS adds the host's checks: the world
must be one of the node's discovered worlds and owned by its namespace, and a
destructive control requires maintenance on, an exact-filename confirmation, a
reason, and a freshly created and verified complete node backup. The CLI
additionally requires a stopped node; the console does not (issue #726, the
maintainer's decision), since maintenance already keeps callers out, the
world's session guard refuses anyone still inside, and the backup is the same
live backup the console's Backup screen takes. The console needs a live node
for the identity directory that backup captures, and records the SysOp account
in the world's audit and NetBBS's audit log. Maintenance stays on until the
SysOp explicitly reopens the world.

Host metadata is either absent (a standalone launch; the world is then
self-owned, see §16, issue #1223) or valid.
Unreadable, oversized, malformed or invalid identity/dimension metadata is refused
before world creation; it never falls back to Guest. Caller messages remain clear
and SysOp diagnostics are bounded. Commands do not activate or redeploy services.
