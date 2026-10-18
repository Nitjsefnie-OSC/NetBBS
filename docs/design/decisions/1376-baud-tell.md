# Issue #1376 — Baud `!tell`: a note delivered on the next join — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

BBS callers rarely overlap. Chat is where people meet, but two regulars who
call at different hours never find each other there. Mail connects them, but
it is a heavier act and does not carry "say this in the channel when you see
them". `!tell <name> <message>` is the long-standing IRC-bot answer.

What existed: the `seen` query takes a local account only and refuses `@`
names. `live_message_refusal` answers the recipient's direct-message opt-out
and then the §6.4 block list, for a local sender and for a Link sender as
`user@<fingerprint>`; a SysOp cannot be blocked, and a blocked live sender is
told "<name> does not accept messages from you". The `!tell` line itself is
public: an addressed request is an ordinary chat line, recorded and broadcast
before the bot sees it, so the channel already saw the note's text. Baud
cannot read accounts or blocks; any such check has to happen in the host.

**Decision 1 — a tell is a host-held line bound to the request token.**
- It uses the shared "say for a caller" action of #1375. Baud sends it with
  the request's token, so the host takes the sender from its own pending
  request, never from the bot. Tells do not use Baud's state, which
  reminders already fill.
- **Delivery:** on the recipient's next join of the channel the tell was made
  in, the host publishes it there:
  `[BOT] <Baud.bot> Nib: Quill asked me to tell you (3h ago): check the Retro board`.
  The note was said publicly in that channel already, so delivering it there
  reveals nothing new to that audience. Help and the confirmation say where
  and how it will be delivered.
- **The recipient is here now:** Baud answers "Nib is here now." and stores
  nothing.
- Rejected: **Baud's state plus a token-bound check query:** a block made
  after the tell could not be rechecked at delivery without trusting the
  bot's claim of who the sender was. Rejected: **a real letter plus a join
  notice:** it turns a one-liner into mail, needs a bot-sends-mail grant (an
  impersonation-shaped power), and is the silent "fall back to mail" that
  §6.3 avoids for live messages.

**Who may be told, and by whom.**
- **Recipients are local accounts only,** keyed by account id so a rename
  between tell and delivery changes nothing. This follows `seen`, and the
  block list is known only for local accounts. `!tell` to an `@` name is
  refused with the reason.
- **Senders** may be local callers or Link callers in a Linked channel: the
  token carries the `link:<fingerprint>:<user>` identity, and blocks by
  `user@<fingerprint>` exist. A Link sender whose node is no longer allowed
  to author here at delivery time (`decide_user_authorship`) has the tell
  dropped, as `run_query` rechecks a remote caller.
- **A recipient who cannot enter the channel** is refused at creation, since
  the tell could never be delivered.

**Blocks and refusals.**
- §6.4's block list and the direct-message opt-out apply through
  `live_message_refusal`, at creation and again at delivery.
- At creation a blocked or opted-out sender is told "Nib does not accept
  messages from you", the live-message wording.
- A block made after the tell drops it silently at delivery, the way
  `/private` rechecks each line.
- A recipient refuses tells through the existing direct-message opt-out; no
  new setting.

**Decision 2 — 3 per sender, 5 per recipient, 32 per bot, 14 days, 160
characters.**
- At most 3 waiting tells per sender, 5 per recipient and 32 per bot
  instance. The per-recipient cap stops a pile-on against one person.
- A tell waits at most 14 days and is then dropped silently: the recipient
  never came, and the sender can see it as waiting with `!tells`.
- The text is 1 to 160 characters, the reminder cap.
- **`!tells`** lists the caller's own waiting tells in this channel, and a
  waiting tell can be cancelled by its sender.
- Rejected: **telling the sender of delivery or expiry:** it needs a second
  held line for the sender, which private answers (#1372) would make
  possible later. Rejected: **a per-sender cap only:** one recipient could be
  flooded by many senders.

**Private delivery.** The shared action takes a private flag once #1372 is
built (#1375). Tells are delivered publicly, as the chosen mockup shows;
switching Baud's tells to private delivery is left for a later decision.

**Implementation notes.** The host table is #1375's, with the sender's
identity kept for the block recheck; the caps are counted there. Harassment
through tells is held off by the per-recipient cap and the block list, and
channel moderators can `/mute` the bot.
