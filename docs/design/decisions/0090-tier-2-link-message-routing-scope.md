# Issue #90 — tier-2 Link message routing scope — deferred

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§10.6 now states the complete answer: distinct from the unrelated,
already-decided `tier2_personal_key` non-goal (§10.2, a hard architectural
blocker); this is a recipient-*reachability* question with no equivalent
blocker, just not built. Neither issue #85's relay nor issue #630's
identity introduction provides tier-2 reachability: the first carries
content, the second lets a receiver verify the author of carried content,
and Link mail still requires a completed hello with the recipient's node.
The relayed, self-certifying `HelloMessage` bundle exchange this entry
first named as the missing piece was built for #630, for verification only;
§10.6 lists what mail would need beyond it. Deliberately deferred rather
than scoped as active work: no architectural blocker, just not needed yet.
