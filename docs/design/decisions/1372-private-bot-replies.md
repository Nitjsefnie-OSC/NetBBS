# Issue #1372 — private bot replies — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Every bot answer was public. Baud's `reply` put the caller's name in front
and wrote one line into the channel, and its help ended "Replies are public."
That one constraint capped what any bot could be: no personal answers, help
crammed into one line, `reminders` limited to the current channel so it could
not reveal a reminder from a private channel, and model conversation public
by necessity.

What existed: a request is bound to a host token that records the requester's
channel and participant id (`BotRuntime.enqueue`), and `reply` takes its
channel from the token and consumes it. A private path to the one requesting
session already existed for refusals, timeouts and model errors
(`hub.send_to(..., BotNotice(..., private=True))`, drawn as a muted aside
since #1241), and over Link `bot_result.error` reaches the requester the same
way. The request itself was public: the addressed line is recorded, queued for
Link, broadcast and observed by channel readers before the bot sees it. The
`bot_result` payload must be exactly `{request_id, error}`, so a new field is a
malformed frame to an older `chat_bots_v1` peer; `chat_bot_callers_v1` set the
precedent of a sibling capability that adds an optional field.

**Decision 1 — a private answer is a bot line marked "(to you)".**
- **The look:** a timestamped bot line with the `[BOT]` badge and the body in
  the chat body colour, with `(to you)` after the speaker so it is not taken
  for public speech:
  `[20:14] [BOT] <Baud.bot> (to you) #1 lobby: check the oven`. Nobody else
  sees anything.
- **No bell.** It answers the caller's own question, as the MRC hub's
  answer does. The bell's private level is for messages from people.
- **Kept like `/msg`:** in the session's kept lines only, so PgUp and a
  redraw show it. It is never written to `channel_messages`, never signed,
  and is not in search or join replay (§6.3, "`/msg` and `/private` remain
  ephemeral").
- **A private block** (#1373) carries `to you` in its title's tail instead.
- Rejected: **the muted aside** used for refusals: an answer is content, and
  muted text without a timestamp reads as an error. Rejected:
  **private-message styling:** "message from" invites a reply and suggests
  someone wrote to you. Rejected: **always a titled block:** heavy for
  "Saved reminder #4."

**Decision 2 — a caller can ask privately: `/msg Baud.bot <text>` and a
`/private Baud.bot` mode.**
- **`/msg Baud.bot <text>`** sends one request privately. The request is
  never recorded, broadcast, sent over Link as a channel line, relayed to MRC
  or observed by readers. The host forces the answer private: it refuses a
  public `reply` on such a token.
- **`/private Baud.bot`** enters a private conversation with the bot, as
  `/private <user>` does with a person: every plain line is a private request
  to the bot until `/close`. It is handy for model conversation and help-desk
  questions (#1382). It is a third kind of `/private` target beside a local
  and a Link user, and ends the same ways (`/close`, leaving the channel).
- **The channel context** of a private request is the caller's current
  channel, so the bot must be allowed there and the per-channel grants, mutes
  and `/bots` policy apply exactly as for a public request. A bot not
  available in the channel is refused with the reason, as `/msg` to someone
  offline is.
- **Remote bots:** `/msg Baud.bot@Vertigo <text>` and `/private
  Baud.bot@Vertigo` send `bot_request` with a private flag, under the new
  capability (Decision 5).
- **No bot-to-bot path:** a private request is a live human input like any
  addressed line, so the rule that bots never trigger bots is unchanged.
- Rejected: **answer only,** where the request stays a public line: a
  question asked in front of the channel is still public, which is half the
  issue.

**Decision 3 — private answers are implied by `addressed`.** A private
answer publishes less than a public one. No new grant, no editor row and no
migration for it. Mute, the kill switches, channel policy and, on a receiving
node, `/bots local|none` still apply.
- Rejected: **a grant of its own, off or on by default:** a column and a Save
  confirmation for an ability that reveals less than the one it extends.

**Decision 4 — private answers have their own per-caller limit, 30 an
hour.**
- They make no noise in the channel, so they never debit the channel's
  `lines_per_hour`. Each bot instance instead allows each caller at most 30
  private answers an hour, a fixed number.
- A privately asked request is checked against that limit before dispatch,
  not against the channel's. A publicly asked request is still checked
  against the channel's limit before dispatch, since its answer may be public;
  if the bot answers it privately, the per-caller limit is debited instead.
- A limit that is hit is said, never shown as silence (#1208 Decision 7).
- Rejected: **the channel limit:** a busy channel that spent its budget would
  also block private help. Rejected: **no limit:** a caller could drive a
  rule-based bot without bound.

**Decision 5 — over Link, an extended `bot_result`, never a direct message
or a channel event.**
- A new capability, working name `chat_bot_private_v1`, allows an optional
  private answer object in `bot_result` (bounded like a reply) and a private
  flag in `bot_request`. It is never sent to a peer that does not advertise
  it.
- `bot_result` is already bound to the request id and the expected host and
  reaches only the requesting participant. `direct_message` was rejected: it
  would let a bot host message any caller unasked, fans out to every session
  and the mailbox, rings, and is blocked by the human live-message opt-out.
- **Never a fallback to public.** If the requester's node lacks the
  capability, the private action is refused with a reason, and the bot may
  then reply publicly. The `message` event tells the bot whether a private
  answer can reach this caller (`"private": true|false`), and whether the
  request was asked privately.
- On the requesting node a remote bot's private answer is checked against
  the local mute and `/bots local|none` before it is shown.

**The action.**
- A private answer consumes the request token as `reply` does: one request
  gets one answer, public or private. Expiry, the 32 pending requests and
  the restart notices are unchanged.
- It goes to the requesting session only. If the caller has left the
  channel, it is dropped, matching the online-only `/msg` rule and today's
  refusals.
- Text rules are a reply's: sanitized, 1 to 500 characters, no controls,
  checked by the host.
- **Model conversation:** a private answer enters the caller, bot and
  channel context as a published one does; the rule "only successfully
  published replies enter context" becomes "delivered replies".

**Queries.** No existing query changes: `stats` and `seen` stay safe for
public replies, and `seen` keeps respecting hidden session history. The host
cannot stop a trusted program from repeating a result in public (#1208
Decision 4), so a future private-only query, such as a caller's own activity
summary (§6.6), is served only on a privately asked token, with a grant
warning that says the program sees it. The issue that adds such a query
decides it.

**Baud.** Once private answers exist, `reminders` lists all of the caller's
reminders privately, across channels; `help` answers privately; and the help
line "Replies are public." is reworded.

**Implementation notes.** One migration, for the per-caller private-answer
count. The privately asked path must leave the request out of
`record_message`, the Link queue, the broadcast, `observe` and the MRC relay
at one seam, so a private question cannot leak into observations.
