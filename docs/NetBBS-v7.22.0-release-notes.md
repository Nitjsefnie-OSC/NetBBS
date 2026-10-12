# NetBBS v7.22.0

This release makes chat easier to read and makes linked channels one room for
presence as well as conversation:
- Command replies such as `/who` and `/list` come as titled blocks.
- The MRC hub's answers arrive as one block, paged when they are long.
- In a linked channel, every BBS now sees everyone who is in it on every other
  BBS.

The SysOp and User handbooks were reworked to be scanned rather than read
through.

**Nothing migrates:** the node database stays at schema 130. The real-time
protocol version (4), door API, bot API, career and world versions are
unchanged. Upgrading is a wheel swap and a restart.

It is a minor release, not a patch, because chat gains new behaviour and Link
gains a capability, `presence_relay`.

## Chat command replies are titled blocks (PR #1257)

Replies to chat commands used to be flat, unaligned lines that were hard to
tell apart from the conversation around them. `/list`, `/who`, `/names`,
`/whois`, `/finger`, `/members`, `/bots` and `/mrc` now each answer with one
block:

```
── Who's in #lobby ───────────────────────────────── 5 ──
 alice              here
 bob                here · away: brb
 [BOT] Baud.bot     addressed messages only
 jasper@vertigo     on MRC
 kite@blackwater    on MRC
```

- A rule carries the title and, at its right end, how many it lists.
- Rows line up in columns; the last column wraps under itself.
- A block is at most 78 columns wide. At 40 columns an over-wide name stands
  on its own line rather than being cut.
- A one-line answer, such as a refusal or "No one is here.", stays one line.

## `/list` counts everyone in a channel (PR #1257, issue #1281)

`/list` used to count only this BBS's own callers, even in a channel bridged to
MRC or shared over Link. It now counts everyone this BBS can see:

- `2 here · 3 on MRC` for a bridged room. The MRC count comes from the room's
  roster while someone here is in it, and from the hub's room list otherwise,
  marked with its age when it is not current.
- `4 on Vertigo` for a linked channel's callers on another BBS, or
  `5 on 2 linked BBSes` when they are on several.

## MRC hub answers arrive as one block, a page at a time (PR #1257)

The answer to an `/mrc` question, such as `/rooms` or `/mrc bbses`, used to
scroll past one `[MRC]` line at a time. It now appears a moment after the hub
finishes sending, as one block headed `MRC hub · /mrc bbses`.

An answer taller than the chat area comes a page at a time. Each page ends
`page 1 of 4 · Enter shows more`:

- **Enter** on an empty line shows the next page.
- **Typing anything else** skips the rest of the answer.

Chat carries on while an answer arrives. A second question asked before the
first answer finished gets its own block, shown once you have paged past the
first.

## Linked channels: every BBS sees every BBS's callers (issue #1281, PR #1284)

Presence in a linked channel used to travel one way, from the BBS that started
the channel (its origin) to the BBSes subscribed to it. So:

- the origin's `/who` never listed anyone on a subscribing BBS;
- a subscriber never saw the people on another subscriber.

Both held even while those people were talking in the channel, since v7.15.0
relays their lines (#860).

Presence now travels both ways, the same way lines do:

- A subscribing BBS reports who is in the channel to the origin when it
  subscribes, then each join and leave.
- The origin announces them to its own callers and passes them on to its other
  subscribers, naming the BBS each person is on.
- `/who`, `/names`, `/list` and the join/leave notices show everyone, each with
  the BBS they are on.

Every BBS still applies its own rules. A caller it has blocked or quarantined,
or one from a BBS that may not publish there, is not listed. This is the same
rule as for that caller's lines.

**Mixed versions:** this travels only between BBSes that both run v7.22.0 or
later. An older BBS keeps today's behaviour: as an origin it knows only its own
callers, and as a subscriber it sees only the origin's. Nothing breaks either
way. There is no protocol version bump; v7.22.0 advertises the new capability,
and peers use it only with each other.

## Smaller changes

- **The MRC room list lasts 30 days** (PR #1257). The last complete room list,
  kept for the picker when nobody is on MRC, used to be dropped after 7 days.
  Room names change slowly, and every count still shows its age.
- **The Node menu's `[B]ack` says where it goes** (issue #1273, PR #1282). It
  always said "Return to Operations". Opened from the console's landing
  screen, it now says "Return to the SysOp console".

## The handbooks are easier to scan (PRs #1256 to #1280)

Every chapter of the SysOp handbook and the User handbook was reformatted:

- An "At a glance" table opens each chapter.
- Procedures are numbered steps.
- Warnings and tips are called out.
- Menu paths use one notation, **Settings ▸ Accounts**, which a test now checks
  against the real menus.

Content is unchanged except where a label had drifted from the software.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.21.0 is the reverse: the node simply
stops advertising `presence_relay`, and its linked channels go back to one-way
presence.

## Verification boundaries

- **Chat blocks and paging:** tests cover
  - the block layout at 40 and 80 columns, including an over-wide cell;
  - `/list` counts for MRC and for one and several linked BBSes;
  - an MRC answer collected, paged and drained with Enter;
  - a second answer waiting behind a pager.

  Two review findings, blank lines at 40 columns and a second answer
  overwriting the first one's pages, were fixed with regression tests.
- **Linked-channel presence:** three-BBS tests on real loopback sessions cover
  - every BBS seeing every other's callers, through join, leave, unsubscribe
    and a subscriber dropping;
  - a second session of the same account leaving;
  - an older subscriber getting nothing relayed and reporting nothing;
  - a subscriber trying to report another BBS's caller (refused);
  - a caller of a quarantined BBS not being listed.
- **Full Windows suite:** 11,904 passed and 136 skipped, and the timing-sensitive
  test passed. One test failed:
  .
  It waits a fixed 6 seconds for a bot announcement that crosses a subprocess
  and Link, and under the parallel run it timed out, as it did in two earlier
  full runs that day. Alone it passes in about a second. Issue #1287 gives it a
  realistic bound.
- **Not yet checked live:** linked-channel presence has not run between real
  BBSes, and the paged MRC answers have not been tried against the live hub's
  longest replies. NetBSD and real terminals remain manual evidence.
