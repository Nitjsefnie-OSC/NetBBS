# Issue #165 — MRC gateway scoping — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** let NetBBS callers reach the existing cross-BBS MRC (Multi Relay
Chat) network without coupling NetBBS's own premium chat model — Noise
XX transport, Link-wide presence, per-message trust-filtered scrollback,
all closed by issue #164 — to a third-party network's pace or ceiling.
Sequenced after #164, which is done (shipped in v5.3.0): this scoping
pass was unblocked the moment that landed.

**Reference protocol, since no formal MRC spec exists:** verified
directly against ENiGMA½ BBS's own reference client/multiplexer
(`core/mrc.js`, `core/servers/chat/mrc_multiplexer.js`), the most
actively maintained modern implementation. MRC is a single central hub
(historically `mrc.bottomlessabyss.net`), not a federated or
peer-to-peer network — every participating BBS is one more client of the
same hub. The wire protocol is a single persistent TCP socket (TLS
optional, separate port), newline-delimited, tilde-separated 7-field
lines: `from_user~from_site~from_room~to_user~to_site~to_room~body~`.
`to_user`/`to_room` double as addressing and as an ad hoc control
channel (`to_user="SERVER"`/`"CLIENT"` carries heartbeat/roster/registration
commands like `IAMHERE`, `USERLIST`, `STATS`, `LOGOFF`, `INFOSYS`/`INFOWEB`/
etc.). The only "handshake" is one unauthenticated line the client sends
on connect — `{boardName}~{clientSoftware}/{os}/{version}` — no password,
token, or signature ties a connection, a user name, or a claimed board
name to anything real; any client can claim to be any board. User and
site name fields are constrained to ASCII 33–125, 30 chars, room names
to 20 (the protocol page's `string[20]`, issue #376), with Mystic `|NN`
pipe-color codes stripped; message bodies to ASCII 32–125. There is no
history or
backfill concept at all — a message reaches only whatever clients happen
to be connected at the instant it's sent.

This resolves several of the issue's own open questions directly, rather
than leaving them for a later pass: MRC has no identity a gateway could
verify, so nothing about it can ever feed Phase 4's trust/reputation
model, and nothing resembling NetBBS's own trusted-scrollback concept
has an MRC-side equivalent to bridge to.

**Existing precedent this reuses, not reinvents:** `netbbs.link.
realtime_channels.LiveChannelBridge` is the architectural template — one
instance per running node, holding no storage of its own, forwarding
everything through the same `netbbs.chat.hub.ChatHub.broadcast()` a
purely local participant's own message already goes through. Its own
`_handle_channel_message` already renders a remote-authored message with
`author_fingerprint=None` and a descriptive `author_label` (`f"{user_id}
@{fingerprint}"`) — the shape an MRC-authored message reuses, with one
storage rule added on review: `channel_messages.external_source` marks
such a row so it is never attested as this node's own content (§6.3;
trusted-scrollback snapshots skip it and Link queueing refuses to sign
it). The outbound hook
point already exists too: `netbbs.net.chat_flow`'s per-message send path
calls `link_context.realtime_bridge.broadcast_local_message_live(channel,
recorded_message)` immediately after `record_message`/`hub.broadcast` —
a sibling MRC bridge attaches at that identical call site, independent
of `link_context` and of whether Link itself is even configured.

**Decision 1 (locked in) — one in-process bridge per running node, no
separate daemon.** Unlike ENiGMA½'s own two-tier design (a per-connection
client process plus a separate local "multiplexer" process fanning
multiple local sessions into one hub connection), NetBBS is already a
single asyncio process per node — there is no per-connection-process
architecture here to multiplex in the first place. One `MrcBridge`
instance, analogous to `LiveChannelBridge`, owns the one outbound hub
socket for the whole node.

