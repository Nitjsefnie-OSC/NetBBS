# Issue #899 — the speaker label on a chat line — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A chat line from a linked node read `<Phase4Ops@OutBound · outbound.netbbs.org>`:
fine once, a lot of width on every line of a conversation, and one run of one
color that did not show where the user ended and the node began. Normative
description: §4.4 and §6.3.

**Decision 1 — the friendly name alone, qualified only when shared.** The DNS
name stays on the screens that have room for it. On a chat line the friendly
name is the only thing marking where a caller comes from, which #843 Decision 3
relies on, so a name another known node or this BBS also uses keeps its
qualifier, in the form the node map already used. The existing caution for an
undismissed cryptographic-identity observation stays in front of the line.
"The same name" includes one that reads the same (issue #900).

**Decision 2 — `Name · abc123` replaces `Name abc123`.** The node map's form
without a DNS name could not be typed back; with the reserved `·` it can, and it
matches the DNS-qualified form. Rejected: a fingerprint prefix alone, which
drops the name a reader knows the node by.

**Decision 3 — style the parts, not the whole.** Brackets and `@` muted, the user
in the speaker color, the node in `NODE_COLOR`. The same split fixes the
alias label: it read `Quill|lena_h` with the username at the alias's weight,
so readers could not tell which was the alias and the alias no longer read as
the chosen name. The alias now leads in its color with the username muted.

**Decision 4 — `alias (username)`, not `alias|username`.** Color alone did not
settle it: a pipe has no settled meaning, and a reader could not tell whether
it joined two names, separated them, or which side was the account.
"Name (handle)" already reads as a name and the account behind it, needs no
fallback on a terminal without UTF-8, and keeps #843's rule that no alias
stands alone. Aliases may no longer contain parentheses, so none can carry a
forged second account, and one set before that is no longer shown: with the
account's own marker inside it, the real username beside it no longer keeps it
honest. The chat status bar, which showed `username (alias)`, now reads
`alias (username)` like the stream, and `/names` marks MRC callers `(on MRC)`
as `/who` does. The verified-name unit keeps its own `(=...=)`
marker. Rejected: `aka`, English and longer on every line; an arrow, which has
no settled meaning and could read as "talking to".

Rejected for Decision 3:
restricting node friendly names to the alias character set, which would make
this node refuse the hello of any existing peer whose name uses one, and would
undo #807's quoting of names containing `@`.
