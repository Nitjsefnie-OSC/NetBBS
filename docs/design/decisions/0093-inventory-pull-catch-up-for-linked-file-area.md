# Issue #93 — inventory/pull catch-up for linked file-area catalogues — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

§11.4 states the complete design and is now fully implemented: `Inventory
Request` gains a third `file_areas` key alongside `boards`/`channels`, the
identical shape issue #87 already added for channels; `netbbs.link.store.
file_area_event_diff`/`_all_file_area_events` mirror `board_event_diff`/
`channel_event_diff` exactly, unioning the same three sources (self-
originated genesis, self-authored descriptors, peer-received `link_events`
rows) those functions already established. `_handle_inventory` now shares
one overall `_MAX_EVENTS_PER_REQUEST` budget across all three diffs in
sequence (board, then channel, then file area) rather than three
independent caps.

`link_events` gains a nullable `file_area_id` column, populated at two
call sites rather than one: `netbbs.link.store.save_event`'s generic
dispatch (for `file_area_genesis`, mirroring how `channel_id` is populated
for `channel_genesis`) and `netbbs.link.files.materialize_carried_file_
descriptor`'s own direct insert (for `file_descriptor`, which — like
`board_post`/`channel_message` before it — skips `save_event` entirely).
`sync.py`'s inventory-response step also gained `max_carried_file_areas`/
`max_remote_files_per_area` parameters, threaded through from `__main__.py`
the same way `max_carried_boards`/`max_carried_channels` already are — a
real pre-existing gap, not a cosmetic one: before this issue, an inventory
response could carry an unbounded number of new file areas/descriptors
even though `LinkServer`'s direct-push path already enforced these same
two quotas (§13.9).

No restart-reconstruction changes were needed: issue #89's own `load_link_
node` work already rebuilt `node.file_areas` from both sources this
issue's diff query also reads, and `file_descriptor` has no chain state
beyond `known_event_ids`/`events` to begin with. Proven with the same two-
layer test shape issues #85/#87 established: a deterministic three-node
`ScriptedTransport` test (`tests/test_link_convergence.py`) proving the
protocol-level multi-hop mechanism in isolation, and a real-transport test
(`tests/test_link_end_to_end.py`) proving a node recovers a missed `file_
descriptor` through an intermediary while the original origin's server is
never started again for that stage — genuinely unavailable, not merely
unqueried. Chunk bytes remain outside inventory entirely and unchanged by
this issue, confirmed by both tests: a recovered catalogue entry lands
with `fetched_file_id` still `NULL`.
