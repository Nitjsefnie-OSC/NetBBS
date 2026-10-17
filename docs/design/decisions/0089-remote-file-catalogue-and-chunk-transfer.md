# Issue #89 — remote file catalogue and chunk transfer — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§11 states the complete design and is now fully implemented: `file_area_
genesis`/`file_descriptor` mirror `board_genesis`/`board_post` for catalogue
discovery (no content, no edit chain, no origin succession — the same
deliberate deferrals issue #87 already set for channels); chunk transfer
(`netbbs.link.file_transfer`, `FileChunkRequest`/`FileChunkDescriptor`) is
genuinely new — a direct point-to-point pull against the file's own origin,
never gossiped, never passed through `handle_events`. `remote_files` is a
new table, not a row in the real `files` table — that table's own invariant
("bytes always exist before the row does") stays true unconditionally, and
a catalogued-but-not-fetched file is a state `files` was never designed to
represent; `netbbs.files.storage`'s existing content-addressed layout is
reused once a transfer completes and verifies, so a fetched file dedups by
hash automatically, same as a local upload. `transfer_id` is deterministic
(`file_id` + this node's own fingerprint), and `chunk_id` (the chunk's own
sha256) is the exact-dedup key for a resent/duplicate chunk request —
found and fixed a real bug while writing tests: `materialize_carried_file_
descriptor` initially keyed `remote_files.file_id` off the signed *event's*
own `content_id` rather than `payload["file_id"]` (the file's actual local
identity) — two different hashes for the same object, the same distinction
`FileDescriptor`'s own docstring already calls out as the reason `file_id`
is carried explicitly in the payload rather than reusing `BoardPost`'s
"the event's content_id already is the object's identity" precedent.

Bounded per §13.5: `max_carried_file_areas`/`max_remote_files_per_area`
(carry-limit errors, tolerated the same way `BoardCarryLimitError` already
is), and `max_concurrent_file_transfers_per_peer` on the serving side,
tracked in memory only (never persisted — serving one chunk is otherwise
fully stateless). Chunk transfer is deliberately not folded into issue #60's
work-item/DLQ abstraction, the same "not every retry-shaped mechanism fits"
reasoning §13.7 already documents — it already has a natural resumable-by-
construction terminal state.

**Explicitly out of scope at the time, tracked separately and since
closed:** file-area origin succession remains untracked (no issue yet);
inventory/pull catch-up (§8.8) extended to file areas was deferred here and
closed by issue #93 (§11.4); a live SysOp/user TUI action to browse a
remote area's catalogue or trigger a fetch was deferred here and closed by
issue #92.
