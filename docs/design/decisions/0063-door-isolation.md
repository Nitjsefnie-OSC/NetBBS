# Issue #63 — door isolation — superseded by the shipped door model

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Issue #172 established the native door model; #296/#297 added legacy and browser
adapters. Phase 7 above records the current contract. Native doors are trusted,
same-user programs with resource limits and supervised cleanup, not filesystem
or network containment. Door metadata is API version 4; companion services and
the SysOp-enabled outbound board-posting and chat hook are implemented.

The [developer handbook](../../NetBBS-Developer-Handbook.md#developing-a-native-door)
is the integration entry point. The outbound hook posts as a distinct door label,
with an explicit per-door board allowlist and rate ceiling. A linked destination
can federate the post through the live node's identity. There is no generic
session-capability API for chat, mail, real-time game moves, or federated scores;
those extensions need their own protocol and authority decisions.
