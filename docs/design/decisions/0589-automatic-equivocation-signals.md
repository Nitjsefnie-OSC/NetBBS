# Issue #589 — automatic equivocation signals — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The open half of the entry below: when a node accuses another. Of everything
the code observes, only equivocation -- one node signing two different objects
into the same slot of one chain -- carries proof a receiver checks itself
(#1036). Revoked-key use, invalid signatures, bad authority, floods and
malformed traffic are observer claims nobody else can reconstruct, and no
production code records them as measurements. Normative description: §12.5,
§12.6, §12.9; the Handbook's "Policy trust settings".

**Decision 1 — fully automatic, equivocation only** (the maintainer's choice;
the alternatives were the node proposing and the SysOp signing, or no
issuance at all). Where a chain refuses an extension because its slot is
taken -- a key-transition chain, a post's content chain, a board's lifecycle
chain -- and both objects verify under one node's keys, the node keeps both,
records a self-verifying local observation, which quarantines that node's
identity integrity here (§12.8), and signs a `signed_equivocation` signal with
both objects embedded. Reordering (an extension of a head not on file yet) and
an exact resend are not forks.

**Decision 2 — receivers verify, and two domains still have to agree.** The
signal counts at a subscriber only once its proof reproduces there (#1036),
and only toward the two-domain threshold, so one node's automatic accusation
does not by itself quarantine its subject anywhere else.

**Decision 3 — bounded, switchable, revocable.** At most one live signal per
subject and five signed in any 24 hours; never about the issuing node itself.
A node-wide switch (Settings → Policy trust → Signals, on by default) stops
issuing and revokes every live signal on the next pass. A SysOp can revoke one
signal and keep the evidence, and clearing the evidence revokes its signal.
Honest forks happen -- a node restored from an old backup, a cloned VM -- so
the Handbook says what such a signal about one's own node means.

**Decision 4 — recovery waits for a SysOp** (§12.9), as the section already
required; until this, every trigger recovered after the 24-hour hold.

**Decision 5 — nothing left unwired.** The digest-evidence path went with
#1036 (its Decision 4). `build_trust_signal`, `build_equivocation_evidence`,
`record_local_observation` and `clear_local_observation` now have callers, so
#589 leaves the production-callers allowlist.
