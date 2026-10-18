# Issue #1208 — chat bots — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The issue scoped chat bots: something that gives a node personality without
compromising Link's openness or its callers' privacy. The maintainer decided
the design on 2026-10-10 and 2026-10-11; implementing it is separate work.

**Implementation boundary (#1210).** Bot identity recognition, the reserved
suffix, live/replay marking, remote roster marking and local channel policy
are implemented. `/bots` shows the policy; `/bots all`, `/bots local` and
`/bots none` change it with channel moderation permission and an audit entry.
The receiving policy starts at *all bots*; this grants no process, event,
query or publishing permission. It filters live display, history, search
and remote rosters without deleting retained messages or signed events.
MRC mappings, including paused mappings, always refuse bots. The local
runtime now supervises BOT API v1 programs, with addressed-only grants,
per-channel output limits, mutes, local bot presence/counts and persistent
stop controls. Baud provides help and public channel reminders through that
same API. Instances are off by default and configured in Content > Chat bots;
first-run onboarding offers to create Baud, defaulting to no (issue #1383).
Linked participation requires a per-instance, per-channel approval confirmed
by the SysOp on Save. Approved bots publish ordinary signed/live channel
messages and advertise separate channel-scoped presence. The optional
`chat_bots_v1` endpoint capability enables bounded `bot_request`, `bot_result`
and `bot_presence` frames without changing mandatory realtime version 4.
Requests travel directly from the caller's authenticated home to the selected
bot host; ordinary channel frames and replay never dispatch them. Only a
channel origin may relay another node's bot presence. Remote `/mute` is a
local receiving filter, including history/search, keyed by bot label and home
fingerprint. Remote caller ids are distinct from local account ids and from
identically named callers on other nodes. A remote request's public reply
also goes directly to the caller's node, with ordinary live deduplication,
so an offline channel origin does not swallow that reply. Node backups now
capture bounded API-managed state files with checksums before the database
snapshot, and restore the matching generation, including absence. SysOps may
delete a stopped instance and its state after explicit confirmation; channel
history and existing backups remain. Separate, default-off statistics and
last-seen query grants now serve Baud and external programs through the same
host-bound request API. Statistics expose node name, uptime and online human
count. Last-seen exposes public online status or the latest recorded login or
logout on the bot's host, respecting hidden session history even for SysOp
requests because a reply may be public: private answers (#1372) do not
change what these queries return. No private activity is queried.
Whole-channel reading and channel-specific live join/leave grants are now
available separately. Baud's optional greetings require join events, not
conversation access. Channel headings, `/who` and live grant-change notices
disclose readers; `chat_bot_readers_v1` carries that disclosure over Link to
supporting peers. A local receiving mute cannot hide a remote reader's access.
Older peers retain ordinary bot traffic but lack the extended disclosure.
Channel history and presence snapshots never become fresh channel events.
Board/file events and optional Baud announcements now use explicit source
lists and one destination per instance. The initial audience proof accepts
only sources readable by everyone, including Community inheritance, so it
holds for local and Linked destinations without assuming peer admission rules.
Restricted mappings are refused. Only public metadata is delivered; source,
content, trust and destination are rechecked inside publication. Events are
bounded, best-effort notices of new approvals/arrivals while the program runs,
not replay on startup. Edits and fetching already-catalogued files do not
reannounce them. Optional model conversation is implemented behind separate
default-off node and instance switches. The host owns provider credentials,
initial disclosure, isolated temporary context/reset and persistent budget
accounting; programs may request it only using a live addressed-message token.
The optional `chat_bot_callers_v1` extension supplies the caller home node's
authenticated/guest classification and level solely for model admission. Older
peers retain ordinary bot commands but cannot assume authenticated model access.
See `NetBBS-bot-api.md` for the implemented protocol and limits.

**Grounding.** Door output already speaks in chat without being a person
(issue #520):
- it uses a label, `<Name>.door`, that is not an account, and the username
  validator reserves the suffix;
- `/mute` works on it;
- it is held to 30 lines an hour;
- it is refused in MRC-bridged channels, and a Linked channel needs the
  SysOp's confirmation first.

A peer's door line is recognized by the same suffix, both live
(`x.door@Node`) and as a stored line whose authenticated author is `x.door`
(`_is_door_line`). Bots reuse this model.

**Decision 1 (locked in): a house bot built on an open bot API.** NetBBS gets
a bot API, and the bot that ships with it uses that API like any other bot.
- **Rejected: a bot built into NetBBS only.** Every new ability would need a
  release, and SysOps could not add their own.
- **Rejected: one bot for the whole network.** It needs a central host on a
  mesh that has none, and that host's operator would see every node's chat.
  MRC operators also frown on bots.

**Decision 2 (locked in): a bot is never taken for a person.**
- **Name.** It speaks as `<Name>.bot`, a label that is not an account, and
  the suffix is reserved as `.door` is.
- **Marking.** It carries a `[BOT]` badge, has its own entry in `/who` and
  `/names`, and the status line counts it apart from people
  (`4 online (1 bot)`). Scrollback records it as a bot, so replay marks it
  too.
- **Control.** Moderators can `/mute` it. The SysOp has a kill switch for
  each bot and one for all of them. NetBBS enforces a per-channel rate limit
  that a bot cannot opt out of.
- **Peers.** A peer's bot line is recognized by its suffix, the way a door
  line is. A person on an older peer whose name ends in `.bot` would be shown
  as a bot; the same edge already exists for `.door`, and it is accepted.

**Decision 3 (locked in): where a bot may speak.**
- **Local channels:** the default.
- **Linked channels:** only after the SysOp confirms it for that channel.
- **MRC-bridged channels:** refused. Hub rules, and the rule never to send
  the hub a request shape it does not document, make bot traffic there
  unsafe.
- **A per-channel bot setting:** *all bots*, *only this node's own*, or
  *none*. The SysOp or the channel's moderators set it. In a Linked channel
  each node decides what it shows, so no SysOp is made to carry another
  node's bot.

**Decision 4 (locked in): the same rules for every bot.** Scopes (Decision 5)
are granted by the SysOp. They never depend on where a bot's code came from.
The bot that ships with NetBBS gets no privilege that others lack. What
protects callers is consent and transparency, and a code privilege would
add nothing: NetBBS is open source, and a SysOp can change its chat code.

The SysOp bot editor selects channels, board sources and file-area sources
from searchable lists with selection markers; it does not require typing
resource names. Channel-specific grants offer the draft's allowed channels,
Linked approvals offer its Linked channels, and sources offer only resources
readable by everyone. The announcement destination is a single-choice picker
with an explicit none option. Toggling a list updates only the draft; Back
returns to the editor and Save applies it with the existing access approvals.
Removing an allowed channel clears its dependent grants and destination in
the draft. Existing unavailable grants stay visible for removal. Names,
program arguments, provider details and numeric budgets remain editable text.

**Decision 5 (locked in): the bot API.**
- **Process.** A bot is an external program that NetBBS starts and
  supervises, like a native door. A crash restarts it with backoff and never
  takes the node down.
- **Protocol.** JSON lines over stdin and stdout, versioned as
  `BOT_API_VERSION`.
  - NetBBS sends events: a line, a join or a leave, a new post or file in an
    area the bot watches, and a timer tick.
  - The bot sends actions: say a line in a channel, reply to a user
    publicly or privately, read or write its own state, and make
    specifically permitted node queries. The current set of actions is in
    §6.3, "Chat bots".
  - The door hook's file drop is rejected for this: a bot has to receive
    events as they happen.
- **Scopes** are explicit and additive. The SysOp grants them per bot and
  per source channel, board or file area as applicable:
  - `addressed`, the default: the bot receives only messages directed to it,
    through explicit naming or the channel's default-bot commands below;
  - `channel`: everything said in the channel;
  - `events`: posts, files, door events, joins and leaves from specifically
    approved sources. Greetings require join events from the approved
    channel; announcements require events from approved boards, file areas
    or doors (§16, issue #1378).

  A grant of `channel` comes with a warning. While a bot holds it, the
  channel's heading and `/who` say that the bot reads the channel.
  Enabling an ability shows the SysOp the additional access it needs and
  requires the corresponding explicit grants.
- **Node queries** have specific permissions for statistics, `!seen` and
  door standings (`top`, §16, issue #1378), rather than general database
  access. `!seen` follows the requesting caller's visibility rules and
  reports when someone was last online, without exposing their
  private-channel activity.
- **Announcement audiences.** Initially, automatic announcements may carry
  information only from boards and file areas readable by every caller
  allowed into the destination channel, and from doors whose play level is
  0 (§16, issue #1378). NetBBS refuses a source-to-channel mapping if it
  cannot establish this; even a restricted post's title must not leak
  through an announcement.
- **Addressing.** Explicit names work for commands and conversation alike:
  `Baud.bot: help` or `Baud.bot: <message>`. Each channel may have one default
  bot, selected locally by each node. Short commands such as `!help` and
  `!seen` go only to that bot. Without a default, NetBBS explains how to
  address one explicitly. Ordinary chat never triggers the default bot.
  - A remote bot requires its node-qualified identity, such as
    `Baud.bot@Node: help`, resolved through existing Link identity rules.
    A command is never broadcast to every bot with the same name.
  - Only the caller's own node handles their shorthand commands; other
    nodes in a Linked channel do not dispatch that shorthand to their
    defaults.
  - Each eligible live human message selects at most one bot. Bot messages,
    announcements and replayed scrollback never become new requests;
    bots cannot automatically trigger other bots.
- **Memory.** Each bot has a store of its own under `<db>.bots/<name>/`,
  mirroring doors' `<db>.doors/<name>/`, and backups include it. Persistent
  state such as reminders is separate from the temporary language-model
  conversation context described below.

**Decision 6 (locked in): the house bot, `Baud`.**
- **Off by default.** The SysOp switches it on, chooses its channels and may
  rename it. First-run onboarding asks once whether to switch it on (default
  no) and, on yes, creates it addressed in one channel the SysOp picks, with
  greetings and their presence-event grant there (issue #1383). It is asked
  only while the node has no bot instance and has a channel to offer. A node can run several instances, each with its own name,
  channels, temperament, limits and memory.
- **Temperament.** A few presets (friendly, terse, retro) shape its fixed
  phrases: the greeting, the help opening, and the busy, reminder-limit and
  memory-full messages. They need no language model. The presets are starting points:
  the SysOp may override each phrase per instance (issue #1381), so a node's
  Baud can speak in its own words or another language. Overrides are
  validated on Save -- plain one-line text, `{name}` the only placeholder,
  and short enough that `<caller>: <phrase>` fits the 500-character line --
  and reach the bot in the `hello`'s `phrases`, next to `temperament`; an
  empty one falls back to the preset.
- **Abilities:**
  - `!help`, which also points to `/sysop` for paging the SysOp (§16, issue
    #1377);
  - `!seen`;
  - node statistics;
  - reminders, by duration or at an absolute time in the node clock, held
    for an owner who is not in the channel when they fall due (§16, issue
    #1375);
  - `!tell`, a note held for a local account's next join (§16, issue #1376);
  - `!roll` and `!coin`, always on, and per-channel quotes and quick polls,
    each switched on separately (§16, issue #1379);
  - greetings and announcements of new posts, files and door events, each
    switched on separately, and door standings with `!top` (§16, issue
    #1378).
- **Scope.** Baud starts with `addressed` alone; help and reminders work
  with that scope. Enabling greetings or announcements requires the
  corresponding source-specific `events` grants. Statistics and `!seen`
  require their specific node-query permissions. These abilities do not
  require access to ordinary channel conversation.

**Decision 7 (locked in): conversation through a language model is narrow
and off by default.**
- **Consent.** Only the SysOp's explicit choice turns it on, after being told
  what is sent where.
- **Provider.** Any endpoint that speaks the OpenAI-compatible API. A model on
  the SysOp's own machine (Ollama, llama.cpp) is offered first: no chat
  leaves the node and nothing costs money per request.
- **What the model sees.** Only the caller's message addressed to the bot,
  plus at most N lines of prior context; the SysOp sets N, and it may be 0.
  Context is separate for each authenticated caller, bot instance and
  channel, and for private and public exchanges (§16, issue #1372), and
  contains only that caller's addressed messages and the bot's delivered
  replies to them. Ordinary chat and other callers' exchanges
  never enter it. In help-desk mode, the default, the host also sends
  excerpts of the shipped caller handbook and public node facts, chosen per
  request under a fixed size (§16, issue #1382). A reply is visible in the
  channel unless the caller asked privately or the bot answered privately
  (§16, issue #1372); separate context alone does not make channel
  conversation private.
- **Context lifetime.** Context stays in memory only, expires after 30
  minutes of inactivity, and is cleared on restart. A caller has an explicit
  command to clear their current conversation context. The command explains
  that it does not delete channel scrollback or provider-held records.
- **Callers are told.** The first conversational request receives a notice
  identifying the provider, context policy and whether prompt logging is
  enabled. That request is neither forwarded nor retained as context; the
  caller submits again to proceed. Disclosure is repeated when any of those
  settings change.
- **Limits.** NetBBS enforces these on top of the provider's own:
  - a node budget per day and per month;
  - a per-user daily quota;
  - a per-user cooldown;
  - a per-channel rate;
  - a minimum level to use it, with guests excluded by default;
  - a cap on reply length.

  A limit that is hit is said in a reply, never shown as silence.
- **Accounting.**
  - **Tokens, always.** NetBBS counts the token usage each response reports.
  - **Money, optional.** It is worked out from prices the SysOp enters per
    million input tokens and per million output tokens. There is no standard
    API that reports prices.
  - **Local models** need token or rate limits only.
  - **The console** shows spending, users and limits hit.
- **Secrets and logs.**
  - The API key is stored as a secret, the way FTN passwords are.
  - Prompts are not logged unless the SysOp turns logging on, and callers are
    told when it is on.
- **Persona and mode.** The SysOp chooses help desk (the default) or
  companion; NetBBS ships a default system prompt for each, and the SysOp can
  edit it (§16, issue #1382).
