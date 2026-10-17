# Issue #91 — wire linked-channel messages into the live chat send path — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Closes the gap issue #87's own §16 entry named: `netbbs.net.chat_flow._chat_
loop`'s message-send path now threads an optional `link_context` (mirroring
`netbbs.net.board_flow._show_board`'s own parameter for board posts exactly)
down from `browse_channels`, and calls `netbbs.link.channels.queue_channel_
message_if_linked` right after a self-authored message is locally recorded,
whenever `channel` is Linked. Fire-and-forget, no separate success/failure
message shown to the sender — the same shape `queue_board_post_if_linked`'s
own call site already established; the actual outbound push, and its own
failure handling, lives entirely in `netbbs.link.sync`'s existing background
loop, unchanged by this issue. An unlinked channel, or a session with no
`link_context` at all (Link disabled, or a caller bypassing `netbbs.net.
login_flow.handle_session`'s real wiring), behaves exactly as before —
local chat stays fully usable and Link-unaware.

Minimal threading, no broader `chat_flow` refactor: only `browse_channels`/
`_chat_loop` gained the new parameter, and only the three existing `netbbs.
net.login_flow` call sites (`[N]ew scan`, `[/] Find`, the main channel-browse
menu) needed updating to pass their own already-in-scope `link_context`
through. A real two-node end-to-end test (`tests/test_link_end_to_end.py`)
drives `_chat_loop` itself with a scripted `FakeSession`, not a direct
`queue_channel_message_if_linked` call, proving the interactive send path
specifically, per the issue's own acceptance criterion.
