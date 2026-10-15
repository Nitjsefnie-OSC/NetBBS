# NetBBS v7.27.0

This release lets a caller ask for a new username and a SysOp rename an
account, with mail to the old name still arriving and linked nodes following
the rename. A SysOp can share a whole Community over NetBBS Link, and another
node can subscribe to it. Callers can post one message to several boards at
once, ask polls on message boards, and choose when chat rings their
terminal's bell.

**Four migrations run:** the node database goes from schema 130 to 134. The
Link, real-time, door API and bot API versions are unchanged
(`NETBBS_PROTOCOL_VERSION` 1, `REALTIME_PROTOCOL_VERSION` 4,
`DOOR_API_VERSION` 4, `BOT_API_VERSION` 1). The Link additions are additive:
- three new event types for Link Communities, sent only to peers advertising
  the new `link_communities` capability;
- one new event type, `board_poll_vote`, which an older node relays opaquely;
- optional `crosspost` and `poll` fields on `board_post`, and an optional
  `poll` field on post edits, which an older node keeps and ignores;
- an optional `user_renames` field in the endpoint descriptor, which an older
  node ignores.

**Rolling back to v7.26.0 needs a restore** (see below).

It is a minor release because callers and SysOps get new screens, two new
Profile fields and a new console setting, and nothing that worked before
stops working.

## Renaming a username (issue #1306, PRs #1334 and #1337)

