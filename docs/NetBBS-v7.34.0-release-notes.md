# NetBBS v7.34.0

FTN echoes that take posts under real names only, such as nixNet's NIX_ADMIN,
can now be carried: a board that requires verified, displayed real names sends
its authors' verified real names to its echo. A SysOp can now verify their own
real name and age.

**This release does not migrate:** the node database stays at schema 148.
Rolling back to v7.33.0 is the old wheel and a restart.

**Versions:** the Link real-time protocol stays at 4, the door API at 5 and the
bot API at 1.

It is a minor release because it adds features. Nothing that worked before
changes, except that a post on a real-name FTN board needs a verified name
that fits, as described below.

## Real names on real-name echoes (PR #1444, issue #1443)

A board that carries an FTN echo and whose **Name requirement** is **Verified
and displayed**, set on the board or inherited from its Community, sends each
post with the author's verified real name as the From name. The same account's
posts on other echoes, on Link and in chat keep the username.

- **No usable name, no post.** If the author has no verified real name, or it
  is longer than FTN's 35-byte From field, the board reads `Read only` with the
  reason in place of `[P]ost`, and the cross-post picker marks the board.
  Writing the post refuses it too, so replies, cross-post copies, bots and
  doors meet the same check. This applies to a SysOp as well, although a
  SysOp skips the board's own name requirement.
- **Never sent under the wrong name.** A post that still reaches the exporter
  without a usable name, for example because its author's verification was
  revoked while it waited for a moderator, stays on this BBS with a warning in
  the node log. Nothing goes out under the username or cut short.
- **The 35 bytes are counted** in the character set the message goes out in.
  A name with letters such as `é` can take two bytes each when the post needs
  UTF-8.
- **Replies** to a local post on such a board are addressed to the parent
  author's real name, matching what the network saw.
- **Netmail to a verified real name** now reaches its member. Usernames and
  old usernames are still matched first, then verified real names, exactly
  and ignoring case. A name no account has, or one two accounts share, goes
  to the SysOp with the usual note.

The SysOp Handbook's new "Real-name echoes" section walks through setting up
an echo such as NIX_ADMIN.

## A SysOp can verify themselves (PR #1445)

A local SysOp now finds their own account in the main menu's
<kbd>V</kbd> **Verify** list and can verify their own real name and age. The
account's **History** marks the entry `(self)`. Staff with "Can verify
identity" still verify other people only, and NetBBS refuses their own account
even outside the menu.

A self-verification is never shared over Link: another BBS couldn't tell it
from someone else's check. The Profile shows sharing as
`off (you verified it yourself)`.

## Upgrade and rollback

- **Upgrade:** install the wheel and restart. Nothing migrates.
- **Rollback to v7.33.0:** install the v7.33.0 wheel and restart. A post on a
  real-name echo then goes out under the username again, and a SysOp's
  self-verification stays on record but can no longer be added.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,780 passed, 136 skipped, none failed (20:22); the timing-sensitive test passed too.
- The real-name path has not yet been tried on nixNet itself; the next
  NIX_ADMIN post from ReLink is that check.
