# NetBBS v7.21.0

This release gives a SysOp a way to clear an outgoing NetBBS Link delivery that
can never arrive, fixes replays that gave up after one try, and makes every
breadcrumb in NetBBS show the way you came. **Nothing migrates:** the node
database stays at schema 130. The Link, real-time, door API, bot API, career
and world versions are unchanged. Upgrading is a wheel swap and a restart.

It is a minor release, not a patch, because the Outbox gains a new SysOp
action, Delete.

## Delete a dead-lettered Outbox item (issue #1245, PR #1247)

When a recipient node is gone for good, its undeliverable mail used to sit in
**Operations › Outbox** for ever. A replay could only fail again, and the
operations panel kept showing **NetBBS Link: ATTENTION**.

A dead-lettered work item now offers `[R]eplay now  [D]elete  [B]ack`.
Delete asks first, then:

- removes the work item and records it in the audit log;
- lowers the dead-letter count, so ATTENTION clears once nothing else needs it;
- leaves the letter itself *expired*, so its sender still knows it was not
  delivered.

## A replay gets its whole retry schedule again (issue #1245, PR #1247)

A work item gives up after ten attempts or five days, whichever comes first.
A replay reset the attempts but kept the original age. So replaying anything
older than five days gave it exactly one try: its first failure dead-lettered
it again. A replay now starts the five days over.

## Breadcrumbs show the way you came (issue #1246, PR #1248)

Every screen used to spell out its own breadcrumb, and the spellings had
drifted apart. Some SysOp screens left out *SysOp*, Settings screens skipped
*Settings*, and many pickers and editors showed only the node's name. For
example:

```
Before                                      Now
Reliable Link › Settings › Mastheads & …    Reliable Link › SysOp › Settings › Mastheads & banners
Reliable Link › Timestamp display           Reliable Link › SysOp › Settings › Timestamp display
Reliable Link › Trust subjects              Reliable Link › SysOp › Settings › Policy trust › Trust subjects
Reliable Link › Retrying/dead-lettered …    Reliable Link › SysOp › Operations › Outbox › Retrying/dead-lettered work items
```

Now each menu adds its name as you enter it, and every screen draws its
breadcrumb from that path. This applies to callers' screens and to the SysOp
console alike.

- **A screen reached from two places is right from both.** A board opened from
  New scan reads *New scan › board*, and from the board list *Message boards ›
  category › board*.
- **A SysOp console shortcut shows the shorter path it took,** for example
  *SysOp › Outbox* rather than *SysOp › Operations › Outbox*.
- **The main menu is the root,** so it never appears as a segment.
- **A category is now part of the path.** Before, a category's boards or file
  areas showed only the board or area name.
- **A path too wide for the terminal still collapses to the title alone,** as
  before.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.20.0 is the reverse.

On rollback, a deleted work item stays deleted, and its letter stays expired.

## Verification boundaries

- **Outbox:** regression tests cover Delete (confirmed and declined), its
  refusal on unresolved or pushed items, its audit entry, and a replay of an
  item older than five days getting its full schedule.
- **Breadcrumbs:**
  - A guard test refuses any screen in `netbbs.net` that spells its own path.
  - A second test walks every screen in the SysOp gallery. It fails if a
    screen drops an ancestor, names one it never passed through, or shows a
    name twice.
  - Exact-path tests cover the examples above and a set of caller walks:
    boards, categories, Community boards, file areas, Mail and the Directory.
- **Full Windows suite:** 11,869 passed, 136 skipped, and the timing-sensitive test passed.
- **Caller-side paths are only partly walked.** The gallery walk covers the
  SysOp console; the caller side has only the exact-path tests listed above.
- **Not yet checked live:** NetBSD, real terminals and multiple linked nodes
  remain manual evidence. Delete has not been run on a live node yet.
