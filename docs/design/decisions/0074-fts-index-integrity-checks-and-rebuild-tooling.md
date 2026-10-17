# Issue #74 — FTS index integrity checks and rebuild tooling — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§6.6's "Integrity checking and rebuild" subsection now states the
complete design: `netbbs.search.check_index_integrity`/`rebuild_indexes`,
a standalone `python -m netbbs.search check|rebuild` command, and the
explicit decision that startup does not run this check automatically
(unlike `Database.check_integrity`). Reports drift by id only, never
indexed content, for all three FTS tables.
