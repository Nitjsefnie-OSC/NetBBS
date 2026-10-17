# Issue #711 — an art post's layout on the Link

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A post drawn in the ANSI art editor keeps its lines: each line stays a line, and
only a line wider than the terminal wraps (§6.1). Which layout a post has is set
by the editor that wrote it, not by an author-managed flag. For a carried post
to keep its lines everywhere, and not only on its origin, the layout has to
travel with it. Normative description: §6.1 and §9.2.

**Decision 1 — an optional `layout` field on `board_post`.** `"layout": "art"`
for an art post. The key is omitted for prose, never `null`, per §7.2's omission
rule. No `netbbs_protocol` bump: §7.5 allows optional fields that old peers can
safely preserve, and they do. A node that predates the field verifies the
signature over the bytes as received, keeps the envelope and relays it
unchanged, and shows the post as prose, which is the intended fallback. A value
this node does not know is prose too, so a later layout degrades the same way.
Rejected:
- a marker inside the body, which every older node would display;
- a new event type, which older nodes would store opaquely and never show at
  all (§7.5).

**Decision 2 — the root carries it; edits follow.** An edit is written in the
editor that drew the post, so `board_post_edit` and the moderator edit carry no
layout: a revision takes its root's. The local `posts.layout` column is
meaningful on the root row. Rejected: a layout per revision, which would let one
edit turn a drawing into reflowed prose with nothing to say why.
