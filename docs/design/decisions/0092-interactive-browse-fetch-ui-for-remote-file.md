# Issue #92 — interactive browse/fetch UI for remote file catalogues — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Closes the UI gap issue #89 left open: `netbbs.net.file_flow._show_area`
gains a remote-catalogue screen (reachable from both the ordinary paginated
file listing and the "has no files yet" action bar, since a Linked area can
have remote catalogue entries with zero *local* uploads of its own),
offered whenever an optional `link_context` is given — threaded down from
`enter_file_area`/`browse_file_areas` the same way `netbbs.net.login_flow`'s
board/channel paths already thread it. It lists every catalogued
`RemoteFile` for the area via `netbbs.link.files.list_remote_files`
(fetched and not-yet-fetched alike, clearly labeled, so a user can tell
"catalogued and I already have it" from "catalogued and I don't" at a
glance — no separate hidden state); picking an already-fetched entry
reports that and stops, never re-offering a redundant fetch. Picking a
not-yet-fetched entry, after a yes/no confirmation, drives `netbbs.link.
transport.fetch_next_file_chunk` in a loop until the transfer completes,
fails, or the origin turns out unreachable (`dialable_base_urls_for_peer`,
new — chunk transfer is never relayed, so an origin with no advertised
direct address is reported clearly rather than attempted). Success is
reported once the transfer's own existing verification/promotion path
(unchanged by this issue) has already placed the content in the ordinary
local `files` table; the file is then reachable through the listing's
pre-existing download keys like any other, no new download path introduced.

No per-file access check inside the catalogue screen beyond what already gated
entering `_show_area` in the first place (design doc's own "merely knowing
a descriptor exists must not bypass local policy" acceptance criterion) —
a `RemoteFile` carries no independent moderation state of its own the way
a pending local upload does, so the area-level read/age/name-requirement
gate already enforced by whichever picker offered the area is sufficient.

`aiohttp`/`netbbs.link.transport` are imported lazily, inside the one
function that actually dials out (`_fetch_remote_file`) — `netbbs.net.
file_flow` is loaded unconditionally by every node, including one with
`aiohttp` not installed, matching the same lazy-import convention
`netbbs.__main__`'s own Link-server startup already established.

A full interactive-flow regression test (`tests/test_link_end_to_end.py`)
drives `_show_area` itself against a real second node — the
`[L]ink catalogue` key, `pick_item` selection, the fetch confirmation prompt
— proving browse -> fetch -> verify/promote -> ordinary download visibility
end to end, per
the issue's own acceptance criterion; `tests/test_file_flow_remote.py`
covers the UI-level edge cases that don't need a real second node (no
catalogue entries, an already-fetched entry, a declined fetch, an
unreachable origin).
