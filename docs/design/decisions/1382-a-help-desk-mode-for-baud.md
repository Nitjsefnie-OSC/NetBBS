# Issue #1382 — a help-desk mode for Baud's model conversation — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The optional model conversation was a general chat companion with a persona
prompt. With the small local model the design prefers (#1208 Decision 7),
open chat is weak. Answering "how do I..." about this node is a job a small
model does well when it is given the right text, and one a BBS genuinely
has: new callers get lost in menus and rarely read handbooks.

What existed: the model settings are one node-wide JSON value, read strictly
(`ModelSettings(**json.loads(raw))`), so a field saved by a newer wheel makes
an older one raise. The model sees a system message (persona plus "Your chat
identity is ..."), at most N lines of the caller's own exchange, and the
prompt. The ledger reserves input as UTF-8 bytes, about four times the real
token count, so the whole caller handbook (57 KB) would reserve about 57,000
"tokens" against the default day budget of 100,000. The handbook does not ship
in the wheel, and no code reads it at runtime. The provider call is a single
non-streaming completion; tools are not used. The disclosure says exactly
what is sent, and a changed revision shows it again.

**Decision 1 — two modes, help desk and companion; help desk is the
default.**
- `ModelSettings` gains `mode`: `helpdesk` or `companion`, node-wide like the
  rest, each with its own shipped default persona. Help desk adds the
  grounding below; companion behaves as the model conversation did before.
- Changing the mode bumps the revision, so callers see the disclosure again.
- **A tolerant settings loader** ships with it: unknown keys are ignored and
  missing ones take their defaults, so a later field never again breaks an
  older reader. An absent `mode` reads as help desk, the default.
- Rejected: **help desk replaces companion:** SysOps who want open chat lose
  it. Rejected: **persona only, with a grounding switch:** the default
  persona and the grounding could contradict each other.

**What help desk sends** (amending #1208 Decision 7, "What the model sees").
- **Handbook excerpts:** the caller handbook, stripped of its markup and
  split at its headings, ships as package data generated from
  `docs/NetBBS-User-Handbook.md` by a script; a test fails when the copy is
  stale. For each request a standard-library keyword score picks the two or
  three best sections under a fixed byte cap of a few kilobytes. No
  embeddings and no new dependency.
- **Node facts:** the node's name and tagline, its SysOps, which optional
  features this node has (MRC, browser links, doors, FTN), and the boards and
  file areas of Decision 4. With the features listed, the model can say "this
  node has no MRC" instead of repeating a handbook section that does not
  apply.
- The grounding goes in the system message and is not part of the N lines
  of context. Context keying, the 30-minute expiry and "ordinary chat never
  enters" are unchanged.
- **The disclosure names it:** "Sends your addressed text, up to N prior
  lines of only your exchange here, excerpts of the node handbook and node
  facts, and, when you ask privately, the boards and file areas you can
  see." The last part is what Decision 4 adds for a private answer.
- **The ledger needs no new mechanism:** reservations count every message
  byte, so grounding is charged automatically, and the fixed cap keeps it
  predictable. The SysOp help for the token budgets says so.

**Decision 2 — the host supplies the facts; the model calls no tools.** When
the bot holds the `stats` grant, the host adds its answer to the grounding by
calling `run_query` with the pending caller, the same query path and caller
check a bot's `query` action uses. No `tools` field is sent, and each answer
is one completion and one ledger row. `seen <name>` stays a plain command.
- Rejected: **one round of OpenAI tools:** small local models are unreliable
  at tool calling, counting two completions as one request needs a migration,
  and it doubles latency inside the 40-second timeout. Rejected: **no queries
  at all:** "is anyone online?" would go unanswered.

**Decision 3 — the host adds the source and a "may be wrong" hint.** The host
knows which sections it sent. It appends the heading of the best-matching one
and a short hint to the answer, within the reply cap:
`(Handbook: Exchange files. May be wrong; ? for help)`. The prompt tells the
model to use only menu paths found in the excerpts and otherwise to say it
does not know and point to `[?] Help`.
- Rejected: **a disclaimer the model writes:** small models drop
  instructions and invent paths. Rejected: **a one-time notice:** a caller
  who missed it is never reminded.

**Decision 4 — public answers name only public resources; a local caller's
private answer may name what that caller can see.**
- A public answer is seen by the whole channel, and the facts go to the
  provider, which may be a cloud service. So a public answer's grounding
  names only boards and file areas that `public_sources` allows: readable by
  everyone, with no age or name gate, including what a Community passes down.
- A private answer (#1372) to a local caller, asked with `/msg Baud.bot` or
  in a `/private Baud.bot` conversation, may name the boards and areas that
  caller can see (`visible_boards`, `visible_areas`). Link callers always get
  the public set: there is no local account to scope them by. Hidden
  Communities stay out for non-SysOps.
- What a private answer named stays private: private and public exchanges
  keep separate context (#1372), so a members-only board named privately
  cannot resurface through the same caller's next public answer.
- Rejected: **the public set always:** a member asking privately would never
  hear of a members-only board. Rejected: **no resource list:** "which board
  is for retro hardware?" could not be answered.

**Persona** (amending #1208 Decision 7). NetBBS ships a default system prompt
for each mode, and the SysOp can edit it.

**Implementation notes.** No schema migration. A wheel from before the
tolerant loader still fails on a saved `mode`, so the release notes give the
rollback step (remove the key, or roll back from a backup). A short fixed set
of evaluation prompts is run against a local model before release, to check
for invented menu paths.