**Decision 2 (locked in, amended by issue #300) — a local channel is
bridged only per channel, explicitly, and off by default; an MRC room a
caller opens is a channel of its own, behind a second node-wide switch
that is also off by default.** MRC rooms are flat, global, and
unauthenticated with no ACL concept at all — "every local channel
bridges by default" would silently leak channel contents onto a public,
unauthenticated network the moment a SysOp enables MRC at all. A SysOp
must name which local channel maps to which MRC room, the same opt-in
shape every other Link-adjacent per-channel setting already uses. The
amendment (issue #300) keeps that rationale intact: an *open room* has
no local content to leak because it is born on the network — a caller
asks for a room by name and it materializes as a real channel named
`mrc:<room>` (design doc §6.3), so enabling MRC alone still bridges
nothing, and the SysOp decides separately whether callers may open
rooms at all.

**Decision 3 (locked in, amended by issue #305) — channels by default;
private messages only for a caller who asked for them.** The protocol
argument stands: MRC's `to_user` targeting carries no real
confidentiality (the hub, or any client willing to lie about its own
identity, can see or spoof it), so NetBBS must never present an MRC
private line as private. What the original decision got wrong is the
remedy: refusing them outright leaves a caller who reads "bob@Other
tried to message you privately" with no way to answer, while every
other client on the network delivers such lines. So the bridge delivers
and sends private lines for a caller who switched them on in their
Profile (off by default, one switch for both directions), tells them
once per session what "private" means on that network, and never
records a private line anywhere. A caller who left the switch off gets
exactly what they got before: one notice per sender, nothing shown.

**Decision 4 (locked in) — inbound content is always rendered as
external/untrusted, and never enters Phase 4 at all.** An inbound MRC
message becomes a `ChannelMessage` with `author_fingerprint=None` and an
`author_label` such as `f"{mrc_user}@{mrc_site} (MRC)"` — visually
distinct from both a local and a genuine Link-originated author label.
It is never passed to `decide_node_action`/quarantine/trust scoring:
there is no cryptographic identity on the MRC side to hang a trust
decision on, so inventing one would be theater, not protection.

**Decision 5 (locked in) — bounded failure containment, matching this
project's existing "bound remotely influenced resources" invariant.**
Reconnect-with-backoff on the one outbound socket (mirroring the retry
behavior every MRC implementation already converges on); a bounded
outbound send queue so a stalled or unreachable hub degrades to that one
bridge going quiet, never blocking local chat delivery or the caller who
just sent a message; malformed or oversized inbound lines are dropped
and logged, never allowed to crash the local channel. Every outbound
field is sanitized to MRC's own documented charset/length limits before
it's sent. Inbound Mystic `|NN` pipe codes are untrusted text, never
raw ANSI: identity fields lose all of them, a body keeps only the
color subset (`|00`-`|23`), and those become NetBBS's own SGR solely
through `netbbs.rendering.pipe_codes`, after sanitization and per
viewer (issue #298, below) — "sanitize before styling" holds exactly.

**Implemented as issue #275** (`netbbs.mrc`), with every decision above
as specified and the three questions this scoping pass left open
answered as follows. Two further choices were made at implementation
time, both verified against four independent MRC implementations
(ENiGMA½, Synchronet, uMRC, ANetBBS) since the hub operator's own spec
page is not publicly reachable:

- **Identity on the MRC side is one MRC user per caller** — the
  caller's canonical username sanitized to MRC's rules (spaces to
  underscores, printable ASCII, 30 characters, reserved routing names
  suffixed), presented at this node's site name, with `NEWROOM` on
  entering a bridged channel, `IAMHERE` every minute and `LOGOFF` on
  leaving. Every reference implementation multiplexes this way; a
  single relay identity prefixing each line with the handle was
  rejected because MRC users could not see who is present or address
  anyone. The announced set is derived from the `ChatHub` roster, so a
  channel mapped while callers are inside it, or a reconnect after a
  hub outage, re-announces exactly who is there. The join banner tells a
  caller their handle is about to appear on a public network before
  the announcement is sent.
- **Inbound room messages are recorded into the channel's scrollback**
  (`record_message`, `author_fingerprint=NULL`, label `user@site (MRC)`),
  bounded by the existing scrollback limit and search-indexed like a
  carried Link message, so a caller joining later sees a coherent
  conversation rather than only the local half; the hub's own echo of
  this node's traffic is dropped by site name. Presence chatter, room
  topics and server notices stay ephemeral. Private (`to_user`) lines
  are never delivered; the addressed caller gets one muted notice per
  sender.
- **Configuration is DB-backed and edited live**, not a TOML section:
  Settings › Inter-BBS chat (MRC) holds the node-wide hub settings
  (enabled, host, port, TLS on by default with certificate
  verification, site name, INFO fields) and saving asks the running
  bridge to reload and reconnect; a channel's own detail screen holds
  its `[M]RC room` mapping and a `[P]ause` that keeps the mapping while
  relaying nothing — the per-bridge disable asked for above; Node ›
  Inter-BBS chat (MRC), named as under Settings, shows link state, hub,
  last error, attempt and drop counters and every bridged channel with the hub's roster, with
  `[R]econnect now`. Inside a bridged channel, the status line carries
  an `[MRC]` badge, `/who` and `/names` list the room's MRC users, and
  `/mrc` shows room, hub, link state and roster.
- **Bounds as shipped:** reconnect backoff 1–60 s with jitter, reset
  after 30 s stable; outbound queue of 200 lines (oldest dropped) behind
  a node-wide 5 lines/s bucket and a 3-line burst / 1 line/s bucket per
  caller (the sender is told quietly when a line was not relayed), and
  on the way out packets from one nick are spaced at least 0.5 s apart
  -- the hub's own per-user limit (MRCDoc rev 1.26; issue #375) -- with
  held-back lines counted under the same 200-line cap; the spec's field
  limits are enforced where a caller or SysOp types (room names 20,
  refused rather than cut; topics 55; passwords 20, room passwords 32;
  LASTSEEN and HELP arguments 20), and the handshake names the client
  as `NETBBS/<Os.arch>/<NetBBS version>` (issue #376);
  inbound 40-line burst / 20 lines/s ahead of any database write, 4 KiB
  line cap -- except the hub's reply to one caller (`/BBSES`, `LIST`,
  `HELP`), shown to them alone and written nowhere, which pays that
  caller's own 300-line burst / 10 lines/s allowance instead and says so
  when it cuts (a `/BBSES` listing of every connected board stopped,
  silently, after 39 boards); a local line is split into at most three
  140-character wire chunks. An `OLDVERSION` rejection from the hub is fatal until a
  SysOp changes settings, since every retry would start a fresh
  rejected session. Bridge warnings land in the same bounded
  diagnostic log Link already uses.

**Issue #298 (body convention, colors, CTCP, per-caller hub replies)**
corrected one shipped assumption and added what falls out of it. Every
reference client embeds the sender's own colored handle *inside* the
message body (Mystic `|03<|11Alice|03>|16|07 text`, ENiGMA½
`|00|10<|02Alice|10>|00 |03text`, Synchronet `Alice |07text`, ANetBBS
`|07Alice|07 text`; actions `|15* |13Alice text`) and displays an
inbound body verbatim; the hub adds nothing. The bridge therefore
sends every chunk in a house style (`|08<|14nick|08>|16|07 text`, the
prefix paid for out of the same 140-character budget) and peels an
inbound prefix only when the embedded name equals `from_user` -- an
unmatched body is recorded whole, nothing is guessed. Issue #1152 added
handles no reference client writes but users choose, such as
`+Nick+[CASTLE BBS]` or `^Nick<tag>`. The spec makes a body's first word
the sender's handle, so a first word that names `from_user` is peeled too,
with only punctuation touching the name. The word runs to the first
whitespace outside brackets, because a tag may hold a space. A body that is
nothing but that word, or has an unbalanced bracket, stays whole. How each
caller sees the peeled handle is their choice (issue #1156, §16). Decisions
taken with it:

- **Color codes are content, not markup.** `|00`-`|23` survive in a
  stored MRC body (printable ASCII, safe to store) and are turned into
  SGR by `netbbs.rendering.pipe_codes` only after sanitization, per
  viewer, under a Profile toggle that defaults to on; every other pipe
  token is stripped at the parse boundary, identity fields lose all of
  them, and the search index receives the plain words. The recognized sender
  prefix's foreground is stored separately as `mrc_nick_color` and applied
  to the author on live display and replay under the same color toggle.
  Old rows keep their text and default author styling. A caller's own
  typed codes are still stripped: letting them through would put codes
  into local scrollback and Link-signed exports of mapped channels.
- **The hub's reply to one caller goes to that caller.** A `SERVER`
  packet addressed to an announced nick is that caller's reply
  (`LIST`, `CHATTERS`, `INFO`, `MOTD`, `HELP`, ...), delivered to their
  sessions alone under a per-caller line allowance, never recorded;
  `/mrc <subcommand>` sends the asks. `USERROOM` re-announces the
  caller in the mapped room at most once per keepalive tick,
  `USERNICK` retargets the announced nick. Generic-client corrections apply
  only when exactly one caller is announced; with multiple callers they
  produce a bounded diagnostic and a caller notice without guessing an
  identity. Generic-client `STATS` replies update the network summary and
  are shown only to callers with outstanding explicit requests.
  `TERMINATE` is fatal like
  `OLDVERSION`. An empty `to_room` is a network broadcast, shown in
  every active bridged channel (ENiGMA½'s reading).
- **CTCP lives in the bridge.** `VERSION`, `TIME` (UTC only), `PING`
  and `CLIENTINFO` are answered for any announced nick, bounded per
  remote sender because every request costs a reply; no SysOp or caller
  wiring exists for it.

Decision 3 (channels only) is unchanged by this issue; its amendment
(opt-in private messages) shipped as issue #305, below. Decision 2's
amendment shipped as issue #300, next.

**Issue #300 (open rooms)** lets a caller reach any room on the network
without a SysOp mapping it first, without a door-style client, and
without teaching native chat anything about MRC. Decisions:

- **An open room is a real channel row, materialized on demand** — the
  same precedent as a carried Link channel (§9.6): the first caller to
  open `lobby` gets a `channels` row named `mrc:lobby` with
  `mrc_room='lobby'` and `mrc_origin='caller'`, gates copied from the
  node-wide open-room defaults (level, age, name requirement), content-
  addressed on the room name so a room reopened after retirement keeps
  its id. A room the SysOp already mapped *is* that channel (one room,
  one channel), so opening it lands the caller in the SysOp's channel
  under its own gates. Every existing chat feature works unchanged
  because nothing but the origin marker differs.
- **The `mrc:` prefix is reserved.** No local user or SysOp screen may
  create or rename a channel into it (`netbbs.chat.channels` refuses),
  so a hub `lobby` never collides with a local `lobby` and no local
  channel can impersonate a room.
- **Lifecycle is bounded and SysOp-tuned:** a cap on open rooms (default
  32; opening refuses past it, nothing is evicted), a retention period
  (default 7 days) after which a room with no local participant, no
  activity and no follower is retired by the bridge's sweeper together
  with its scrollback, a blocklist of rooms callers may not open — or
  enter, once blocked — and `[A]dopt`/`Re[t]ire` on the channel
  screen. The sweeper runs on its own task, independent of the hub
  connection and of the switch, so a long outage or switching MRC off
  never strands the cap. It is a node action with no `User` behind it
  and the moderation log requires an actor, so a retirement is reported
  to the MRC diagnostic log and counted on the status screen instead of
  audited; a SysOp's own retire is audited as usual. The gates are
  checked before a room is materialized, so an account they turn away
  cannot spend the cap on rooms it can never enter.
- **Never Link, in either direction.** `link_channel` refuses a
  caller-origin row and the channel screen offers no `[L]ink` for it:
  MRC content is that network's, not this node's, and must never be
  re-broadcast under this node's signature. Inbound, a peer's genesis
  named into the `mrc:` prefix is carried as `local-mrc:...` (a channel
  Linked before the prefix was reserved keeps propagating; a squat
  lands nowhere), one claiming an open room's id is refused outright,
  and a carried message is projected only into a channel with a genesis
  on file. An adopted room keeps its `mrc:` name until the SysOp
  renames it, and Link asks for that rename before signing. An open
  room's id is content-addressed on the
  room *and* a per-node secret, so two nodes opening the same room
  never share an id and no peer can compute one. Adopting a room
  clears the origin and makes it an ordinary mapped channel again,
  which may be Linked like any other. The migration renames any local
  channel that already wore the prefix (it was typeable before this
  release) to `local-mrc:...`.
- **One MRC identity per account, in one room.** The hub knows one
  `nick@site` in one room; a second session of the same account
  entering a different bridged channel is refused naming the room the
  identity already holds. The rule is decided from local occupancy of
  the bridged channels, not from what has been announced to the hub
  (announcements lapse during backoff), and enforced once, atomically,
  at the chat loop's own hub join. The session that is leaving a room
  on `/join` does not count against itself unless another session of
  the same account stays behind. A nick suffix was rejected: it would
  present a second person to the network.
- **Finding rooms.** With MRC and open rooms enabled, Chat's Multi Relay
  Chat section lists retained rooms and discovered network rooms with hub
  user counts and topics. A fresh directory offers `lobby` as a starting
  point, plus opening a room by name. Entering a room requests `LIST` in
  the background, also on reconnect and roster refresh, at most once per
  five minutes per connection without spending callers' message allowance;
  `/rooms` refreshes it
  explicitly and shows a readable listing. The first MRC room of a
  session answers the background listing with one line -- how many more
  rooms there are, and that `/rooms` lists them and `/join <room>` moves
  there -- from the listing already in hand when the five-minute floor
  suppresses the request; never the table. Discovery never opens the
  listed rooms. Local access rules and the blocklist still gate entry.
  Refusals stay in the picker's header through redraws. Open rooms appear
  in this section, not the ordinary channel list. Inside MRC, `/join <name>`
  finds an open MRC room, then a local channel, then opens an MRC room;
  `/join mrc:<name>` opens or finds that MRC room from anywhere.
  `/join <room>` and `/mrc` subcommands and recipients have completion.
- **Directory evidence.** The hub's room-list layout observed during live
  use supplies the parser's anchored rows. Only replies to recent `LIST`
  requests populate the bounded cache; unfamiliar output remains text,
  never guessed room names. Counts and topics are advisory snapshots, with
  stale readings labeled with their age. Reconfiguring the hub clears its
  directory and observed room names, then reloads locally retained
  mappings. Unknown
  generic-client LIST lines stay scoped to explicit requesters and are
  suppressed for automatic requests, never broadcast to other callers.
- **The directory outlives the run (issue #636).** The hub lists rooms
  only to a user who is already in one: `LIST` is a session-context verb
  (MRCDoc protocol page, rev 1.26), and a probe of the development hub on
  2026-09-18 met both a client-context `LIST` and a `LIST` from a nick
  never announced with `NEWROOM` with silence. So the node never asks by
  itself -- no caller-less request, and no service nick parked in a room,
  which would put a fake user on the network from every NetBBS node.
  Instead the last *complete* listing (rows closed by the hub's footer)
  is kept in `node_config`, bound to the hub's host and port, bounded to
  the directory's own cap, every field validated again on load, and
  readings older than thirty days dropped -- room names change slowly, and
  every count is shown with its age; after a restart the section
  shows those rooms at once, each with its real age, until a caller's
  entry refreshes them. A complete listing replaces rather than only
  adds: a room the hub no longer lists has emptied (an MRC room exists
  only while someone is in it) and leaves the directory, unless chatter
  or a caller named it after the listing was asked for. A footer with no
  row before it proves nothing and changes nothing.
- **The room count says when the kept listing is out of date (issue
  #1283).** Room *names* need a user, but the room *count* does not: the
  hub's server stats query, a lone `~` on a connection of its own
  (MRCDoc rev 1.26), answers `<bbses> <rooms> <users> <activity>` and
  closes. While MRC is on, the bridge asks once a minute, never with
  more than one query in flight, and up to fifteen minutes apart while
  the hub does not answer. It sends nothing else on that connection: no
  handshake, no nick. The reading becomes the network's size that the
  SysOp status screen and Who's online already show. With nobody here
  on MRC, a count that differs from the kept listing's room count puts
  one line in the picker's header, such as "Room list as of 3 h ago; the
  network now has 14 rooms." That way the old listing stops reading as
  complete. With a caller on MRC, a count that changed since the last
  poll sends `LIST` at once, as that caller and under the shared
  five-minute bound, instead of waiting for the refresh. The count says
  *that* the network changed, never *which* rooms: the names still come
  only from a caller's listing. A count older than three polls is not
  used, and a poll that fails changes nothing a caller sees.
- **MRC on, nothing reachable.** Decision 2 stands: switching MRC on
  bridges nothing. With open rooms off and no active mapping, saving the
  settings says that no caller can reach MRC yet and names both ways out.
- **Occupancy.** The status line separates local participants and their
  local away count from the hub's remote roster count. An absent roster
  reads `?`, not zero; stale snapshots are marked. MRC roster replies do
  not supply structured away flags, so NetBBS does not invent a remote
  away count. The status refreshes at most five seconds after roster
  changes even in an otherwise quiet room.

**Issue #304 (presence and welcome)** carries the presence a caller
already has here onto the network and lets the network's own life show
through, without teaching native chat anything about MRC:

- **Away state is mirrored, never separate.** NetBBS's `/away` is the
  one away state; the bridge sends the hub `STATUS AFK <message>` and
  `IAMHERE:AWAY` for every room the caller is announced in, repeats
  them on every announcement (reconnects included) so the hub is never
  behind, and reports `IAMHERE:ACTIVE` on return. Those are the
  documented forms (MRCDoc protocol page, rev 1.26, read 2026-09-09;
  issue #373): there is no `AFK` verb, the away message is 55
  characters, and the spec names no verb that clears AFK -- the hub's
  own activity tracking decides when a returned caller stops showing
  as away.
- **The hub's welcome, once per session.** On a caller's first MRC room
  in a session the bridge shows the hub's `BANNER:` lines as remembered
  from connect and asks for `MOTD` as that caller; the reply reaches
  them alone through the per-caller path of #298. Once per session, not
  per room.
- **The network's size where callers look for company.** The bridge asks
  `STATS` once per roster refresh, parses `bbses rooms users` (the
  Mystic layout; an unparseable reply keeps the raw line for the status
  screen and shows "unknown" elsewhere), and Who's online, the picker's
  MRC section and the bridge status screen show "N users on M boards"
  with the reading's age. The bridge's own ask is parsed, not shown; a
  caller's `/mrc stats` is shown.
- **An open room's topic is the hub's.** Inbound `ROOMTOPIC` for an
  open room is stored on the row (a direct update; the node is not a
  `User`) so the status line shows it; `/topic <text>` inside an open
  room sends `NEWTOPIC` and the hub decides (MRC Trust applies there),
  a bare `/topic` shows the topic rather than clearing it. Inside a
  mapped channel `/topic` keeps its local meaning and audit.
- **Identity commands with masked input, nothing stored.** `/mrc
  register`, `/mrc identify`, `/mrc update password` and `/mrc
  roompass` ask for the secret separately with echo off and send it
  once as the caller's nick; the secret reaches no scrollback, log or
  input history (decision 5 stands: the node stores no credentials that
  are not its own).
- **A personal nick color.** The house-style body's handle color is a
  per-caller choice among the sixteen CGA colors (Profile, default
  yellow), read when the caller is announced; brackets and text color
  stay the house's, and typed pipe codes stay stripped (#298).

**Issue #305 (private messages, opt-in)** amends Decision 3 as the
planning pass intended:

- **One switch, off by default, both directions.** `[P]rofile` →
  `[P]rivate MRC messages` (Communication section). The bridge reads it
  when it announces the caller, alongside the nick color, and forgets
  it with their last announcement; switching it on applies the next
  time they enter an MRC room. Sending requires the same switch:
  starting conversations while refusing replies is not offered.
- **Delivered as a notice, never as chat.** An inbound private line for
  a caller who opted in is an `MrcNotice` of kind `private`, shown as
  `[MRC private] bob@Other: text` with the bell (unless the caller's
  chat bell is off, issue #1302), to that caller's
  sessions only, through the per-caller path of #298 and bounded per
  remote sender ahead of it (a flood from one nick must not evict
  chat). The sender's embedded handle is peeled exactly like a room
  line's and the color codes kept. It is never recorded: no scrollback,
  no search index, no log body.
- **Sent as the caller's nick, in the house style.** `/mrc msg <nick>
  <text>` and `/mrc r <text>` (to whoever last messaged them this
  connection) send a packet with `to_user` set and fields 5 and 6
  empty, the spec's private-message transaction (MRCDoc protocol page,
  rev 1.26; issue #374). Field 5 is `MsgExt`, reserved for extensions
  such as encryption, never routing -- "remove any reference to
  ToBBS" -- and the hub routes on the nick, which `USERNICK` keeps
  unique across boards. The site a nick was last seen at is still
  learned from inbound traffic, bounded, but only for the `nick@site`
  the sender's own echo shows. The body wears the house style so the
  recipient's client shows who wrote it; chunking and the caller's own
  send allowance apply as for a room line. The sender sees a local
  echo, never a channel line.
- **What "private" means, said once.** The first private line a
  session sends or receives comes with one note: private MRC messages
  are not private on that network; the hub and any client can read or
  spoof them. Once per session, whichever direction comes first.
- **Nothing for the SysOp to configure.** The switch is the caller's;
  the node-wide MRC switch and the channel mappings already decide
  whether they are on the network at all.

**Issue #377 (what the hub is told)** follows the protocol page's
control-context verbs, read on 2026-09-09:

- **Per caller, on announcement:** `TERMSIZE` always (the hub formats
  wide replies with it; it says nothing about the person); `USERIP` and
  `BBSMETA` only behind two SysOp switches under Inter-BBS chat (MRC),
  both off. The infrastructure principle applies: the node provides
  the interface, the SysOp owns the decision to hand a third party a
  caller's address or level. The spec's warning that a caller without
  `USERIP` "may get removed from room traffic routing" is stated on the
  switch and in the Handbook rather than used to justify a default.
- **Per connection:** `CAPABILITIES` lists what the bridge really
  handles (`MCI`, `CTCP`, `USERROOM`, `GOODBYE`, `SSL` when on) and
  carries the bridge module's SHA256 in the spec's hash field;
  `IMALIVE` carries the process id and a timestamp the hub echoes in
  `PONG`, from which the status screen shows the round trip.
- The `INFO*` fields the issue asked for already existed (issue #275).

**Issue #378 (smaller spec follow-ups)**, same reading:

- A server `NOTIFY:` is a one-time notice shown in every bridged
  channel like a banner and never remembered; the `STATS` reply's
  fourth field is the hub's activity level, shown beside the network
  size; handles are displayed with underscores as spaces, as the spec
  asks, while matching and addressing keep the wire spelling.
- The hub is moving its identity verbs to `!helper` chat text. Typed
  as chat, `!identify secret` would be recorded in scrollback and,
  the moment the channel is bridged, relayed to the room, so those
  four helpers are refused as chat in every channel while the node
  has an MRC bridge -- a paused mapping or a local channel is no
  safer a place for a password -- with a pointer to the masked `/mrc`
  forms, and the line is dropped from the input history. Nothing else
  typed with a `!` is touched: the hub's own helpers (`!time`,
  `!weather`) are ordinary chat.
- `STATUS LASTSEEN OFF` is a caller's choice on the Profile (on by
  default, the hub's default), sent on every announcement like the
  away state.
- A CTCP request whose target is `*` (blank `to_user`) is not answered:
  every wildcard would cost one reply per announced nick.
- Hub facts worth keeping: the handshake must arrive within one second
  of connecting, the heartbeat times out at 125 s, a client may hold
  at most eight connections per address, and the operator's public
  pool is `na-multi`, `eu-multi` and `au-multi.relaychat.net` (5000
  plain, 5001 TLS) with `mrcdev.relaychat.net` for development; the
  default host stays what it was until the operator retires it.
