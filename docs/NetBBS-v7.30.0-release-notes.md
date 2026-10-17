# NetBBS v7.30.0

This release brings moderation to NetBBS Link. The node a linked board or
chat channel comes from can let people on other BBSes moderate it, and their
edits, removals, mutes, bans and topics reach every node that carries it. A
linked chat channel can now be handed to another node, as a board could.
Callers can also hold a direct chat with someone on another BBS: `/dm
name@TheirBBS`.

**This release migrates:** the node database goes from schema 134 to 139.
Rolling back to v7.29.0 needs the backup taken before the upgrade (see
below). The Link real-time protocol stays at version 4: the new behaviour
is advertised as two capabilities, `link_moderation` and `direct_chat_v1`, and
is only ever sent to nodes that advertise them. The door API and bot API are
unchanged.

It is a minor release because it adds features and a migration, and changes
nothing an older node relies on.

## Moderators on other nodes (issue #1359, PRs #1360 and #1361)

**Link status ▸ Moderators** lets users of other nodes moderate the linked
boards and channels this node originates.

- <kbd>A</kbd> **Add** opens a form. **User** is `name@TheirBBS`. **On** is one
  linked board or channel, one of this node's Link Communities, or everything
  this node originates. **May** is what they may do there: edit and remove
  posts, mute and ban, and set the topic. Nothing is granted until you
  <kbd>S</kbd> **Save**.
- <kbd>R</kbd> **Remove** takes a grant back. Each node stops accepting that
  user's moderation as the change reaches it.
- **Held by this node's users** lists what your own users may moderate
  elsewhere. Such a user edits and removes posts on those boards here, and
  the change reaches every node carrying the board; their confirmation says
  so instead of "on this node only".

Every node checks a delegate's action against the grants it holds from the
board's or channel's origin, so a revoked grant stops working as soon as the
revocation arrives. Names are matched ignoring case.

## Mutes, bans and topics across Link (PRs #1363 and #1364)

In a linked channel, `/mute`, `/unmute`, `/ban` and `/unban` also take
`name@TheirBBS`.

- **On the channel's origin, or for a delegated moderator,** a mute or ban
  reaches every node carrying the channel. Each one hides that caller's
  messages there, and the caller's own node enforces it on them: a muted
  caller cannot speak, and a banned one cannot enter, or speak if already
  inside.
- **A moderator on a carrying node without a grant** mutes or bans a caller
  from another node on that node only. That never lifts the origin's own
  mute or ban, and the origin's always outranks a delegate's while it is in
  force.
- **Topics:** a linked channel has one topic everywhere, the newest set on
  its origin or by a delegate with the topic permission. On a channel your
  node carries, `/topic` is refused for anyone else, because a local topic
  would contradict the shared one. A linked channel's topic is at most 300
  bytes.

## Handing a linked chat channel to another node (PR #1366)

A linked channel's screen now shows its **Origin**, and the origin can choose
<kbd>T</kbd> **Transfer origin** to offer it to another node. That node's
SysOp accepts with <kbd>A</kbd> **Accept transfer**, as for a board. From then
on the new node decides the channel's topic, mutes and bans, and its grants
count there. The SysOp Handbook now describes handing over boards and
channels.

## Trust and history (PR #1367)

- A mute, ban, topic, or post edit or removal from another node does not take
  effect when that node is quarantined or blocked here, or when the moderator
  who made it is. Nor does a grant from an origin quarantined or blocked here.
- A linked board's or chat channel's <kbd>H</kbd> **History** lists the ones
  that took effect, and who made them. Chat channels gain **History**, as
  boards have; its moderators include the users of other nodes the origin's
  grants name.

## Direct chats with someone on another BBS (issue #1368, PR #1369)

`/dm name@TheirBBS` in chat, or <kbd>I</kbd> **Invite to chat** on their entry
in Who's online, starts a direct chat with a caller on a linked BBS. It works
like a local one: they are asked on their main menu, or told in one line to
go there, and you can cancel with <kbd>C</kbd> while you wait.

- If their BBS doesn't support it yet, you are told so; `/msg` still works.
- If they can't be reached, don't take live messages, or block you, you are
  only told they can't be reached right now.
- If the connection between the two BBSes drops, the chat ends and says so.

## Link v1 interoperability vectors (issue #71, PRs #1362 and #1365)

`docs/NetBBS-link-v1-interoperability.md` describes Link v1's canonical
envelope for anyone writing another implementation. It comes with fixed
test keys, signed example events of five kinds, and wire JSON that must be
accepted or refused (duplicate keys, floats, unsafe integers, unsupported
versions, trailing data, invalid UTF-8). A small Node.js checker that shares
no code with NetBBS runs the same corpus, and `python
scripts/link_interop_check.py` runs it alongside the Python tests. This is a
first, bounded step: it shows two implementations agree on these vectors,
not that NetBBS interoperates with any other software.

## Upgrade and rollback

- **Upgrade:** take a backup, install the wheel and restart. The database
  migrates from schema 134 to 139. The new tables start empty, and nothing
  changes until someone grants a Link moderator, sends a mute or topic, or
  offers a channel.
- **Rollback to v7.29.0 — MANUAL:** v7.29.0 refuses a database newer than
  schema 134 ("database schema version 139 is newer than this NetBBS build
  supports (134)"). Stop NetBBS, install the v7.29.0 wheel, then restore the
  backup taken before the upgrade with `python -m netbbs.backup restore`. In
  that order: a restore run by v7.30.0 would migrate the backup straight back
  to 139. Anything since the upgrade is lost with it: moderator grants, Link
  mutes, bans and topics, channel handoffs, and every other post, message and
  setting.
- **What peers learned does not roll back with you.** Other nodes keep the
  grants, mutes, bans and topics this node signed, and a channel handoff it
  took part in. **MANUAL —** do not roll back after this node accepted or
  offered a channel's origin: peers would go on treating the new origin as
  authoritative while this node no longer knows it.
- **Mixed versions:** a node on v7.29.0 or earlier is never sent the new
  events or frames. It keeps showing what newer nodes hide, and its callers
  can't be invited to a cross-node direct chat.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,415 passed, 136 skipped, none failed (38:27); the timing-sensitive test passed too.
- Link moderation, channel handoffs and cross-node direct chats are tested end
  to end between real nodes in the test suite, on one machine. They have not
  yet been exercised between live nodes on different hosts.
