# Issue #82 — operator-ready installation and release path — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

[SysOp handbook](../../NetBBS-SysOp-Handbook.md) is the complete operator lifecycle:
install (a real, tested non-editable wheel build/install with no
source-checkout dependency, now sourced only from official GitHub
releases), first-SysOp bootstrap via the existing `netbbs.admin`
CLI, running under systemd/rc.d (`examples/netbbs.service`/`netbbs.rc`,
the rc.d script since confirmed working on real NetBSD hardware,
including its `LD_LIBRARY_PATH` handling for the pkgsrc-vs-base OpenSSL
runtime-linking gap documented in the worklog §10), persistent
state paths, backup/restore (linking the existing disaster-recovery
drill), upgrading, version/schema compatibility, and uninstalling
without losing data. `python -m netbbs --version` (issue #82) prints
the release version and expected schema number together. Installing a
release from the console arrived later (§6.7, issue #731); manually
installing an official GitHub-release wheel into the node's virtual
environment remains supported.
