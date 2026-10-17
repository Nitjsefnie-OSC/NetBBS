# Issue #1394 — splitting the design document by area — decided and built

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The design document had grown to about 15,900 lines in one file, more than a
third of it (§16) a log of 81 decisions. Reading it whole was no longer
realistic for a person or an agent, contradictions between distant sections
went unnoticed, and every scoping decision edited the same file, so
concurrent design work collided in one diff. The maintainer approved the split
as high priority on 2026-10-17, to be done before the next implementation
round, whose scoping decisions would all have edited the monolith.

**Decision 1 — one file per top-level section, numbered as the section.**
`docs/design/NN-<area>.md` holds section `NN` and nothing else, so `§12.4`
is found in `12-trust.md` without a lookup. An earlier draft grouped §1 with §2
and §4 with §5 in fewer files numbered 01–15, which made a file's number
disagree with the sections it held; that is rejected.

**Decision 2 — section numbers and headings never change.** `§n.n` references
run through the code (over a thousand in `src/` alone), the issues, the
worklog and the handbooks. The split moved text without renumbering anything,
and a new subsection takes the next free number rather than renumbering its
neighbours.

**Decision 3 — the old path stays as the entry point.** `docs/NetBBS-design-doc.md`
keeps the preamble, the current status and a section map linking every
subsection, and holds no normative rule of its own. Every existing link to the
file keeps working; the two links that pointed at an anchor inside it (in the
developer handbook) were updated.

**Decision 4 — one file per decision.** Each §16 entry lives in
`docs/design/decisions/NNNN-<slug>.md`, named after its issue zero-padded so
the directory lists in issue order, and `16-decisions.md` is an index table
(issue, decision, state). The few decisions that never had an issue of their
own are named by their slug alone and listed after the numbered ones. A new scoping decision adds a file and a row; it
does not edit anyone else's. Rejected:
- **keeping §16 as one file**, which left a 5,700-line file in place and the
  collisions with it;
- **grouping decisions by area**, which forces a single area onto decisions
  that span several (a Link moderation decision touches chat, boards, Link
  transport and trust).

**Decision 5 — area text stays normative.** A decision file keeps the
reasoning and the rejected alternatives; the rule it produces is written into
the area section it changes, as before. When the two disagree, the area text
is the answer and the decision file is stale.

The split itself was mechanical and verified byte for byte: only the
separators between sections, each new file's wrapper lines and the depth of
relative links changed. The maintenance rules are in §17 and in `AGENTS.md`'s
documentation policy.
