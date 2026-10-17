# Issue #1214 — Voidrunner on other BBS software — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The issue asked what it takes to run Voidrunner as a door under other BBS
packages, such as Synchronet, Mystic and ENiGMA½. The maintainer decided on
2026-10-11. Implementation is tracked in Thiesi/Voidrunner#1–#6 and #1216.

**Starting point.** The door is already one stdlib-only Python file that
imports nothing from `netbbs` and runs standalone. It manages its own
storage: JSON careers, a session lock and Hall of Fame records under
`VOIDRUNNER_SAVE_DIR`. Four assumptions tie it to NetBBS:
- the `NETBBS_DOOR_INFO` JSON drop file;
- UTF-8 output, while classic BBS callers mostly use CP437 terminals;
- stdio as the only transport, while Windows BBSes pass a Winsock handle;
- NetBBS's resize protocol: a drop-file rewrite followed by SIGUSR1.

**Decision 1 — both platforms.** The door supports Unix hosts that launch it
over stdio or a PTY, and Windows hosts that pass a DOOR32 socket handle. On
a socket, the door does the telnet duties itself: it filters IAC sequences,
escapes 0xFF in its output, and treats CR NUL as Enter.

**Decision 2 — its own repository.** Voidrunner lives in
Thiesi/Voidrunner, with its history and tests (Thiesi/Voidrunner#1). Its
game design text moved with it, to that repository's `docs/design.md`
(#1216). This document covers only how NetBBS hosts the door (Phase 7).

**Decision 3 — DOOR32.SYS and DOOR.SYS.** Both drop files are read, mapped
onto the same fields `NETBBS_DOOR_INFO` provides. When `NETBBS_DOOR_INFO`
is set, it wins, so behaviour under NetBBS does not change. A foreign drop
file defaults to 16 colours. Outside NetBBS the save directory and branding
are neutral.

**Decision 4 — CP437 inside the door.** The CP437 encoding and its glyph
mapping live in the door's own file and are not shared with NetBBS's CP437
work (#929). A portable door depends on nothing outside itself, and the
table is small enough that duplicating it is cheaper than coupling the two.

Encoding and colour depth are separate settings, and neither drop file
names a character set. The door picks its encoding as follows:
- **Under NetBBS:** always UTF-8. `door_info.json` carries no charset
  either; NetBBS converts the door's UTF-8 output for a caller on a CP437
  terminal profile.
- **Under a foreign drop file:** CP437 by default, because that is what
  classic BBS callers' terminals expect.
- **Either way:** the player can override the encoding with a new setting,
  saved with the career as the colour preset is. The setting is separate
  from the colour and artwork presets. Those presets still never change
  the encoding, as Voidrunner's presentation contract says; `plain` stays an
  artwork fallback, not a transport change.

**Decision 5 — Synchronet is the test target.** Synchronet runs native
doors on both platforms: stdio mode on Linux and DOOR32 sockets on Windows.
The field test uses SyncTERM in CP437. Mystic or ENiGMA½ is an optional
second Unix check.

**Decision 6 — NetBBS vendors a tagged release.** NetBBS keeps bundling
Voidrunner by copying the single file from a tagged release into
`netbbs.doors.bundled`, recording where it came from
(`scripts/vendor_door.py`, `VENDORED.json`). A new door version
reaches NetBBS through a deliberate, reviewable bump. Rejected: making the
door a pip dependency, which adds a package to every install for one file.
NetBBS keeps the door's registration, `VOIDRUNNER_SAVE_DIR`, and the
backup/restore integration, so existing careers stay where they are and
load unchanged.

Resize stays a NetBBS-only feature: other hosts neither rewrite a drop file
nor signal, so the door keeps its starting size there.
