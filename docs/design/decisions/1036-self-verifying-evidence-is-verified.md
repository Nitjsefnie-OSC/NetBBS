# Issue #1036 — self-verifying evidence is verified — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

`ingest_trust_objects` stored an embedded `self_verifying` signal and counted
it on its label: a configured reporter in scope could quarantine a node,
together with a second domain, with "evidence" that proved nothing.
Normative description: §12.6.

**Decision 1 — only signed equivocation reproduces.** It is the one violation
whose evidence a receiver checks against its own record of the subject's keys,
trusting nothing about the issuer. Revoked-key use, invalid authority and an
invalid signature delivery stay observer claims when they come from elsewhere.

**Decision 2 — unverified is kept, not refused.** The signal is stored and
shown as unverified, so a SysOp sees what was claimed, and re-checked once the
subject's keys are known. It counts toward no quarantine.

**Decision 3 — verified evidence counts toward the threshold, and is not
promoted to a local observation.** This supersedes the earlier §12.6 sentence
that made reproduced evidence the receiver's own observation, written for the
digest path. With automatic issuance (#589), promotion would let one issuing
node quarantine its subject everywhere; the two-domain rule keeps the
independence §12.7 is built on. Existing signals stop counting until their
evidence reproduces here (migration 112; release note).

**Decision 4 — the digest-evidence path is deleted.** `fetch_trust_evidence`,
`verify_evidence_bytes` and `activate_reproduced_digest_signal` had no
production caller, and the last promoted reproduced evidence to a local
observation, which Decision 3 rejects. Equivocation evidence always fits
inline, and fetching an issuer-named locator was network access with no
remaining use.
