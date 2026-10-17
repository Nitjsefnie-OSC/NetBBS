# Issue #166 — FidoNet/BinkP gateway — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The issue scoped an FTN gateway as a hedge against Link's adoption: FidoNet
and its othernets are populated networks today. The maintainer decided the
design on 2026-10-07. Normative description: §6.8.

**Research.**
- **Prior art.** Every current BBS package runs its FTN mailer and tosser
  inside the BBS:
  - Synchronet: SBBSecho and BinkIT;
  - Mystic: MIS and MUTIL;
  - ENiGMA½: a native BinkP 1.1 mailer;
  - Talisman: Postie and Binki;
  - ANetBBS: Python, MIT.

  The external binkd + hpt combination survives on older Linux setups. Python
  has no maintained FTN or BinkP library: python-ftn is GPL-3 and archived,
  and no Python BinkP implementation exists outside ANetBBS. ANetBBS keeps
  its queue in the database rather than in BSO directories, as this design
  does.
- **The usual on-ramp** for a new BBS is fsxNet (zone 21). A node applies by
  email. A node that only polls, with mail held at the hub, is accepted. Its
  infopack forbids gating fsxNet traffic to another network without the zone
  coordinator's approval.

**Decision 1 — native BinkP, not an external mailer.** The node speaks
binkp/1.0 with CRAM-MD5 and NR in-process. Rejected: binkd with BSO
directories, which means a second program with its own configuration and
passwords for the SysOp to keep in step. It is also awkward on Windows.

**Decision 2 — the first version answers calls as well as polling.**
Rejected: poll-only first. Answering needs an open port, so it stays a
per-network switch, but hubs deliver crash mail sooner to a node that
answers, and a node this one netmails directly (Decision 5) can only answer
the same way if this node takes the call.

**Decision 3 — a board is local, Linked or FTN, never two.** Rejected: both,
opt-in behind a warning. fsxNet's gating rule forbids carrying its traffic
onto Link, and Link would sign FTN posts as this node's own
(`queue_board_post_if_linked`). Keeping them apart keeps the gateway at the
boundary, as the issue asked.

**Decision 4 — full caller netmail, in Mail.** Rejected:
- AreaFix only;
- AreaFix plus netmail for the SysOp alone.

Netmail is personal mail with an FTN address. The To line takes
`Name (zone:net/node)`, the form an FTN author already has on boards, since
`@` names a Link identity. Rejected: a separate Netmail item with its own
folder, which splits one inbox into two.

**Decision 5 — netmail goes direct when it can.** The node processes the
network's nodelist and calls a destination that lists a BinkP host. Anything
else, and a failed direct call, goes to the uplink. Rejected: everything via
the uplink. That needs no nodelist, but it is hours slower, through every hub
on the way.

**Decision 6 — sending netmail needs a minimum level, which starts at SysOp
only.** Netmail leaves under the node's address, so the hub holds the SysOp
answerable for it. Rejected: every account by default.

**Decision 7 — netmail to an unknown name goes to the SysOp.** This is the
usual FTN practice, and nothing is lost to a typo. Rejected: bouncing it to
the sender, which loses a mistyped letter and can loop with a misconfigured
node.

**Defaults set alongside, without a separate question:**
- one echo area per board, off by default, as MRC rooms are;
- FTN authors shown as `Name (address)`, never trust-evaluated, through the
  board's own moderation;
- FTN dates kept;
- duplicates dropped by MSGID;
- SEEN-BY and PATH kept, and no re-export of FTN-sourced posts;
- CP437 when the text fits, UTF-8 otherwise, named by `^ACHRS`;
- file echoes, file requests, hub mode and QWK out of scope.
