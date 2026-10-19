# Issue #1373 — titled block replies for bots — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Titled reply blocks are chat's visual language for any answer longer than a
line: `/who`, `/mrc info` and the paged MRC answers use them (#1257,
`netbbs.net.chat_reply`). Bots were the one participant that could not.
Baud's help, `stats` and reminder list read as run-on lines joined with
semicolons, because `publish` refuses anything but 1 to 500 characters with
no line breaks.

The renderer takes sanitized plain text and styles it last; a block is at
most 78 columns wide, its last column wraps under itself and is never cut,
and a block taller than the chat area is paged
(`page 1 of 4 · PgDn more · PgUp back · Esc done`). The hub's asynchronous
answer is the precedent for a block that arrives after the question: its
title shows the time, not a count. Over Link, the signed `channel_message`
event is not key-strict on ingest, so an older node keeps a field it does not
know and shows `body`: the poll precedent (#1299). The live `channel_message`
frame and `scrollback_snapshot` entries are strict.

**Decision 1 — a row is an optional label plus text.**
- Each row has an optional short label and a text, like `/who` and `/mrc
  info`: the label in the label colour and aligned, the text wrapping under
  itself. Lists and key/value answers both read well.
- **Plain text only.** The bot sends no colour; the renderer owns it, as in
  every block.
- **The title:** `[BOT] Baud.bot · <the bot's title>`, with the time at the
  right when the viewer keeps timestamps. The bot supplies only its own title
  (1 to 40 characters); the host adds the label, so a block can never claim
  another author (#1208 Decision 2).
- **Narrow terminals:** the renderer's rules: the first column capped, the
  last wrapping, nothing truncated, and paging when taller than the chat
  area.
- Rejected: **one plain cell per row:** key/value answers lose their
  alignment. Rejected: **up to four cells:** more validation and more ways to
  wrap badly at 40 columns.

**Decision 2 — up to 12 rows of up to 200 characters, costing one line.**
- One answer is one output, as a public `reply` is: a block debits one line
  of the channel's hourly limit, or one private answer (#1372).
- Twelve rows fit a 24-row terminal's chat area unpaged.
- Rejected: **one line per row:** a single help answer could use a fifth of
  a 60-an-hour budget. Rejected: **different limits for public and private
  blocks:** two numbers to document and test.

**Decision 3 — over Link, a structured field beside a host-built fallback.**
- **The fallback:** the host builds a plain `body` for every block,
  `Title: row; row; row`, cut to 500 characters. That line is what older
  nodes show, what search matches, and what is debited. The bot never
  supplies a separate fallback that could disagree with the block.
- **The signed event:** `channel_message` gets an optional, bounded `block`
  field next to the host-built `body` (§9.6), with its own size check, as the
  poll field has. An older node shows the fallback line.
- **Live:** the live `channel_message` frame and the scrollback snapshot
  carry the block only to peers that advertise a new capability, working
  name `chat_bot_blocks_v1`. Every other peer gets the fallback line. A
  remote request's reply, which also goes directly to the requester's node,
  carries the same block, so live deduplication sees one line.
- **A malformed or oversized block** from a peer is shown as its fallback
  line, or refused when oversized, as a poll is.
- Rejected: **blocks only in local channels and private answers:** bots
  would look different in exactly the channels that show NetBBS to other
  nodes. Rejected: **one signed line per row:** other traffic lands between
  rows, it costs N lines, and it is not a block anywhere.

**History, search and filters.** A block is one `channel_messages` row: the
fallback body plus a nullable block column. Join replay and live display draw
it through the same renderer (#64). Search matches the fallback body. Mutes,
`/bots local|none` and remote mutes filter it like any bot line, because it is
one bot-authored message.

**Which actions.** `reply` and a private answer (#1372) may carry a block; a
private block's title tail reads `to you`, the marker of a private line.
`say` and `announce` stay one line: reminders and announcements are
one-liners. `hello` tells a bot whether the host takes blocks, as it does for
model conversation; an older host refuses an unknown shape as it refuses any
unknown action.

**Implementation notes.** One migration (the block column). Several replayed
blocks can fill a join replay: count a block's rows against the replay
budget, or draw replayed blocks unpaged.

A block may mark its labels as command syntax with the optional
`"labels": "commands"`, the only value: the renderer then draws each label as
`/help` draws its commands (the first word of each `, `-separated command in
the accent colour and bold, parameters in the label colour, punctuation and
separators muted), with the same helpers, so a bot's command list reads like
the host's. Any bot may use it (#1208 decision 4); Baud's `help` does. The
key rides inside the block on Link with no capability of its own; a 7.31 node
refuses the unknown key and shows the block's fallback line, a cost accepted
for a cosmetic field.
