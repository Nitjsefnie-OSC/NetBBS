# Issue #78 — decompose LinkNode protocol state — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The engineering record's "LinkNode internal state organization" entry
(§9) is the design pass this issue asked for: which of `LinkNode`'s
eleven flat fields belong together (`PeerDirectory`, `BoardEventState`,
`BoardLifecycleState`, `RelayState`), and which stay directly on the
façade (`identity`, `known_event_ids`, `events`, as the shared
substrate every object type uses). Every external consumer
(`netbbs.link.store`/`.sync`/`.transport`/`.relay_selection`,
`netbbs.net.admin_flow`, and their tests) still reads the old flat
attribute names unchanged, via backward-compatible properties over the
same live dicts -- zero test changes, zero wire/serialized-shape
changes. A future state family (inventory/pull, linked-channel
lifecycle) should follow the same shape: its own small dataclass with
narrow methods, not a further flat field.
