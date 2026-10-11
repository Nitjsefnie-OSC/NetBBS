# NetBBS v7.19.3

This patch release removes a stray blank row at the top of empty picker
screens. **Nothing migrates:** the node database stays at schema 130, and the
Link, real-time, door API, career and world versions are unchanged. Upgrading
is a wheel swap and a restart; rolling back to v7.19.2 is the reverse.

## Empty pickers start on the first row (PR #1239)

An empty list, such as Chat bots with no bots configured, cleared the screen
and then wrote a newline before its breadcrumb, so the heading started on the
second row. The breadcrumb now starts on the first row after the clear,
matching populated pickers and the draft editor. The separator is kept when
the picker appends below existing output or follows a banner.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.19.2 is the reverse.

## Verification boundaries

- PR #1239's regression test checks the first visible row on entry and after
  returning from model settings, for empty and populated bot lists. The empty
  case fails without the fix.
- NetBSD, real-terminal and live multi-node checks remain manual evidence;
  this patch does not change those boundaries.
