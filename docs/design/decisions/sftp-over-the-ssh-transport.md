# SFTP over the SSH transport — declined

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Listed as a possible follow-on while issue #475 was open, on the reasoning that
SSH callers already have an authenticated connection and SFTP would ride it.
Declined; this is the decision, not a deferral.

**The motivating problem is gone.** #475 existed because most callers could not
transfer at all — Zmodem needs an emulator that implements it, and PuTTY,
Windows Terminal, an ordinary OpenSSH client and this project's own browser
terminal do not. The session-bound HTTP path (§6.2) answers that for every
transport. SFTP would not reach anyone who is currently stuck.

**What it would add is bulk and scripted transfer**, and the cost of that is a
second enforcement path for every gate the file screen applies: `min_read_level`
/`min_write_level`, `min_age`, `name_requirement`, Community inheritance,
moderation state, `get_max_upload_bytes`, and the rule that a pending upload is
visible only to its uploader and to moderators. Writes carry nearly all of it,
and would additionally have to route through `upload_file_from_temp` so that
content-addressed storage, `FILE_ID.DIZ` reading (issue #463) and Link
descriptor queueing (issue #464) behave exactly as they do elsewhere. A file
area is not a directory tree; presenting it as one means re-deriving each of
those rules in a filesystem vocabulary that cannot express them, which is how
the two surfaces drift apart.

**And bulk seeding does not want a live network surface anyway.** The realistic
case is a SysOp arriving from other BBS software with an existing collection to
bring across — a migration, run once, against a node that is not serving it yet.
That is a local job on the machine holding the files, where the work is reading
someone else's catalogue format and converting it, not moving bytes over a
protocol. It belongs in a standalone CLI tool alongside `python -m netbbs.admin`,
not in the SSH listener. Recorded as issue #505.

Revisit only if a concrete caller-facing need appears that HTTP transfer cannot
serve — not because SFTP would be convenient to have.
