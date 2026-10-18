# Issue #1378 — door events as a bot announcement source — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The bundled games gained achievements, ranks and seasons, but nobody outside
a door ever learned of them. On a BBS, part of the point of a door game was
the shared standing: the bulletin of top players and the boast in chat.

What existed: door API 4 already lets a door speak in chat with no bot. A door
whose outbound hook a SysOp switched on drops `{"channel": ..., "body": ...}`
into its outbound directory, and NetBBS records the line under the door's
label (`Voidrunner.door`), delivers it live and queues it for Link (§15, Phase
7). That path gives no structure, so the host cannot tell that a line names a
caller and cannot honour an opt-out, and nothing exists for a `!top` query to
read. It also has no audience gate: `allow_channel` refuses only MRC-bridged
channels, so a door with a play level above 0 can speak in a channel everyone
reads (filed as its own bug). None of the bundled games uses it yet.

On the bot side, announcements come from `bot_source_events` (kinds `post`
and `file`, references only, bounded to 1,024 rows), gated by `_public` so
only what every caller may read is announced (#1208, "Announcement
audiences"), with per-source grants and a switch per ability, and with text
the host composes: the bot cannot replace the text or the channel.

**Decision 1 — structured door events, announced through a bot.**
- **Door API 5:** a door may write an event request,
  `{"event": {"type": "rank", "user_id": 12, "title": "Merchant Captain"}}`,
  through the outbound file drop. `door_info.json` advertises
  `outbound.events` when the SysOp has switched events on for the door. Each
  contract addition bumps the version; this one makes it 5.
- **The door supplies only** an event type, a subject `user_id` and a short
  title (at most 80 characters). The host composes the chat text, resolving
  the handle from the `user_id` when it publishes, so a rename shows
  correctly; a disabled, deleted or pending account is not announced.
- **A bounded journal:** the host stores each accepted event in a bounded
  table, like `bot_source_events`, and offers it to bots granted that door.
- **A third source kind:** `door`, beside `post` and `file`, with its own
  per-bot grants and its own ability switch ("Baud door announcements"). The
  32-source cap counts doors too. The editor's door picker offers public
  doors only.
- **The door switch:** reporting events is a per-door SysOp switch, off by
  default, on the door's `[O]utbound` screen, separate from posting, with its
  own hourly ceiling, as chat and board posts already have separate ones.
- **Audience gate:** a door's events are announced only while its
  `min_play_level` is 0, the only gate a door has, mirroring `_public`. A
  grant is refused otherwise, and the gate is rechecked at publication, as
  `source_event` rechecks a board.
- **Rechecked at publication:** the grant, the door gate, the subject's
  account state and consent (Decision 2), the destination and its Linked
  approval. Linked destinations follow the bot rules (#1208 Decision 3): a
  per-channel Linked approval, never an MRC-bridged channel, and `/mute`.
- **Reach:** native and VM doors, the same reach as the outbound hook. DOS
  doors follow the hook when it reaches them; remote doors never can, since
  NetBBS runs no process for them and shares no files.
- **Guests and rehearsals emit nothing:** a guest call on a bundled door is
  a rehearsal, and a rehearsal's event request is answered with what would
  have happened.
- **Only live events.** A door emits only what was earned in the session,
  never catch-up unlocks: Voidrunner unlocks an existing career's past
  accolades the first time it opens with a new version, and those must not
  become a burst of announcements. Bot startup already skips history.
- **Door API 4 chat lines stay** as they are, for a door's own narration.
- Rejected: **door API 4 chat lines with an optional subject field:** no
  `!top`, every door words its lines differently, and a door could still name
  a caller in free text. Rejected: **both paths as equals:** two voices for
  one game.

**Decision 2 — a caller can opt out: "Game results announced in chat",
default on.**
- A new profile switch beside "Name shown to other callers", opt-out like
  that field: achievements are game standing and add no personal content.
  ```
   Name shown to other callers ........ yes
   Game results announced in chat ..... yes
     Whether Baud may announce your ranks, achievements
     and high scores from door games, and list you in !top.
  ```
- Off drops the caller's events entirely and hides them from `!top`.
- Consent is checked per subject when the event is published, so an event
  naming several callers (a season close) checks each one and names only
  those who allow it.
- Rejected: **reusing "Name shown to other callers":** hiding from the
  callers roll would also silence announcements, and the reverse. Rejected:
  **opt-in:** the feature would stay silent on most nodes.

**Decision 3 — milestones and top-tier achievements.**
The bundled games emit, each from its own repository and bumped here with
`scripts/vendor_door.py`:
- **Voidrunner:** rank promotions (the five ranks), and accolades of tier 2
  and above (for example "Untouched" and "Two for One").
- **War Dialer:** rank tiers (the six), season medals (the top three at a
  season's close), and exchange captures, which the game already publishes
  in its own public scene feed.
- **Retro Trivia:** a perfect round, its only possible event without new
  storage in that game.

Captures can come in bursts during a contested season. They are bounded by
the door's own hourly events ceiling and by the bot's hourly output in the
channel; no capture-specific limit is added.
- Rejected: **milestones only:** a quieter channel, but the top accolades and
  captures are what players talk about. Rejected: **everything** (all 66
  accolades and every capture): announcements would stop meaning much.

**Decision 4 — `!top <door>` reads a standings snapshot the door writes.**
- A door may write `{"standings": {"title": "Hall of Fame", "rows": [...]}}`
  with at most 10 rows of `user_id`, a value and an optional label. Each
  snapshot replaces the last, and the host keeps one per door. The game stays
  the authority on its own scoring, seasons included.
- **A granted node query, `top`,** like `stats`, with its own grant. The host
  applies the door gate and each row's opt-out.
- **The answer is a titled block** (#1373):
  ```
  ── [BOT] Baud.bot · Voidrunner · Hall of Fame ── 20:15 ──
   1  Vex      182,400 cr   Legend of the Frontier
   2  Nib       96,150 cr   Void Baron
   3  Quill     40,020 cr   Merchant Captain
  ```
- Retro Trivia has no list until it keeps scores.
- Rejected: **a list the node derives from events:** it misses everything
  before the feature, ignores War Dialer's season resets, and makes NetBBS
  learn game semantics. Rejected: **reading the games' own files:** works for
  the bundled games only and couples NetBBS to formats other repositories
  own. Rejected: **deferring `!top`.**

**Implementation notes.**
- Migrations: the door events journal, the per-door events switch and
  ceiling, `bot_door_sources` and the ability column, the standings
  snapshot, and either a rebuild of `bot_source_events` for `kind = 'door'`
  or a separate cursor for door events. The caller switch lives in user
  preferences and needs none. Rollback across a table rebuild is "restore".
- The event type names (rank, achievement, medal, capture, perfect round)
  and the host's sentence for each are fixed in the implementation and
  documented in the door guide; a door cannot add a type.
- A door can claim any `user_id`. A door is a trusted program, as the
  outbound hook already says, so this is attribution, not security: the
  receipt and the stored row name the door.
