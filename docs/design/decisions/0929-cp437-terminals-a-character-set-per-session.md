# Issue #929 — CP437 terminals: a character set per session — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

NetBBS sent UTF-8 to everyone, with a Unicode or ASCII choice of decoration.
Classic BBS terminals such as SyncTERM read bytes as CP437, so they showed every
non-ASCII character as two or three characters of noise and never saw ANSI art
as drawn. Normative description: §3.2, "Character set per session".

**Decision 1 — map at `Session.write`, with a curated table.** Every text write
already goes through `Session.write`, so one mapping covers every screen,
caller-written text, preset names and copy with `—` or `…` alike. The table
gives NetBBS's own glyphs deliberate CP437 substitutes. Rejected: a glyph set
passed to every renderer in place of the Unicode-style flag. That flag reaches
975 call sites in 29 files, and even then caller text and ordinary copy would
still carry characters CP437 lacks, so it would need the central mapping as a
safety net anyway.

**Decision 2 — the preference is Auto, Unicode, CP437 or ASCII.** Auto is the
default, and an explicit choice beats detection, so one caller can use SyncTERM
at home and PuTTY at work. The old style preference migrates: off becomes
ASCII, on becomes Auto. Rejected: keeping a separate style flag beside the
character set, which would allow meaningless pairs such as ASCII with Unicode
decoration.

**Decision 3 — an undetected Telnet caller gets ASCII before sign-in, including
the SysOp's banner.** CP437 would garble a UTF-8 terminal and UTF-8 garbles a
CP437 one; ASCII is readable on both. After login the "which line looks right?"
question settles it. Rejected: guessing UTF-8 (the pre-#929 behaviour, which
gave SyncTERM callers noise on their first screen) and guessing CP437.

**Decision 4 — ASCII is true 7-bit.** Before this, turning Unicode off only
swapped decorative glyphs; em dashes, ellipses, arrows and accented letters in
posts still arrived as UTF-8. Now nothing above 0x7F reaches an ASCII session.

**Decision 5 — substitutes keep the display width.** Width is measured once, on
the Unicode text, and every substitute fills exactly the cells its original
did. That keeps every layout rule in §3.2 and §3.6 valid unchanged.
`truncate_to_width` asks for the set's own ellipsis instead of mapping `…` to
`...`, which would be wider. Rejected: re-measuring after mapping, which would
make every width-aware screen depend on the caller's character set.

**Decision 6 — `ansi` means CP437.** That is the PC-ANSI convention, and it is
what older SyncTERM versions reported; the question after login is the safety
net for a UTF-8 terminal that says `ansi`.

**Scope.** CP437 is the only code page; SyncTERM's other fonts (CP850, CP866 and
others) are not detected or served, though the layer can take another codec
later. The CTerm device-attributes answer (`CSI = 67;84;101;114;109;… c`) would
also identify SyncTERM on any transport, but is not used until TTYPE proves too
weak. SysOp art storage with SAUCE, art with live slots, hand-drawn menu items
and animation pacing are later steps of #929 and build on this layer.
