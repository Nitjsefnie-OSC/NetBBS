# NetBBS v7.20.0

This release fixes several bugs in the chat bot system and its house bot, Baud,
and brings the bot screens in line with the rest of the SysOp console. It is
the result of the audit in issue #1241. **Nothing migrates:** the node database
stays at schema 130, and `BOT_API_VERSION` stays at 1. The Link, real-time,
door API, career and world versions are unchanged. Upgrading is a wheel swap
and a restart.

It is a minor release, not a patch, because two documented bot API behaviours
change. Request text is now sanitized for every bot, and `!` shorthand follows
stricter rules. Both are described below.

## Baud's reminders keep flowing (PR #1242)

Previously, one reminder that could never be delivered blocked every later
reminder of that Baud instance, for every caller and channel, until the SysOp
edited its state by hand. Typical causes were:

- the reminder's channel was renamed or deleted;
- the bot lost its grant for that channel;
- the channel's bot policy was set to *none*;
- a Link caller's text contained a tab or control character.

Each reminder now backs off on its own: one minute, doubling up to an hour.
Other due reminders are delivered in the meantime. A reminder refused eight
times is dropped.

A second bug could repeat a delivered reminder every second. This happened
when saving its removal failed. It no longer repeats.

## Bot behaviour (PR #1242)

- **`!` shorthand needs a command word.** `!help` still reaches the channel's
  default bot, but `!!!` and `! help` are now ordinary chat. A `!` line is
  also ordinary chat in MRC rooms, in channels whose bot policy is *none*, and
  in channels no enabled bot may use. Callers in those places no longer get
  "No default bot here".
- **Request text is sanitized for every bot.** Terminal controls are removed
  and tabs become spaces before a bot sees the text. This now applies to
  requests from Link callers too, which used to arrive unsanitized. Ordinary
  spacing is kept.
- **`seen` shows the node's display time,** not a raw UTC timestamp.
- **A crashing bot shows the real reason.** Before, every crash was reported
  as "invalid or oversized JSON". Now the status says, for example, "Bot
  closed its output pipe."
- **Remote bot requests always get an answer.** An unexpected error on a
  Link bot request now answers the caller at once. Before, the caller waited
  for the 75-second timeout.
- **Baud's reply to an unknown command lists `seen` and `stats`** when they
  are granted.

## Bot screens follow the console style (PR #1243)

- **Chat bots catalogue:**
  - Each row is now a table with STATE, CHANNELS and LAST ERROR columns.
  - The state is coloured by what it means: green for running, red for
    restarting.
  - A killed bot reads *stopping* until its process has exited, then *off*.
    Delete waits for exactly that.
- **Outcomes are announced.** Kill selected, Stop all, Delete (done or
  cancelled), and Save in the bot editor or Model settings each say what
  happened. Refusing to delete a running bot names `[K]ill selected`.
- **The bot editor fits an 80x24 terminal.** It was 32 rows, so its title
  and status scrolled away. Its fields are now grouped into sections that
  page. Every field keeps its old number. Switches read yes/no, and
  Temperament and Lines per hour now have help text.
- **Linked channels are marked** in the bot's channel pickers, using the same
  colour as every other SysOp list.
- **Model settings:**
  - Errors name the field you see, for example "Node daily token budget must
    be 1-1,000,000,000.".
  - Money is given in your currency units.
  - Whole numbers are checked as you type them.
- **Usage is a screen of its own.** It used to be printed as plain lines
  under the catalogue. It shows today and this month, the busiest callers,
  and the limits callers reached, in SysOp wording.

## Chat shows bot notices as notices (PR #1243)

- **Disclosures** such as "Baud.bot reads this channel" are muted `***` lines
  with the `[BOT]` badge.
- **Failures only the sender sees,** such as a bot that did not reply, are a
  muted aside in brackets. Before, they read like the bot speaking in public.
- **`/who` says a bot receives joins and leaves** only when it actually holds
  that grant in the channel.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.19.3 is the reverse.

On rollback, any reminders Baud dropped after eight refusals stay dropped.

## Verification boundaries

- Regression tests cover each fix: reminder backoff and dropping, the failed
  removal, `!` shorthand, request sanitizing and spacing, crash reasons,
  `seen` time, the catalogue table and its last error, outcome announcements,
  editor sections and numbering, Linked marking, the Usage screen, model error
  labels and notice styling. The new and changed tests fail on v7.19.3.
- The bot screens are now part of `scripts/sysop_gallery.py`, so the console
  presentation test holds them to the terminal's size.
- Full Windows suite: 11,792 passed, 136 skipped, and the timing-sensitive
  test passed.
- No automated test reaches the catch-all for unexpected errors on remote
  bot requests. Reaching it would need a two-node fault injection.
- NetBSD, real-terminal and live multi-node checks remain manual evidence.
