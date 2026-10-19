# NetBBS v7.33.0

Tab now completes bot names after `/msg` and `/private`, so asking a bot
privately is as quick to type as messaging a person. This release also fixes
a Tab slip in chat that could erase what you had typed.

**This release does not migrate:** the node database stays at schema 148.
Rolling back to v7.32.0 is the old wheel and a restart.

**Versions:** the Link real-time protocol stays at 4, the door API at 5 and the
bot API at 1.

It is a minor release because it adds a feature. Nothing that worked before
changes.

## Bot names in Tab completion (PR #1440)

After `/msg ` or `/private `, <kbd>Tab</kbd> offers the bots that would answer
you in this channel, after the people who are online:

- **Bots on this BBS** that are running and may be addressed here, the same
  ones `/who` lists.
- **Bots on other BBSes** in a linked channel, written `Name.bot@Node`. Only
  bots whose BBS takes private questions are offered, and a node name with a
  space is quoted, for example `Eliza.bot@"Cats Den"`, so `/msg` reads it
  back as one address.
- **Never offered:** a bot you muted, a bot the channel's `/bots` setting
  hides, and a stopped bot.

`/whois`, `/finger` and `/invite` still complete people only.

## Fix: Tab could erase the word you typed (PR #1440)

When the names Tab offered started with the same letters but in different
case, for example `bob` and `Baud.bot` after `/msg b`, Tab replaced the typed
word with their common start, which was empty. Now Tab keeps what you typed
unless the common start still reads as that word, ignoring case. This applies
everywhere Tab completes in chat, not only to bots.

## Upgrade and rollback

- **Upgrade:** install the wheel and restart. Nothing migrates.
- **Rollback to v7.32.0:** install the v7.32.0 wheel and restart.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,751 passed, 136 skipped, none failed (11:46); the timing-sensitive test passed too.
- **Known gap:** when two linked BBSes have names with a space that share a
  start past the space, and both host a bot of the same name, Tab completes up
  to the space and then offers nothing more. Type the rest of the name.
  Nothing is sent to the wrong bot.
