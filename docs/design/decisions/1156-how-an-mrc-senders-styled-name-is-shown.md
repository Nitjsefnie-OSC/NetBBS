# Issue #1156 — how an MRC sender's styled name is shown — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Since #1152 a decorated sender handle (`+Nick+[CASTLE BBS]`, `^Nick<tag>`) is
peeled off an MRC line. The maintainer asked for a per-caller choice instead
of one fixed rendering. The #298 body convention describes the peeling; this
records how a peeled handle is shown.

**Decision — three styles, combined by default.** A Profile setting
(`mrc_names`): *combined* puts the handle's styled name in place of the plain
one in the `<nick@site>` label and shows the rest of the handle as a tag in
round brackets inside it, `<+Nick+@Castle_BBS (CASTLE BBS)>`; *both* shows the
plain label and then the handle as sent (v7.17.0); *label only* shows the label
alone (v7.17.1). It applies to room lines, live and on replay, and to MRC
private and broadcast lines.

- **The site is never taken from the handle.** The label keeps the packet's
  `from_site`; only the name part changes.
- **The label's own characters stay out of it.** The name part is the sender's
  name plus the ASCII marks `+^!#$%&-_.` directly against it. Everything else
  in the handle becomes the tag, with `( ) [ ] { } < > @ | ~ * =` removed,
  punctuation trimmed from its ends, and a cap of 24 columns. These are the
  characters #916 keeps out of local aliases, for the same reason: inside
  `<…@…>` they make a label ambiguous.
- **The handle is stored, the choice is not.** `channel_messages.mrc_handle`
  keeps the raw handle, colour codes included, only for a decorated one, so a
  change of setting re-renders scrollback. Older lines have none and look the
  same in every style. Nothing about it travels over Link.

Rejected: the handle as sent inside the label
(`<+Nick+[CASTLE BBS]@Castle_BBS>`), and the alias shape
`<+Nick+[CASTLE BBS] (Nick)@Castle_BBS>`, because both put user-chosen
brackets inside NetBBS's own label.
