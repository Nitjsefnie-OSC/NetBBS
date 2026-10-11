# NetBBS v7.19.1

This patch release makes chat-bot settings selectable in the SysOp console and
restores the catalogue menu formatting. **Nothing migrates:** the node database
stays at schema 130, and the Link, real-time, door API, career and world
versions are unchanged. Upgrading is a wheel swap and a restart; rolling back
to v7.19.0 is the reverse.

## Bot settings are selectable (PR #1235)

Chat-bot settings that contain channels, boards or file areas now open a
picker. Multiple selections can be toggled while paging and searching, and
the draft remains unchanged until the bot editor is saved. A channel removal
also clears dependent grants and an announcement destination from that draft.
Unavailable existing grants remain visible so a SysOp can remove them without
silently regranting anything. The announcement destination has an explicit
`(none)` choice.

The picker now supports this toggle interaction as a reusable editor control.
Its catalogue title, breadcrumb and descriptions follow the console's current
menu preferences, including narrow-terminal fitting. Hidden, MRC and
ineligible inherited resources are excluded from new grants.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.19.0 is the reverse.

## Verification boundaries

- The bot settings UI and reusable picker regression tests cover selection,
  paging, search, discard-before-save, stale grants, dependent-grant cleanup,
  resource filtering and 40/80-column catalogue fitting.
- The focused bot and picker regression set passed: 61 passed. The full
  Windows suite produced 11,754 passed and 136 skipped, with one unrelated
  failure in `test_link_key_rotation.py::test_a_backup_checksums_the_retired_keys`
  because the existing user-level Voidrunner save directory rejected creation
  of `.maintenance.lock` (`PermissionError`).
- NetBSD, real-terminal and live multi-node operational checks remain manual
  evidence; this patch does not change those boundaries.
