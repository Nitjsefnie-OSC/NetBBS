# Issue #639 — what expiry means to a caller — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Found when issue #638 made the file area keystroke-only and took
`/download <filename>` with it, which was the last path to a file that had
expired. The contract of `list_files_page` promised expired files were
"delisted from normal browsing though still individually reachable", and
`get_file_by_name` kept returning them, so the promise survived its only
surface. Normative description: §5.3.

Two things found while deciding it, which changed the shape of the answer:

- **`posts.py` makes the same promise and never had a surface either.**
  `get_post`'s contract says "expired content stays individually reachable,
  only delisted from normal browsing", and `netbbs.net.board_flow` contains
  no reference to an expired post and no call to `get_post`. So this was not
  a regression #638 introduced in file areas; it was one sentence written in
  two subsystems, of which files happened to hold an accidental instance.
- **`get_file_by_name` had no production caller left.** Every remaining
  mention was a docstring in the past tense. The promise was being kept by a
  function nothing called.

**Decision 1 — expiry ends a caller's reach, in both subsystems.** A caller
may rely on this: expired means gone. Rejected: a `show expired` toggle on
the file listing, and returning expired rows from `[/] Find` labelled. Both put
delisted content back in front of callers, which is the one thing expiry
exists to stop, and the second costs the most to build — the expiry sweep
calls `reindex_file`, which *deletes* a row from `file_search` as soon as it
stops being `approved`, so search would need a status dimension carried
through every consumer in order to re-expose what it had just removed.

**Decision 2 — the domain keeps its unfiltered lookups, for two named
consumers.** Reply-parent resolution needs an expired thread to resolve, and
SysOp recovery needs an expired file's row to reach its bytes. What changes is
the contracts, which stop describing either as something a caller can do.
Rejected: filtering expired rows out of the domain, which would break replies
to a thread that expired mid-conversation.

**Decision 3 — the SysOp recovery surface is a file-area screen, not a
caller-facing one.** Expired files are listed on the file area's admin detail
screen with `[D]ownload`, mirroring the pending-file review screen #638
already gave that action to, and offered under the same transport rule
(issue #475).

**Decision 5 — `get_file_by_name` is deleted.** Decision 3 was first expected
to give it a production caller again. Building the screen showed it does not:
the recovery screen is a picker, which hands over the row the SysOp chose, and
#638 had already moved `send_file_to_caller` off re-reading a file by name
because a filename is not unique within an area. Routing recovery through the
by-name lookup would have handed back the oldest row of that name, possibly an
approved file rather than the expired one picked. With no caller the function
went, rather than stay as another implemented, tested and unreachable name. Its
pending rule lives on where it is enforced: `list_pending_files` and the
transfer path.

**Decision 6 — a transfer link does not outlive expiry.** A download link
minted while a file was listed is refused once the file expires, its uploader
included, the same way an outstanding link does not outlive a lockout. It
serves an expired file only to a holder of `APPROVE` on the area, which is who
the recovery screen mints it for.

**Decision 4 — posts get no equivalent screen.** A file is an artifact that
is unrecoverable once the grace period ends; a post is text in a board. The
recovery case that justifies decision 3 does not arise, and inventing a
screen for symmetry would be building for nobody.

**Not done, deliberately.** `test_expired_file_still_reachable_by_name` stays,
renamed `test_expired_file_keeps_its_row_until_purged` and pinned through
`get_file` now that the by-name lookup is gone, since the domain behaviour it
guards is still true and still load-bearing: what was wrong was the promise
about callers, not the return value. The recovery screen has no restore
action; a re-upload is how a SysOp puts a file back. The §3.5 bullet recording #638's trade also
cited §11 for the delisting rule, which §11 does not state; it now points at
§5.3, which does.
