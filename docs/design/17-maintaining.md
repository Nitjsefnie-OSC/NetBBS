# NetBBS design — §17. Maintaining this document

Part of the [NetBBS design](../NetBBS-design-doc.md). Section numbers are stable: cite it as §17.

## 17. Maintaining this document

Add or change text here only when it affects:

- product semantics;
- protocol or compatibility guarantees;
- authority and trust boundaries;
- persisted data meaning;
- long-lived user or SysOp behavior;
- roadmap dependency or phase scope.

Keep one current answer per topic. Replace superseded text instead of appending
correction paragraphs. Preserve only rationale which prevents a plausible but
harmful alternative from being chosen again.

Do not add:

- numbered decision rounds;
- implementation walkthroughs;
- changed-file or test lists;
- passing-test totals;
- debugging transcripts;
- transient “next up” status;
- stale issue-resolution commentary.

Use issues for unresolved work, commit/PR descriptions for change narratives,
the engineering record for durable implementation constraints, and Git history
for archaeology.

Put text where its topic lives (issue #1394): a rule goes in the area file
for its top-level section under `docs/design/`, and a decision's reasoning
goes in its own file under `docs/design/decisions/`, listed in §16. Keep the
section map in `docs/NetBBS-design-doc.md` in step when a subsection is
added or renamed. Never renumber an existing section: `§n.n` references
throughout the code, issues and handbooks depend on the numbers.
