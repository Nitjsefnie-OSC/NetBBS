# Issue #80 — end-to-end regression tests for cross-subsystem Link orchestration — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§14.1's "Cross-subsystem end-to-end scenarios" subsection now states
the complete design: `tests/test_link_end_to_end.py`, a real-transport,
real-domain-read-path vertical slice per implemented Link product
surface. It now covers linked boards, linked channels, remote file
catalogue/fetch, and Link mail, with restart and duplicate-delivery
coverage where the surface has persisted state. The mail vertical also
covers a dead-letter -> replay -> real-redelivery cycle end to end. Confirmed the consolidated
mail scenario (and its restart variant) would fail on the pre-fix
issue #69 implementation by temporarily reverting the fix and observing
both fail, then restoring it. Future Link vertical slices extend this
file before being considered complete.
