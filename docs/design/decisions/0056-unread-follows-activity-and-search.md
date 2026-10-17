# Issue #56 — unread, follows, activity, and search

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§6.6 now states the complete design: cursor-based read/unread state for
boards, file areas, and channels (mail already had this); replies/mentions
derived from existing `parent_post_id`/message-body fields with no new
schema; a follow/favourite table independent of channel membership and node
carry; a `[N]ew scan` activity surface covering every accessible resource,
not only followed ones, with a direct jump to the first unread item; local
FTS5-backed search scoped to this node's own carried content, explicitly
never broadcast over Link; and a zero-backfill migration story (existing
users' read cursors start empty; first post-upgrade visit sets the
baseline).

Implemented: the read-cursor table (`netbbs.activity`), the follow table, and
the `[N]ew scan` main-menu screen, wired into board/file-area viewing and
channel scrollback replay. Verified against a real Telnet session, not just
scripted tests.

Also implemented: local FTS5-backed search (`netbbs.search`) over board
posts, files, and channel scrollback, synced from every write path, gated by
the exact same visibility rules browsing already enforces, and surfaced as a
new `[/] Find` main-menu entry that jumps straight to a selected hit. FTS5
availability, this round's stated blocker, was resolved by tracing pkgsrc's
actual build chain rather than empirical access to a NetBSD box: `lang/
python312` buildlinks against `databases/sqlite3`, whose own Makefile passes
`--fts5` unconditionally, so the target Python build should always have it —
and a build that doesn't fails the migration loudly rather than silently
disabling search.

Issue #56 is fully implemented; all four §6.6 subsections have shipped.
