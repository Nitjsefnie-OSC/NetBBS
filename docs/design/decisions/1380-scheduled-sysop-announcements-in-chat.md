# Issue #1380 — scheduled SysOp announcements in chat — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A SysOp running a community has a calendar of their own: net meetings, game
tournaments, downtime. Chat is where callers will see it, but the only ways
to get it there were to type it at the right moment or to put it in a
bulletin callers skip. NetBBS already says two kinds of dated line by itself:
the midnight message and the New Year and clock-change lines (#1354,
`netbbs.net.daybreak`). They are a host task that needs no bot, said only into
channels with someone in them, stored as the kind `daybreak` with author
`system`, drawn muted with no speaker, and deliberately local, because a
node's clock means nothing on another node. The backup schedule
(`backup_schedule.py`) is the precedent for a wall-clock rule: off, daily or
weekly on one weekday, at `HH:MM` in the node's display zone, with a gap time
read at its pre-transition reading and a repeated time at its first
occurrence.

**Decision 1 — a host feature, said as a system notice.**
- **Host, not bot:** announcements are a node feature, configured in the
  SysOp console, and work with no bot enabled, like the New Year and
  clock-change lines. A bot does not voice them.
- **How it reads:** a muted system notice with no speaker,
  `[19:00] Announcement: Net meeting tonight at 20:00 in #main.`
- **Stored as the local `daybreak` kind,** which was introduced as "a local,
  per-node system announcement". No new kind, so no `channel_messages`
  rebuild. Like the midnight message it is left out of unread activity.
- **The editor:** a §3.5 draft editor, reached per channel from Content >
  Chat channels, holding the text, the channel, the recurrence and the time,
  with a `Next` line showing the next slot in the node clock. Nothing is
  stored before `[S]ave`.
- **The text:** 1 to 160 characters, sanitized, one line, the bound bot lines
  and reminders already have.
- **The clock:** the node's display clock, as backups and the midnight
  message use (§6.3, "NetBBS has one clock").
- Rejected: **host-scheduled, voiced by a chosen bot.** The host would be
  publishing under a bot's name without the bot's process, against #1208
  Decision 1 ("the bot uses that API like any other bot"). Rejected: **a Baud
  feature.** It needs a new API to prove the caller is the SysOp, keeps node
  configuration in a 16 KiB bot state file, and stops when Baud is off.

**Decision 2 — once, daily, weekly on chosen weekdays, or monthly.**
- **Once:** on a date at a time, for maintenance notices.
- **Daily:** every day at a time.
- **Weekly:** on a set of weekdays at a time ("Mon and Thu"), the backup
  schedule's shape widened to several weekdays.
- **Monthly:** on the nth weekday of the month at a time ("first Friday").
  The editor offers first, second, third and fourth, which every month has.
- **Clock edge cases** follow `backup_schedule`: a time that falls in a
  spring-forward gap is said at its pre-transition reading, and a time that
  repeats in the autumn is said at its first occurrence.
- **Missed slots are skipped, not caught up.** This follows the midnight
  message. A backup catches up because a missed one loses data; an
  announcement said late gives a wrong time ("tonight at 20:00" at 23:00).
  After a node restart the next slot is the next one in the future.
- Rejected: **exactly the backup shape** (one weekday): twice-weekly events
  would need two rules.

**Decision 3 — seen on other nodes in a Linked channel, after the SysOp
approves that channel.**
- **The approval:** a Linked channel carries announcements to other nodes
  only after the SysOp approves it for announcements, once per channel,
  confirmed on Save the way a bot's Linked approval is (#1208 Decision 3),
  because chat has no retraction and the line reaches every peer. Without
  the approval a rule for a Linked channel is said on this node only, the
  way the midnight message is.
- **An authored event:** a system notice has no author, and Link carries
  only authored lines, so an announcement in an approved Linked channel is
  signed as an ordinary `channel_message` whose author is the SysOp who saved
  the rule (§9.6), with an optional `announcement` marker. Newer nodes draw
  it as an announcement naming the node it came from; an older node, which
  keeps the marker it does not know and shows `body`, shows it as an ordinary
  line from that SysOp. That is the poll precedent for an optional field on
  a signed event (#1299).
- **Live:** the live `channel_message` frame and the scrollback snapshot
  carry the marker only to peers that advertise the capability for it (the
  live frame refuses unknown keys, §8.10.2). Other peers get the plain line.
- **Whose authority:** the rule records the SysOp who saved it, as the door
  outbound hook records the SysOp it runs on (§15, Phase 7). If that account
  is deleted or is no longer a usable SysOp, the rule's Linked carriage
  lapses and it is said on this node only until another SysOp saves it.
- **The clock:** the times are this node's. The text is the SysOp's own words
  and says what it means; other nodes add no conversion.
- Rejected: **local only** (the daybreak rule). The issue's own expectation,
  and the maintainer's choice, is that a community's announcement reaches
  the whole Linked channel once the SysOp has said so.

**Decision 4 — always recorded; at most 8 rules per channel, an hour apart.**
- **Always recorded:** an announcement is said into its channel even when
  nobody is in it, so a caller who arrives later finds it in scrollback. The
  channel was chosen on purpose. This differs from the midnight message,
  which only greets people who are there.
- **At most 8 rules per channel.**
- **At least an hour apart,** checked on Save, so a misconfigured rule
  cannot flood. Two rules in one channel whose times of day are less than an
  hour apart are refused, whatever their days.
- Rejected: **only into occupied channels:** a caller arriving at 19:05
  would miss the 19:00 notice entirely. Rejected: **a count cap only:** eight
  daily rules could still be eight lines in eight minutes.

**Implementation notes.**
- One migration: a rules table (channel, text, recurrence, weekday set,
  monthly ordinal, time, date for a one-off, the saving SysOp, last slot
  said), deleted with its channel, plus the per-channel announcement
  approval. A received announcement needs its marker kept locally: a
  nullable column beside the bot block column of #1373 avoids another
  `channel_messages` rebuild.
- The announcer extends or sits beside the daybreak task, waking for the
  earliest of midnight, a clock change and the next announcement, and is
  owned and cancelled with it.
- An MRC-bridged channel takes announcements locally only: a system notice
  is never relayed to the hub, as the midnight message is not.
- Tests cover both DST transitions with the backup schedule's slot tests.
