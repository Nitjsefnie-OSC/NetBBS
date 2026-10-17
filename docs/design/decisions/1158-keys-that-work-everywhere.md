# Issue #1158 — keys that work everywhere — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Every new setting meant hunting for a free letter: Profile had `j` and `z` left
when #1156 needed one. Labels bracketed a letter mid-word where the first was
taken (`Bl[o]cked`), and the same function sat on different keys on different
screens (`[N]ext`/`[P]rev` in a picker, `[O]lder`/`[N]ewer` in a post list,
where `P` meant `[P]ost`). Tracker #1158 settles it in three steps: reserved
keys (this decision), the first-letter rule for menus, and two-digit numbered
fields on settings screens. Normative description: §3.5, "Keys that work
everywhere".

**Decision 1 — reserved keys are punctuation plus `B`.** Paging is `<` `>`
with PgUp/PgDn and ←→, find is `/`, help is `?` with F1 and Ctrl-H, back is
`[B]` or Esc, and Enter chooses. Rejected: `N`/`P` reserved everywhere, which
moves New scan, Profile and Post. Rejected: `N`/`P` reserved only on screens
that page; nearly every busy screen pages, and a hybrid such as the post list
makes "is this a list?" a judgement call for callers and for the enforcing
test.

**Decision 2 — ←→ step a value on forms.** On a draft editor ←→ already step
the highlighted field's value, and they keep doing so; the form's sections
page with `<` `>` and PgUp/PgDn. Everywhere else ←→ page. Rejected: ←→ paging
on forms too, which loses the quickest way to change a value.

**Decision 3 — Esc drops a highlight first.** With a row or field
highlighted, Esc clears the highlight as before; with none it goes back like
`[B]`. Rejected: Esc always back, which would leave the screen for a caller
who only meant to clear the highlight.

**Decision 4 — every hotkey screen has help.** `?`, F1 and Ctrl-H open the
same help, and screens without any get some written in this step, so the keys
never answer with a bell. F1 is decoded on Telnet, SSH and the browser
terminal. Rejected: the keys only where help already exists.

**Decision 5 — no global quit, and doors keep their own keys.** Log off stays
`[L]ogoff` on the main menu; a key that ends the session from anywhere is too
easy to hit in the middle of a form. Bundled doors keep their own action bars
under their presentation contracts.

**Decision 6 — callers are told once.** After the first login on the release
that changes a key callers use, a short screen says that paging moved to `<`
`>` and which menu keys were renamed (Decision 7 ships in the same release),
dismissed with any key. SysOps see it on the console too. Only an account
that knew the old keys sees it: the migration that ships with the change marks
every account existing then (`keys_notice_1158 = pending`), and the screen
(`netbbs.net.keys_notice`) is shown at that account's next login, after the
previous-callers screen, then marked seen. An account made later has no mark,
and a guest, signed in to a shared account, is never shown it. Rejected:
counting logins, which would also show it to an account made after the
upgrade on its second login.

**Decision 7 — a menu key is its label's very first letter.** Step 2 of the
tracker. Every letter key on a menu, an action bar or a one-key question
starts its label. A collision is resolved by rewording one label with a
synonym, and a renamed key is a clean switch with no hidden alias for the
old one. Rejected: allowing the first letter of a later word (`Reply [a]ll`,
`Change [N]ame`), which reads naturally but leaves the eye hunting for the
key inside the label.

- **Toggles show their state.** `[F]ollow: off` / `[F]ollow: on`,
  `[K]ept: no` / `[K]ept: yes`. The key and the label's start never
  change with the state. Where the highlighted row already shows the state
  (the mail list's `*` mark and `new`), the toggle keeps one label,
  `[M]ark` and `[U]nread`, so the bar still fits 40x12. Rejected: the key followed by the action
  (`[F] Unfollow`), which sets the key apart from its label, and separate
  verbs (`[F]ollow` / `[U]nfollow`), which move the key with the state and
  collide with other `U` keys.
- **The main menu.** `C[o]mmunities` becomes `[T]opics` (its own description
  already called them topic spaces; the screens keep the word Communities),
  `S[t]aff list` becomes `[O]perators`, `P[r]evious callers` becomes
  `[R]ecent callers` and `Moder[a]tion` becomes `[A]pprovals`.
- **Settings and field screens are left to step 3**, which numbers them
  (Decision 8), so their fields are not reworded twice.
- **Row numbers are two digits on every list**, as decided for the tracker:
  a board's posts, the mailbox and a file area took one digit and now read
  `01`-`99` like the picker (two digits, or one and Enter).

**Decision 8 — a field is chosen by its number, straight through.** Step 3 of
the tracker. Every draft field editor draws a two-digit number in front of each
field and reads it like a list's row number; ↑↓ and Enter still work, and a
field has no letter. The numbers run through the whole screen instead of
restarting in each section, so a number names one field on every page, a
SysOp helping a caller can say "Profile, 13", and typing it from another page
turns there. A resource's own screen (issue #1081) numbers its fields too:
numbers cannot collide with its action letters, which is what kept letters
off its fields. Rejected: restarting at `01` in each section, which keeps the
numbers small but makes `03` a different field on every page; keeping
resource screens on the cursor alone, which leaves the one field list
without numbers; and numbers only where letters ran out, which keeps two
schemes.
