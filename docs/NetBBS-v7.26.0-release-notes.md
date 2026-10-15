# NetBBS v7.26.0

This release lets a SysOp put a tagline on their node's www.netbbs.org page.
It also tells callers when the MRC room list they are looking at is out of
date, and fixes deleting a Community that holds a door. Under the hood, the
SysOp console's 26,000-line module is split into one module per area. SysOps
see no change in the console.

**Nothing migrates:** the node database stays at schema 130. The endpoint
descriptor that nodes exchange in their hellos gains one optional field,
`tagline`. Older nodes verify the signature over the descriptor as received
and ignore the field. The Link, real-time, door API and bot API versions are
unchanged. Upgrading is a wheel swap and a restart, and so is rolling back to
v7.25.0.

It is a minor release because SysOps get a new setting and callers get a new
note in the MRC room picker.

## A tagline on the node page (issue #1172, PR #1303)

A node page can carry one line of the SysOp's own words under the node's
name. Set it on the console's Managed DNS screen with `[T]agline`. Enter saves,
a blank line clears it, and Esc keeps what was there. Changes are audited.

Because the line is remote text published on netbbs.org, it has a hard
shape on both sides:
- at most 80 characters;
- printable text only (no control, format or bidi characters);
- runs of spaces folded to one.

The console refuses anything else and says why. Every reader drops an
invalid tagline rather than repairing it. A peer that sends a bad one still
has its hello accepted. The page shows the tagline HTML-escaped, never as
markup or a link, and never on the badge. A node only advertises its tagline
while its page is not off.

The page generator gains `--hide-taglines FILE`. The file lists names or
fingerprints, one per line, whose tagline is left out while the page itself
stays. If a list is given but cannot be read, the build stops, so a hidden
line never comes back because the file went missing.

## MRC: the picker knows when its room list is old (issue #1283, PR #1301)

The MRC hub lists room *names* only to a user who is in a room. With nobody
from this node on MRC, the room picker shows the kept listing, which can be
up to 30 days old. The bridge now asks the hub for the network's room
*count* once a minute. It uses the documented stats query: a lone `~` on a
connection of its own, with no handshake and no nick.

- **Nobody on MRC:** when the count differs from the kept listing and the
  listing is not fresh, the picker's header says so, for example "Room list
  as of 3 h ago; the network now has 14 rooms."
- **A caller on MRC:** a count that changed since the last poll refreshes
  the listing at once, within the existing five-minute bound.
- **Elsewhere:** the SysOp's MRC status screen and the Who's online line
  show the reading with its age.
- **The hub does not answer:** nothing changes, and the poll backs off up to
  15 minutes.
- **The SysOp changes the MRC settings while a query is out:** the answer
  is dropped, so the previous hub's count is never recorded as the new
  hub's.

## Fixes

- **Deleting a Community that holds a door works** (PR #1305). It used to
  fail with `FOREIGN KEY constraint failed`, because doors were not detached
  the way boards, channels and file areas are. The confirmation's count of
  affected resources now includes doors.
- **Door authors:** the Developer Handbook now says that a polling door
  should treat a failed read of its result file as "not yet" and retry on
  the next poll (PR #1307). On Windows, a freshly renamed file can briefly
  refuse a read.

## For developers: the SysOp console in one module per area (issue #1311)

`netbbs.net.admin_flow` was 26,060 lines. It now keeps the entry points
(`admin_menu`, `staff_menu`, `staff_list_screen`), the admin, Operations and
Content menus, and the dashboard: 1,883 lines. The screens moved, unchanged,
into the following modules:

- the shared `admin_ui`, `admin_fields` and `admin_resources`;
- `admin_users`, `admin_trust`, `admin_link`, `admin_system`,
  `admin_sessions`, `admin_maintenance`, `admin_banners`,
  `admin_moderation`, `admin_boards`, `admin_files`, `admin_chat` and
  `admin_doors`.

The area modules never import `admin_flow`. It took 14 PRs, #1312 to #1325.

What changes for anyone working on the code:
- Import a screen from its module, not from `admin_flow`.
- Log lines from the moved code name their module. For example, shutdown,
  drain and maintenance-mode lines now log as `netbbs.net.admin_sessions`.
- A test patches the module whose code *calls* the name.
  `tests/test_admin_patch_targets.py` fails any `setattr`, `patch`,
  `patch.object` or plain assignment on an `admin*` module that no code in
  that module reads, because such a patch reaches nothing.

The worklog's module-ownership section records the layout.

## Decided, not built

Two design decisions are recorded in design doc §16, with no code in this
release:
- **Renaming a username** (issue #1111, PR #1304). The caller asks and the
  SysOp approves. The old name is retired and still reaches the account, and
  the rename travels on Link in the signed endpoint descriptor.
- **Link Communities** (issue #1308, PR #1309). They hold the origin's own
  linked resources, a subscription accepts members within the caps, and
  members land in a local copy.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs. The tagline
is an ordinary setting (`link_node_tagline`) and is empty until a SysOp sets
it.

Rolling back to v7.25.0 is the reverse. A tagline that was set stays stored,
but v7.25.0 neither shows nor advertises it.

**MANUAL (project operations only):** the www.netbbs.org node-page cron may
add `--hide-taglines FILE` once nodes run this release. Without it, nothing
is hidden.

## Verification boundaries

- **Full Windows suite:** 11,988 passed and 136 skipped on the release tree, with no failures,
  and the timing-sensitive test passed. An earlier run on the same tree had
  one failure under the parallel run's load, a chat input test this release
  does not touch. It passed three times out of three when run alone.
- **The console split** moved code byte for byte, which a reviewer checked
  on every PR. Each move had a full run before it merged. One run found a
  real break, which never reached `main`: the FTN network editor reached
  its helpers through `admin_flow`, and it was fixed in #1319. Every other
  failure was a load failure in an area the move did not touch, and each
  passed three times out of three when run alone.
- **The tagline** was tested in the console, through the signed descriptor
  round trip, against hostile values from a peer, and in the generator's
  escaping and hiding. No live node page shows one yet: the pages are
  rebuilt from nodes running a release that carries it.
- **The MRC room count** was tested against the fake hub, which answers the
  stats query. Its behaviour against the live hub, which is the documented
  query, has not been observed in this release's verification.
