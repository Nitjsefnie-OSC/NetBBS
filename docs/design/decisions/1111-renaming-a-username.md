# Issue #1111 — renaming a username — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Nothing renames an account today, and the code says so in three places:
`local_user_id` is "the account's canonical, immutable, stored-case username"
(§7.3). A username is three things at once. It is the login name. It is the
account's identity on NetBBS Link, where `local_user_id` *is* the username and
nothing else stable per user goes on the wire (§4.5: password-only accounts
have no personal key). And peers key a remote user's trust subject, mail
blocks and mail threads on it. Locally the account is an integer id almost
everywhere. The exceptions are chat scrollback's `author_label`, presence, the
chat hub, session checks that re-read the account by name, Link mail and FTN
netmail delivery, and the MRC nick. So the hard part is the network, not this
node.

**Decision 1 — a caller asks; the SysOp approves.** A caller requests a new
name from their own profile. The request is checked exactly as
self-registration checks a name (`self_service_username_problem`: grammar,
staff titles, taken, retired, and the #835 look-alikes of a SysOp). It waits
in a queue beside pending registrations. The SysOp approves it or refuses it
with a reason the caller sees on their next login. A SysOp can also rename an
account directly from its detail screen, for the case that does not start
with the caller: a name chosen to offend, or a support request by mail.
Rejected: caller self-service, which makes a rename a way to walk away from a
block or a reputation without anybody deciding it should be; and SysOp-only,
which turns every request into an e-mail. Limits: one open request per
account, one approved rename per account per 30 days (a SysOp's own rename is
not limited), and every step audited (`request_username_change`,
`refuse_username_change`, `rename_user` with the old and new name).

**Decision 2 — the old name is retired, like a deleted account's.** The
rename writes the old name into `retired_usernames` in the same transaction,
under #594's rule (a node that has ever run Link). The #594 entry says why:
peers still send to that name, and whoever held it next would receive it. The
SysOp's existing release action still applies. The retired row also records
the account it now belongs to. So Link mail, FTN netmail and a typed `/msg` to
the old name reach the renamed account here, instead of bouncing. A
retirement that came from a deletion records no account and bounces as
before.

**Decision 3 — history keeps the name it was written under.** Posts, files and
sent mail already store the author's name as a snapshot beside the account
id. They keep it: it is what was signed and carried, a post's content id is
derived from it, and every peer's copy says the same. What follows the account
is everything that looks the account up live: Who's online, the directory,
ownership of one's own posts, mail recipients, presence. Chat scrollback is
the one table with no account id. It gains `author_user_id`, written for
every new line, so a line still resolves to its author after a rename and
never to a later holder of the name. The migration backfills older lines by
name only where that is safe: a line is given to the account that holds its
name now only if that account already existed when the line was written. A
line older than the account was written by a former holder of a name that was
freed and re-registered (§4.3 allows that on a node that never ran Link, and
#594 Decision 5 leaves earlier reuse unrepaired). That line keeps its stored
name and resolves to nobody. Rejected:
rewriting snapshots, which would make this node's copy of a signed post
disagree with every peer's copy.

**Decision 4 — a rename is announced on Link, in the node's signed
descriptor.** The node lists its recent renames in its endpoint descriptor,
as `user_renames`: `{from, to, at}` entries, at most 64, none older than 400
days, newest first. The descriptor is what every peer already receives, from
every hello and every introduction, so the announcement reaches each node that
holds anything about the user without a new route. Rejected: a trust-object
style pull, which reaches only peers that chose this node as a reporter
(§12.7), and a flooded content event, which §12.7 rules out for non-content.
The bounds keep the descriptor small. A peer that has not heard from this node
in 400 days misses the notice and meets the new name as a new user, which is
today's behaviour.

A peer **keeps** what it learns, in its own alias table keyed by node
fingerprint and old name. It does not re-read it from the descriptor each
time, because entries age out of the list. From then on it treats
`new@node` as the same user as `old@node`:

- **Trust** resolves through the alias. The new name's subject takes the old
  subject's overrides, quarantine and block states, signals, vouches, activity
  days and probation age. A rename neither launders a block nor resets
  probation.
- **Mail blocks and refusals** recorded against the old address also cover the
  new one. A reply to a letter from the old address goes to the new one.
- **Content signed before the rename** keeps showing the old name (Decision
  3). A peer's view of the user, such as a profile, a block list or a trust
  screen, shows the current name with "formerly" and the old one.

**Decision 5 — a peer accepts a rename only when nothing about it is new
power.** A home node already speaks for its users, so letting it rename them
grants nothing it lacks, with two exceptions, and both are refused. A peer
ignores an entry whose new name it already holds as a separate subject with
history from *before* the entry's `at`: that would merge two users into one.
History from `at` onwards is the renamed account's own. It is signed by the
same home node, and it can reach a peer before the notice does, through
another route or a peer still holding the older descriptor. An honest rename
is therefore never refused for being noticed late. A node could backdate `at`
to slip past this test. So when both names already carry trust state, the
merged subject keeps the stricter one of each kind (a block or quarantine
on either side stays), and a rename can never lift a restriction.
It also ignores an entry that renames to a name the same node renamed away
from earlier: names are retired at home, so an honest node never does this.
After a peer has learned `old → new`, content newly signed as `old@node` is
refused as a retired identity. Chains resolve transitively, A → B → C, and a
cycle cannot form under these rules. Each refusal is logged for the SysOp, not
shown to callers.

**Decision 6 — the rename takes effect at once, and live sessions are told.**
An approved rename ends the account's live sessions with a line saying the
username is now `new` and to log in with it. Every session check re-reads the
account by name, so a session that carried on would read as deleted. Ending it
on purpose says what happened. The rest follows the account on its next
appearance:

- **MRC:** the nick changes the next time the caller is announced.
- **Doors:** War Dialer refreshes its handle at the next login. Voidrunner's
  callsign is the player's own in-game choice and stays.
- **Attestations:** the remote attestations this node issued about the
  account are revoked and reissued under the new name by the existing
  reconciliation.

The confirmation names the one thing outside the node's reach: an RLogin
remote door that keys its players on the handle sees a new player.

**Not done, deliberately.** A personal-key identity (§4.5) would make the
name a label rather than an identity. It is a separate, larger tier. Display
names (#1110) and chat aliases were never identities and are unchanged. FTN
networks and the MRC hub have no rename concept; mail written there to the
old name reaches the account here through the retired row (Decision 2), and
nothing more is attempted. Implementation is its own issue.
