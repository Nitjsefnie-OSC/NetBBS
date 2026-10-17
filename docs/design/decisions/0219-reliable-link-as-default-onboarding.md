# Issue #219 — Reliable Link as default onboarding infrastructure

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** turn Reliable Link (the project's own persistent, internet-
reachable node, run on hosting with roughly a decade-plus operating
history) into the first entry of a small, resilient "reliable nodes"
list new installs can use to get online frictionlessly — as a default
Link seed, an async relay candidate, and (once #168 ships) a live-chat
relay anchor — without becoming a hard dependency or a single point of
failure or responsibility. Builds directly on #168 (real-time relay
protocol) and #201 (managed DNS) without duplicating either's own
decisions; this entry is the product/architecture layer that sits above
both.

**Terminology (locked in):** official prose always says "Reliable
Link" — the same "spell it out in anything official" convention already
established for "NetBBS Link" itself (never "the Link" outside casual
shorthand). "ReLink.NetBBS.org" stays the DNS-only shorthand — a
hostname, not the node's name in any document or UI copy.

**Decision 1 (locked in) — purely a configurable default, never
protocol-privileged.** Nothing in the Link protocol may treat any
reliable node's fingerprint as special, required, or protocol-blessed.
Technically indistinguishable from a SysOp typing in any other seed —
this is the concrete answer to "does this violate the no-central-master
design," not just an assertion of it.

**Decision 2 (locked in) — a list, not a single hardcoded node.**
Resilience by actually having more than one reliable node from day one,
not resilience-in-theory because the field happens to be configurable.
Reliable Link is the flagship/first entry. If it were ever to stop
operating, the intended failure mode is "some other already-established
reliable node is already in the list," not "someone has to
specifically step up" at that moment.

**Decision 3 (locked in) — discovery is hybrid.** A hardcoded fallback
list ships in source (always works, even offline or if the live
endpoint is briefly unreachable on a SysOp's very first run) plus a
live-fetched list from a stable project-controlled endpoint (under
`netbbs.org`, which the project already controls and presumably outlives
any one node), preferred when reachable. Keeps the roster current
without requiring every installed node to upgrade NetBBS itself to
learn about a change.

**Decision 4 (locked in) — one list, three consumers, not three
separate mechanisms.** Default Link seeds for peer discovery; async
relay candidates (falls out of the *existing* `relay_selection.py`
reliability-ranking automatically, once a reliable node is a known peer
with relay serving enabled — no new selection mechanism needed); and
the live-relay anchor for #168's raw-proxy design. This is also the
answer to one of #168's own still-open questions ("does live relay need
its own consent/capacity concept, or reuse the async model's?") — for
v1, neither: just try reachable reliable nodes from the same list.

**Decision 5 (locked in) — relaying carries zero Phase 4 trust
implications.** Using a reliable node as a relay is not that node
vouching for the relayed traffic's origin, and trusting a reliable node
as infrastructure is not a signal about anyone else who also relays
through it. A reachability decision, not a trust decision — matches how
existing async relay consent already works.

**Decision 6 (locked in) — a node cannot participate in Link with an
unset/placeholder display name.** Surfaced during this discussion, not
originally part of it, but adopted alongside it: today's default node
name is the literal placeholder `"NetBBS"`; every default-installed node
sharing that same display name would make human-to-human conversation
about "which node am I talking to" incoherent the moment Link
participation is easy enough that many nodes actually use it. Enforced
before Link participation, not before local-only operation — a
SysOp running a purely local board never has to touch this.

**Decision 7 (locked in) — first-run UX is one screen, two separate,
defaulted-to-accept choices, not one bundled yes/no.** Relay/seed
participation and the public DNS name (#201) are offered together on
one friendly first-run screen, each pre-set to accept (so accepting
both is a two-Enter-keystrokes path) but each independently declinable
with a plain-English explanation of what accepting means. Deliberately
not collapsed into one "get online easily" choice: both declines have a
real, coherent meaning a SysOp might actually want (relay without a
public name — mesh reachability without independent discoverability; or
a public name without relay — already has direct connectivity sorted),
and bundling them would silently reintroduce the exact consent problem
that made #201 land on opt-in in the first place, for whichever half of
that combination a given SysOp didn't actually want.

**Implemented (issue #266), with the previously-open implementation
choices settled as follows.** The live roster is
`https://www.netbbs.org/reliable-nodes.json` — a JSON object
`{"version": 1, "nodes": [{"name", "url"}, ...]}`, served as plain static
content from the project's own web host (source copy and runbook in
`services/reliable_nodes/`), fetched once a day by
`netbbs.link.reliable_nodes.run_scheduled_reliable_nodes_refresh` under the
same off-switch as the release check, bounded at 32 entries with per-field
length caps, and rejected as a whole on any other format version so an old
build keeps its last good copy. A successful fetch *replaces* the built-in
fallback rather than merging with it, so removing a node from the roster
actually stops it being dialed. `[link] enabled` is now tri-state: an
explicit TOML/CLI value always wins; a silent configuration defers to the
node-wide participation decision (`netbbs.link.onboarding`), resolved once
at startup, so accepting on the first-run screen turns Link on as an
outgoing-only node with no port to open. The participation decision also
gates whether the roster is dialed at all, independently of how Link came
to be enabled — a node upgraded in place never dials project
infrastructure until its SysOp says so. Decision 6 is enforced at the
startup boundary: with Link effectively enabled and the placeholder name,
`python -m netbbs` refuses with a `StartupError` naming the fix, the same
shape as the no-SysOp refusal; the first-run screen asks for the name
before recording an accept so a fresh install never reaches that refusal,
and the console's `Settings > Join NetBBS Link` screen refuses to accept
under the placeholder for the same reason. The first-run screen
(`netbbs.net.onboarding_flow`) lives at the two anchors issue #201's prompt
already used — `netbbs.admin`'s first-SysOp bootstrap and, as a fallback,
a SysOp's authenticated login — each choice checking its own state so an
upgraded node is asked only what it never answered. Still open: Reliable
Link's own operational configuration (relay-serving is already on by
default; live-relay capacity planning waits for issue #168).
