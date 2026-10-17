# Issue #81 — supported platform tiers — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§2.1's "Platform support tiers" subsection now states the complete
policy: NetBSD (Tier 1, primary, with dependencies preferably supplied
by pkgsrc), mainstream Linux/systemd
(Tier 2, supported), other POSIX systems (Tier 3, best-effort), and
Windows (development-only, no production-semantics promise). Auditing
every existing `sys.platform`/`os.name` branch (`netbbs.net.
local_terminal`, `netbbs.backup._process_is_running`,
`netbbs.__main__`'s signal-handler setup) found the codebase already
drew exactly this line in practice, each in its own narrow, already-
isolated function — this closes the gap between that existing practice
and an explicit, written contributor-facing policy, rather than
requiring code changes.