Built as decided in design doc §16 (issue #1111).

**The caller asks.** **Profile ▸ Username** takes the name the caller would
like. It is checked at once, as a new account's name is: grammar, staff
titles, names taken or held, and look-alikes of the SysOp. Typing another
name replaces the request, and a blank line withdraws it. One approved
rename per 30 days. A refusal and its reason are shown at the next login.

**The SysOp decides.** **Users ▸ Name requests** lists open requests. The
Users screen and the console's attention panel count them. A request opens
with <kbd>A</kbd> **Approve** and <kbd>R</kbd> **Refuse**, and a refusal asks
for one reason line. <kbd>C</kbd> **Change username** on an account's screen
renames it directly, without the 30-day limit. Its confirmation names the
caveat for RLogin remote doors, which may see a renamed caller as a new
player. Only the SysOp renames, approves or refuses; staff do not. A rename
that changes only the case of a name is refused.

**What a rename does:**
- The account's live sessions end with `Your username is now '<new>'. Log in
  again with it.`
- The account keeps its id, level, mail, posts, settings and door saves.
  Posts, files and sent mail keep the name they were written under. Chat
  lines show the new name.
- On a node that has run NetBBS Link, the old name is held for the account
  (**Users ▸ Held names**, "renamed to ..."). Link mail, FTN netmail and
  `/msg` written to it still reach the account. On a node that never ran
  Link, the old name is freed instead, as for a deleted account.
- Requests, refusals and renames are audited and show in the account's
  history.
- Attestations this node issued under the old name are revoked and
  reissued under the new one.

**Over Link.** The node's hello carries its recent renames in the signed
endpoint descriptor's new `user_renames` field: at most 64 entries, none
older than 400 days, and only names still held for the account. A peer that
learns `old -> new`:
- moves what it recorded about the old name to the new one: trust overrides,
  signals, vouches and probation progress. Where both names carry a
  restriction, the stricter stays, so a rename never lifts a block or a
  quarantine;
- applies a mail block on the old address to the new one, and sends a letter
  addressed to the old name to the new one;
- shows the subject as `nib@Node (formerly alice)` on its trust and
  blocked-senders screens;
- refuses content newly signed under the old name. Content signed before the
  rename keeps its name.

A peer refuses and logs, under `netbbs.link`, a rename onto a name that
already had history before it, and a rename back to a name the node renamed
away from. Callers never see these refusals.

## Link Communities (issue #1310, PRs #1336 and #1340)

Built as decided in design doc §16 (issue #1308).

**Sharing.** On a Community's screen (**Content ▸ Topics ▸ List**),
<kbd>L</kbd> **Link this Community** offers it to other nodes with its
name, description, default levels and gates, and the list of its boards,
channels and file areas that are already Linked from here. Linking the
Community links nothing else. The list updates on the next sync pass after
a member is linked, unlinked or moved. <kbd>C</kbd> **Close on Link** closes
it on every carrying node, for good; it stays here as a local Community and
can be linked again later as a new one. <kbd>R</kbd> **Remove** on a Linked
Community closes it first. Removing one needs the running node, which signs
the closure, so the shell's `python -m netbbs.admin` console refuses it.

**Subscribing.** A Community from another node is never carried on its own.
It waits under **Link status ▸ Offered** with its origin and member count:
- <kbd>A</kbd> **Accept** creates a local copy with the origin's defaults
  (a short suffix is added on a name clash) and carries its members within
  the existing carry caps. A member past a cap waits under **Offered** with
  the reason "cap".
- Members the origin adds later follow. A member the SysOp files elsewhere
  stays where it was put. A member the origin removes moves out of the copy
  and stays carried.
- <kbd>R</kbd> **Remove** on the copy leaves the Community: the members stay
  carried, it is listed under **Link status ▸ Excluded**, and
  <kbd>R</kbd> **Restore** subscribes again.
- When the origin closes it, the copy becomes an ordinary local Community,
  marked as closed at its origin.

Linked Communities carry the Linked marker on the SysOp's Community list and
on callers' **Topics** list.

**On the wire.** Three new event types, `community_genesis`,
`community_members` and `community_closure`, all signed by the Community's
origin. A members list names at most 500 ids of each kind, and a node keeps
at most three events per Community. Inventory gains a signed `communities`
map and a `communities` kind in `not_carried`. Nodes advertise the new
`link_communities` capability, and a node sends any of this only to a peer
advertising it. An older peer gets no community events, not even opaque
ones, and keeps carrying the member boards, channels and file areas as
before.

## Cross-posting (issue #1331, PR #1341)

Built as decided in design doc §16 (issue #1298). <kbd>T</kbd> **To boards**
on a new post's review screen picks more boards for the same post. Replies
and edits stay on one board.

- **The cap** is a new setting, **Cross-post boards** in **Settings ▸ Limits
  & retention**: 5 by default, 1 to 20, the board the post was written on
  included. 1 hides **To boards**.
- **Boards the caller cannot post on** are listed dimmed with the reason:
  level, verified name, age, closed, the Link posting setting, or no color
  for an art post.
- **All or nothing:** every board is checked before any copy is written. If
  one refuses, nothing is posted, and the review screen names the board and
  why.
- **One copy per board.** Each copy is an ordinary post with its own
  replies, moderation, edits and removal. A moderated board may hold its copy
  while the others appear.
- **Reading:** opening one copy marks the reader's other copies read. New
  scan and search count the post once. A copy's byline names the other
  boards it is on that the reader can open ("also in ...").
- **Linked copies** carry an optional `"crosspost"` field on `board_post`.
  An older node shows them as separate ordinary posts.
- **FTN copies** are one echomail message per echo, each with its own MSGID,
  whose first line is `* Crossposted in` and the other echoes of the same
  network. Local and Linked board names never go to an FTN network.

## Board polls (issue #1332, PRs #1342, #1343 and #1344)

Built as decided in design doc §16 (issue #1299). <kbd>N</kbd> **New poll**
on a board's list asks a question with 2 to 10 answers (200 bytes each at
most). The author chooses one answer or up to a number of them, the closing
date (7 days ahead by default, at most 365) and whether results show after
voting or only after the close. A poll is a post: moderation, expiry,
withdrawal and takedown apply to it. Polls are not offered on FTN boards.

- **Voting:** anyone who may reply may vote with <kbd>V</kbd> **Vote**, and
  change their vote until the poll closes. A held poll takes no votes until
  it is approved. The guest account holds one vote, which a later guest call
  cannot change.
- **Results** show as bars (`#` and `.` with Unicode style off), with the
  caller's own answers ticked, the voter count, and "N votes not counted"
  when some were left out.
- **Closing:** the author and the board's moderators can end a poll with
  <kbd>C</kbd> **Close poll**. Withdrawal and takedown close the poll and
  hide its totals. Once anyone has voted the answers lock; the closing date
  stays editable with <kbd>E</kbd> **Edit**.
- **No screen names a voter,** only totals and the number of voters.

**On a Linked board** the poll travels as an optional `"poll"` field on its
`board_post`, so its answers are fixed when it is posted. A closing-date
change or a close travels as a post edit with the text unchanged. A vote is
a new signed event, `board_poll_vote`, naming its voter and signed by the
voter's home node. Each node keeps the latest vote per voter and counts it
itself:
- no vote dated after the close;
- no vote whose author or home node its trust policy hides;
- at most 50 voters from each other node per poll, and at most 5,000 voter
  rows per poll.

So two nodes can show different totals for one poll. A vote from a
probationary remote user is not accepted, and a renamed voter counts once. An
older node relays votes opaquely and shows the post without its poll.

**A Linked vote is not secret from the network.** Every carrying node holds
who voted what, and its operator can read it from the database. Before a
caller's first vote on a Linked poll, the poll says "Your vote is shared with
every BBS that carries this board." On any board, the local database holds
each account's vote, and the User Handbook says so.

## Chat bells (issue #1333, PR #1339)

Built as decided in design doc §16 (issue #1302). Chat rings the caller's
terminal bell (BEL) when it needs them. Callers choose when in **Profile ▸
Chat bell** or with `/bell off|private|mentions` in chat; a bare `/bell`
shows the level.

- **Private** (the default): `/msg`, `/private`, a message from someone on a
  linked node or on MRC, and a `/dm` invitation.
- **Mentions:** those, and a channel line that names the caller by username
  or alias, with or without `@`. The caller's own lines, bots' lines and door
  lines never ring.
- **Off:** never.

`/away` does not silence it. It rings at most 3 times in a row, refilling one
every 10 seconds, and once per 30 seconds for any one sender. It never rings
while the caller is scrolled back with <kbd>PgUp</kbd>, and only on the chat
screens and in direct chat. In the web client a bell plays a short tone once
the caller has pressed a key or clicked in the page, and a hidden tab's title
starts with `(!)` until it is shown again.

## Fixes

- **Baud no longer skips a new file or post** (issue #1326, PR #1329). A bot
  reading its event journal could move its cursor past an upload or post
  committed by another connection while it read, so that one was never
  announced. The cursor now moves only past what the read saw.
- **The MRC private-line bell follows the caller's bell level** (PR #1339).
  It used to ring always. At `off` it is now silent.

## For developers

- **Banner and masthead screens** (issue #1327, PR #1335): one set of screens
  draws all eight slots from a `_BannerSlot` description, and
  `admin_banners.py` shrinks from 3,926 to 2,041 lines. Callers and SysOps
  see no change: 96 recorded console walks at 80×24 and 40×12 were byte
  identical before and after.
- **The welcome-banner login tests** wait up to 60 s instead of 5 s for a
  hang (PR #1338). Three Argon2id checks took longer than 5 s under the
  parallel suite.
- **The worklog** notes that two reads in one lane job are two snapshots,
  so a journal cursor must read its head first, and records the invariants of
  renames, Link Communities and polls.

## Upgrade and rollback

Take a backup, stop NetBBS, replace the wheel and start it. Four additive
migrations run on the node database, taking it from schema 130 to 134:

- **131: renames** (#1306): `retired_usernames.user_id`, the
  `username_change_requests` and `username_changes` tables, the
  `link_user_aliases` table, and `channel_messages.author_user_id`, which is
  backfilled for existing chat lines.
- **132: Link Communities** (#1310): Link columns on `communities`,
  `link_events.community_id`, `community_placed_locally` on boards, channels
  and file areas, and `link_carry_decisions` rebuilt to accept the
  `communities` kind, with every row copied.
- **133: cross-posting** (#1298): `posts.crosspost_group`.
- **134: board polls** (#1299): the `board_polls`, `board_poll_options` and
  `board_poll_votes` tables.

`netbbs.toml` has no new keys. The cross-post cap (`crosspost_board_limit`)
and each caller's bell level are stored in the node database. The systemd
and NetBSD rc.d examples did not change.

On the first start after the upgrade:

- **Every caller's chat bell is at Private.** Callers whose terminal beeps
  will now hear a bell on private messages and `/dm` invitations.
  **MANUAL —** tell your callers about **Profile ▸ Chat bell** and `/bell`.
- **Profile field numbers shift.** Chat bell is the last Communication field,
  so the number of every Display and Account field goes up by one, and Username
  is added as the last field. **MANUAL — check scripts and notes** that
  name Profile fields by number.
- **Cross-posting is on,** up to 5 boards. Set **Cross-post boards** to 1 to
  turn it off.
- **No Community is Linked** until a SysOp links one, and no offered
  Community is carried until a SysOp accepts it.

**Rolling back needs a restore.** A v7.26.0 wheel refuses a database newer
than schema 130 ("database schema version 134 is newer than this NetBBS build
supports (130)"). **MANUAL — to roll back:** stop NetBBS, install the
v7.26.0 wheel, then restore the backup taken before the upgrade with
`python -m netbbs.backup restore`. Anything since the upgrade is lost with
it: renames, name requests, Linked Communities and subscriptions,
cross-posts, polls and votes, and every other post, message and setting.

What peers learned does not roll back with you:

- **A rename peers have learned stays with them.** After the restore, the
  account has its old name again, and upgraded peers refuse content newly
  signed under it. **MANUAL —** do not roll back after a rename has reached
  your peers; if you must, expect that account's new posts to be refused on
  upgraded peers.
- **A Community you Linked stays on the nodes that subscribed.** The
  restored node no longer knows it and cannot close it.
- **Votes and cross-posts already sent** stay on the carrying nodes.

## Verification boundaries

- **Full Windows suite:** 12,224 passed and 136 skipped on the release tree, with no failures,
  and the timing-sensitive test passed. Each feature also had a full run
  before it merged. Those runs found two real breaks, both fixed before
  merging: a Profile test still expected 24 fields after Username was added,
  and the Community screen's first unlink key broke the first-letter hotkey
  rule (it became Close on Link). Every other failure was a timeout under
  load in a test the change did not touch, and passed when run alone.
- **The multi-node tests run on one Windows machine.** Renames over Link,
  Link Communities and Linked polls were tested with harness nodes and a real
  loopback transport (a hello carrying a rename, promote then subscribe,
  votes pushed and pulled through a third node, restarts). No live test
  between independently run nodes has been done for any of them.
- **Older peers:** that a v7.26.0 node ignores `user_renames`, `crosspost`
  and `poll`, and relays `board_poll_vote` opaquely, rests on the existing
  optional-field and opaque-relay rules. A test checks that a peer without
  `link_communities` gets no community events. No test ran a v7.26.0 build
  against this release.
- **The web client's bell tone** was tested with a faked xterm and WebAudio
  in Node, not in a real browser. Whether a real Telnet, SSH or SyncTERM
  terminal beeps is up to the terminal and was not checked.
- **Screens:** the poll screens were checked by eye at 80×24 and 40×12. The
  rename, Community and cross-post screens were driven by scripted keys in
  tests, not checked by eye in a live session.
- **The banner refactor** was checked against 96 recorded console walks;
  screens outside those walks were not compared.
- **The vote-secrecy limit is by design:** carrying nodes' operators can read
  Linked votes, and any operator can read local votes from the database.
