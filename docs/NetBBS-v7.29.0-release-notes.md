# NetBBS v7.29.0

This release lets a SysOp greet every new account with a letter of their own.
Chat now marks the New Year and says when the clocks change. Long paged
answers in chat also read as one block when you scroll back.

**Nothing migrates:** the node database stays at schema 134. The welcome
letter keeps its settings in the existing node settings table, and each copy
is an ordinary letter. The Link, real-time, door API and bot API versions are
unchanged. Upgrading is a wheel swap and a restart, and so is rolling back to
v7.28.0. A rolled-back node simply stops sending the letter, and letters
already sent stay in their mailboxes.

It is a minor release because SysOps get a new setting and chat gets new
announcements.

## A welcome letter for new accounts (issue #1352, PR #1353)

**Settings ▸ Mastheads & banners ▸ Session banners ▸ New-account letter.** You
write the letter once, and every new account finds it unread in its mailbox.

- **When it arrives:** as soon as the account can sign in. That is right away
  when anyone may sign up (Telnet, the web or SSH), when you approve the
  account if signups need approval, and when you create the account on the
  console. Existing accounts never get it, and no account gets it twice.
- **Who it's from:** a real account, so a reply reaches a person. Whoever
  switches the letter on is the sender until you pick another SysOp or staff
  account with <kbd>F</kbd> **From**.
- **What it says:** <kbd>E</kbd> **Edit text** opens it in the mail editor,
  and <kbd>S</kbd> **Subject** changes the subject. `{user}`, `{node}` and
  `{date}` are filled in for each new account. Each letter is a frozen copy,
  so changing the text affects only letters still to come.
- **Starting out:** it is off, with a sample text to rewrite. <kbd>P</kbd>
  **Preview** shows it as a new caller will read it, and <kbd>O</kbd>
  **On/off** switches it.
- **If a letter can't go:** the account is created anyway. If the sender has
  lost its staff access or can no longer write mail, the screen and the log
  say what went wrong and when. The warning clears with the next letter that
  goes out.

Approving a pending account on the console now also says "The welcome letter
is in their mailbox." when it sent one. Every change to the letter is in the
audit log.

## The New Year and clock changes in chat (issue #1354, PR #1355)

The midnight message in chat has two new variants:

- **On January 1st** it reads "Happy New Year, chatters! A new year has just
  begun: where this node lives, it is now Friday, January 1st, 2027."
- **When the clocks change**, it posts at that very moment, for example
  "Clocks just went forward an hour: where this node lives, it is now 03:00
  CEST." or "Clocks just went back an hour: ... it is now 02:00 CET."

Clock changes follow the node's own display timezone, so a node in New York
says EDT and EST, and a node in a zone without daylight saving never posts
one. Like the daily message, both go only to channels with someone in them,
stay on this node, and are kept in chat history. In a zone whose clocks change
exactly at midnight, such as Santiago, the day's message comes first and the
clock change right after it.

The announcer also can no longer announce the same midnight twice if its
timer wakes a moment early.

## Fix: a paged answer reads as one block in chat history (PR #1351)

v7.28.0 removed the stale `PgDn more` lines from chat history, but each later
page's heading (`── MRC hub · /mrc BBSes ── 10/11 ──`) was still kept, so
scrolling back over `/mrc bbses` showed the list in pieces. Now only the first
heading stays, above every row. Chat's `/help` had the same problem, with a
`More [1/3]` prompt and a new heading for every page. It now keeps one "Chat
commands" heading. What you see while paging is unchanged.

## Upgrade and rollback

- **Upgrade:** install the wheel and restart. No migration. The welcome letter
  stays off until you switch it on.
- **Rollback to v7.28.0:** the previous wheel and a restart. The letter stops
  going out, and its settings stay stored for when you upgrade again.

## Verification boundaries

- The full test suite ran on Windows against the release tree: 12,307 passed, 136 skipped, none failed (27:39); the timing-sensitive test passed too.
- The welcome letter is tested through open registration on Telnet/web, an
  approval on the console and an account created on the console. The SSH
  registration path calls the same delivery but has no end-to-end test of its
  own for the letter.
- The clock-change times are tested against the tz data for Europe/Berlin,
  America/New_York, America/Santiago, Asia/Tokyo and Australia/Lord_Howe. They
  have not been watched happening on a live node; the next one in Europe is
  2026-10-25, when clocks go back.
