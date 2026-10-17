# Issue #201 — managed netbbs.org subdomain + dynamic DNS — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

**Goal:** since the project controls the `netbbs.org` domain, offer SysOps
an easy way to publish their board under it (e.g. `myboard.netbbs.org`)
and, for boards on residential/dynamic IPs, keep that record pointed at
the node's current address without manual DNS maintenance. Two
components, not one: a one-time subdomain *registration* (name
reservation + initial DNS record), and a recurring *updater* that keeps
the node in contact with the service and, for a board that asks for it,
pushes a record update when the node's public address changes. A board
on a static IP still wants the friendly subdomain without the address
tracking, and that is the `dynamic` choice at registration; what it
cannot opt out of is the contact itself, which is the liveness signal
every managed name owes the service (Decision 10 — this paragraph used
to call the updater optional, and issue #600 found the code had never
agreed).

**Decision 1 (locked in) — offered via a prominent prompt on first-SysOp
bootstrap or first authenticated SysOp login, not a silent default and
not a toggle a SysOp has to go discover.** Two different concerns were
in tension here and both are real: a bare opt-in-only design loses most
of the feature's actual value (the whole pitch is removing first-run
friction — a setting nobody discovers might as well not exist), but a
silent default also isn't right, because this makes the node contact and
register public presence with project infrastructure before the SysOp
has decided whether they want that at all — a private/test node would
get unexpectedly enrolled. This is the same *kind* of decision as Link
participation itself, which already isn't automatic on a fresh node
(seeds must be configured before a node reaches out and joins the mesh)
— for internal consistency, "does my node touch external infrastructure
and become discoverable" should stay an explicit choice here too, just
asked at the moment it matters instead of requiring discovery later.
Code review follow-up (PR #218): a supported persistent deployment
bootstraps its first SysOp via `netbbs.admin` and then runs headlessly
under systemd/rc.d — a literal "first daemon run" prompt has no
interactive input channel at that point and would either block startup
or silently be skipped. The prompt is anchored to an existing
interactive surface instead — first-SysOp bootstrap, or that SysOp's
first authenticated login if bootstrap itself stays a non-interactive
CLI invocation — with the accept/decline answer persisted so it is
asked exactly once, not on every subsequent login.

*Default flipped by issue #219 Decision 7:* on the shared first-run screen the
prompt's bare-Enter default is now accept (both first-run choices are pre-set
to accept so accepting everything is two keystrokes); an explicit "n" still
declines, and the decision is recorded once either way.

*The accept and the registration are separable in time (issue #634).*
Accepting continues straight into choosing a name, because an opt-in
that gets the SysOp nothing without a second trip through the console
loses the friction argument above. But the earlier of the two anchors —
first-SysOp bootstrap in `netbbs.admin` — runs before the node has ever
started, and the fingerprint the service knows a node by is cached by
the node's own startup; `netbbs.admin` is given a database path, not the
node's configuration, so it cannot load or create the identity itself
without risking a second one in the wrong directory. On such a node the
accept is recorded as always, the SysOp is told the name is picked once
the node has started, and the registration is *owed*: the next
interactive surface that can register — a SysOp's authenticated login,
or `netbbs.admin` run again, which is the only one a headless deployment
ever uses — opens the name editor, once, exactly as the prompt would
have. Backing out of it is a final answer, like declining the prompt;
registering by any route settles it; the DNS screen's `[R]egister` is
the same editor from then on.

**Decision 2 (locked in) — the managed-service credential is a separate,
auto-generated, per-registration secret, not the node's own Ed25519 key.**
Both the self-hosted path (SysOp supplies their own dynamic-DNS
provider's credentials, no design question) and the managed path stay
available, not exclusive. For the managed path specifically: reusing the
node's existing Ed25519 key (the Link protocol trust root — Noise XX
handshake identity, canonical event signing) was considered and
rejected. The convenience argument for reuse doesn't actually hold — a
minted credential, generated at registration and stored transparently by
the node, gives the identical zero-manual-handling experience reuse
would. What reuse *would* cost for no real gain: blast-radius coupling
(a compromised node key would also hijack the SysOp's public DNS name,
not just Link identity) and coupling any future node-key rotation/
recovery work to also remember to propagate to DNS. A separate
credential keeps the two systems', and their compromise/recovery
stories, fully independent.

**Decision 3 (locked in) — name governance is first-come-first-served
plus a reserved-word blocklist and a one-name-per-node cap; a registered
name only actually goes live once the node has maintained a minimum age
of successful contact with the registration service; new registrations
service-wide are rate-limited, rejected outright once that rate is
exceeded; and total active managed registrations are capped by a
separate cumulative ceiling, refused once reached — no preventive
identity vetting, and no human review queue, beyond that.** Matches how the project
treats registration/content elsewhere (SysOp owns the trust decision,
best-effort not gatekept) — requiring identity *verification* before
registering a subdomain would add real friction against the feature's
own point, for a comparatively low-stakes resource. The blocklist covers
only the obvious cases (the project's own names, trademarks, slurs) —
not a general dispute-avoidance mechanism.

Code review follow-up (PR #218): first-come-first-served plus a
blocklist bounds *which* names can be taken but not *how many* —
without a cap, one node could hold indefinitely many names, consuming
DNS-provider records/cost and squatting desirable ones. A first fix
attempt (a bare cap of one name per node/registration-credential) was
itself found insufficient on further Codex review (PR #221): a node
identity and a registration credential (Decision 2) are both free for a
remote client to mint — an attacker can generate a fresh node identity
per desired hostname and hold all of them simultaneously, each
individually satisfying a "one-per-node" cap.

**A second fix attempt kept the one-per-node cap and added a minimum-age
gate** on when a reservation actually becomes a live DNS record,
deliberately decoupled from Decision 1's first-run opt-in: accepting the
offer at first run (or first SysOp login) still costs nothing and
happens immediately, but the node's registration *intent* is recorded,
not yet published, until the node has maintained a minimum period of
successful contact with the registration service. **This was itself
found insufficient on yet another Codex review, same PR (#223):** the
gate only costs an attacker *wall-clock time*, not *effort per
identity* — a single process can run a trivial heartbeat for thousands
of fake node identities in parallel, all maturing simultaneously, at
essentially zero marginal cost per additional name. The gate delays the
attack once; it doesn't bound how many names come out the other end of
that delay, so the DNS-provider cost/squatting problem this decision
exists to prevent recurs in full once the qualifying period passes.

**The age-gate is kept anyway, as a mild friction layer, but the actual
bound is a separate, service-enforced admission control layered on
top: a rate limit on new registrations across the whole managed
service (not per node/identity — the thing an attacker cannot multiply
by minting more identities), automatic up to that rate, with anything
beyond it originally specified as queued for the project maintainer to
review** — the same "a human reviews it" shape Decision 4 already uses
for contested-name disputes, extended from content disputes to
registration *volume*. This is what actually closes the gap:
DNS-provider record cost and squatting are bounded by how fast the
*service* will create new records at all, independent of how many
identities a single attacker can mint and age in parallel.

Two further Codex findings, same PR (#225), both about resources a
review queue would itself introduce or leave unbounded, not about the
rate limit's own logic: the queue would need its own explicit-capacity
bound (itself a remotely-influenced resource a Sybil attacker submitting
past the rate limit could otherwise fill without limit); and separately,
a rate limit alone bounds *speed*, not *total count* — an attacker
patient enough to always submit exactly at (never over) the threshold,
keeping every registration's contact alive indefinitely so nothing
qualifies as abandoned, could still accumulate an unbounded number of
active records over a long enough time. The second finding needed its
own fix regardless of the queue question: a separate cumulative cap on
total *active* managed registrations service-wide, refused (not queued)
once reached, freed only as existing registrations are voluntarily
released or genuinely abandoned (Decision 5) — the rate limit alone
never stands in for a real total ceiling.

**The review queue itself was dropped entirely during implementation
planning, not carried forward as a bounded-capacity queue.** Once a
request is queued at all, the realistic resolution is the same either
way: the maintainer hears the SysOp's explanation and decides by hand.
A capacity-bounded queue adds real code and a genuine single point of
(human) failure without actually simplifying that manual conversation —
the same outcome is reached faster, with less to maintain, by simply
rejecting outright once the rate limit or the cumulative cap is
exceeded, symmetric with each other, and telling the caller plainly
that the service is at capacity with a contact channel for the rare
legitimate exception. Both are hard-reject and service-wide. A reclaim
(Decision 5) bypasses the *rate* limiter because it is not a new
registration, but it must still fit the cumulative active-registration
ceiling and the one-active-name-per-node limit: reactivation consumes a
real active slot, and release/reclaim cycling must not create more live
rows than either bound permits.

*Issue #598:* for three releases neither refusal carried a channel, and
the cumulative-cap refusal said "try again later", which is wrong for
that cap specifically — the rate limiter refills, but the ceiling frees a
slot only when some other registration is released or swept as
abandoned, so a SysOp told to wait retries against a wall instead of
getting in touch. The channel is the operator's own setting
(`MANAGED_DNS_CONTACT`, `services/managed_dns/README.md` §4), not a
project address baked into a service anyone may self-host; an instance
without one says so in words. The cap refusal now says what frees a
slot and that retrying will not; the rate-limit refusal keeps "try again
shortly", which is true. The node shows the service's sentence, not the
JSON it arrived in.

*Two things this decision states as flat rules that the rename workflow
(Decision 9) deliberately bends (issue #602).* The one-name-per-node cap
is one **except across a rename**, where a node holds two active rows —
the live name and its maturing replacement — until the replacement
matures; make-before-break requires exactly that, and it is bounded at
two (the node refuses a second rename while one is pending, and the
service treats a retry against an existing `replaces_name` link as
recovery of the same replacement, never a third row). And a rename
**spends a rate-limit token** where a reclaim does not: a reclaim
reactivates a row and a record that already exist, whereas a rename
reserves a new name and will create a new DNS record, which is precisely
the cost the limiter exists to bound. Both were accidents of
implementation until written here; both are now the rule.

**Decision 4 (locked in) — contested-name disputes are manual and
complaint-driven, stated as such, not implied automation; the operator's
end of that process is a revocation which takes the name away from the
registrant who held it, and nothing more.** At this project's current
scale, there is no realistic alternative to a human (the project
maintainer) reviewing a reported impersonation/abuse claim and revoking
if warranted. Documented explicitly so this isn't mistaken for a more
automated process than actually exists.

Issue #599: for three releases that was the whole of it. The decision
said a human revokes, and nothing existed for a human to revoke *with* —
no channel a complaint could arrive through, no endpoint or command, and
no runbook step. Doing it by hand meant editing the service's SQLite and
deleting the record out of BIND separately, with nothing to stop the
holder's node republishing on its next heartbeat. A decision whose
enforcement is entirely manual still has to say what the manual act is.

**The act is `POST /admin/revoke`, on the running service, gated by a
bearer token that is unset by default.** Inside the service rather than
a separate CLI because it must share the same transition lane as the
heartbeat, the sweep and every SysOp-driven transition — a second
process editing the same database could commit from a snapshot the
sweep had already moved past. Unset by default because a public-facing
service should not carry an administrative route that merely hopes
nobody finds it; an instance whose operator has not configured a token
has no administrative surface at all, and the refusal is identical for
"no token configured", "wrong token" and "no header" so the response
cannot be used to learn whether this instance has one.

**A revoked name is a fourth terminal status, not a reuse of
`released`.** The difference that matters is reclaim: a released or
abandoned row is deliberately reclaimable by the credential that held
it, for the length of the cooldown, which is Decision 5's whole point.
Applied to a takedown that same rule would undo it — the registrant's
node still holds the credential, and its registration draft prefills the
name it just lost, so a reclaim is one keystroke. A revoked row is
reclaimable by nothing.

**It expires on the same cooldown as the other two exits.** Revocation
blocks the registrant who was taken down, not the name forever: once
`released_at` ages past the shared cooldown the name is available to a
genuinely new registrant, including in principle the person it was taken
from. Permanent retirement was considered and rejected for the same
reason Decision 5 rejected it for voluntary release — an ever-growing
set of names retired for the life of the project — and because a
mechanism for names nobody may ever hold already exists: the
`blocklist` module's `RESERVED_NAMES`, curated by hand and changed by a
code change, which is where a permanently-barred name belongs.

**Revoking one half of a rename takes both halves.** A rename is the one
state in which a single registrant holds two names (see Decision 3's own
note on the per-node cap), so revoking the live name and leaving the
replacement to mature would hand the taken-down registrant a working
name. The voluntary release path refuses in this state and tells the
SysOp to cancel the rename first; an operator acting on a complaint has
nobody to ask, so revocation takes both rather than refusing.

**Only the rows that are still this registrant's, and still the rows
that were checked.** A replacement can outlive the name it replaced, so
the same node-fingerprint check rename completion and cancellation apply
before mutating anything through a `replaces_name` link applies here
too: a name whose cooldown elapsed and was reissued to a different node
is never taken by a complaint about the node that used to hold it. And
because a fresh registration does not pass through the transition lane
that a revocation holds, each row is re-read after the provider awaits
and written only if its credential hash is unchanged — proof it is still
the registration that was checked, rather than one that claimed the name
in between.

**Publication is undone before the rows move, and a provider failure
revokes nothing** — the same rule voluntary release already follows.
A takedown recorded in the database while the record is still resolving
would be worse than no takedown, because it looks finished. Only a row
which can still hold a published record is worth a provider call at all:
release and abandonment leave `last_known_address` behind on a row whose
record they already deleted, and failing a revocation on a deletion that
had nothing to delete would leave the credential reclaimable during
exactly the provider outage an operator cannot wait out.

**The registrant is told that, and where to write — not why.** The
first cut of this left the taken-down SysOp with a 401 the node read as
`abandoned`, and a `[R]egister` refusal that spoke of a cooldown: their
board went dark under a badge that blamed their own uptime. The service's
uniform "unknown or inactive registration" exists so that a caller
presenting a stale or invented credential learns nothing; the credential
that *held* a revoked name proves the caller is the one person the fact
belongs to, and the only thing the answer reveals is their own
registration's state. So every credential-bearing route — heartbeat,
release, rename, cancellation, and a reclaim by either path — answers a
revoked credential with `status: revoked` and the operator's contact
channel (Decision 3's `MANAGED_DNS_CONTACT`), and the node adopts that as
a terminal state of its own: the updater stops, the DNS screen shows
REVOKED with the channel, and `[R]egister` with a different name works as
usual. The reason stays the operator's. Anyone else asking for the name
still gets the cooldown refusal and learns nothing.

**The operator acts from inside NetBBS, not from a shell on the service
host.** The node whose operator also runs the service carries
`[managed_dns] admin_token` in its `netbbs.toml` — config file only,
never a command-line flag, since a secret in `argv` is visible to every
process on the host — mirrored into the node database at startup like
`service_url`, absence included. Its presence is what makes the Managed
DNS status screen offer `[A]dminister service`: the service's whole
table (`POST /admin/registrations`, same token, same uniform refusal,
every row but its credential hash, inactive rows included because a
name inside its cooldown can still be the subject of a complaint), one
registration in full — when it was registered, whose node, when it last
checked in, what it publishes, whether a rename is in flight, the reason
if already revoked — and `[R]evoke` behind a required reason and a
type-the-name confirmation, the same shape every delete in the console
uses. That is README §8's checklist made executable, minus the `dig`,
which stays the operator's. The reason prompt before the type-the-name
confirmation is a deliberate exception to §3.5's one-value rule: the
reason is the audit record the runbook requires, and a revocation is an
action against somebody else's board.

**Reports arrive through the project's issue tracker**, which is a
public channel and a real trade-off: an impersonation complaint tends to
name the impersonated party. The alternative considered was a dedicated
address, which is better suited to the content but is infrastructure
this project does not otherwise run, and an unmonitored one would be
worse than the tracker. Nothing in the mechanism assumes a report
arrived there. `services/managed_dns/README.md` §8 carries the
operational half: what to check before revoking, what separates abuse
from an ordinary naming dispute this decision deliberately does not
cover, the request itself, and what the former holder sees afterwards.

**Decision 5 (locked in) — every exit path shares one deliberately
generous cooldown before a name becomes assignable to a *different*
registrant; this is an accepted, bounded residual risk, not a solved
one.** There are four ways a name stops being live, not the two this
decision originally named (issue #601): voluntary release, abandonment
by the sweep, completion of a rename (the old name is released the
moment the replacement's record is published — Decision 9), and
operator revocation (Decision 4). All four set the same `released_at`
and start the same timer; the first three stay reclaimable by the
credential that held the name for as long as it runs, revocation by
nothing. A SysOp planning a rename should read that as: the old name is
not free the moment the new one goes live, and not re-registerable by
them either while they hold their one name under the new label. §8.10 states that
"the remote node label, endpoint, DNS name, and TCP address are never
identity authority" — real *Link* node identity is verified by the
Noise XX handshake against the Ed25519-derived key, independent of how a
connection was dialed, so a reassigned DNS name cannot impersonate a
node at the Link protocol level; that fact is why Decision 4's manual,
complaint-driven dispute process can stay lightweight for
*impersonation* claims specifically. It does not make reassignment safe
in general: ordinary Telnet and plain-HTTP callers are never protected
by that handshake, carry plaintext passwords, and a caller who still has
the old hostname bookmarked after reassignment can have credentials
harvested by whoever holds the name now — even an HTTPS caller can be
handed a convincing fake board once the new registrant obtains a
legitimate certificate for the name.

A Codex review (PR #221) correctly pointed out that a finite cooldown
only *delays* this exposure, it doesn't bound it to zero — a caller can
in principle hold a bookmark longer than any cooldown. That observation
is true but was weighed against the wrong bar: zero residual risk isn't
the standard any real identifier-reassignment system actually meets.
Domain registries drop-catch expired names — commonly with *no* grace
period at all — and telecom carriers recycle phone numbers after a
dormancy window (typically 90 days to a couple of years), both fully
aware that a returning party can be phished by whoever holds the
identifier now; neither treats "never reassign" as the answer. A
permanent-retirement design was tried in this entry and reverted:
correct in principle, but strictly more conservative than the
registries and carriers this project is directly comparable to, and it
trades a security property nobody else in this space provides for an
unbounded, ever-growing cost (a permanently-retired name is retired for
the life of the project, not just until interest fades). **The bounded-
cooldown design is kept, deliberately set longer than commercial
practice needs to be** (on the order of 90 days, well past a typical
registrar's ~30–45-day redemption window) **since NetBBS has no
commercial pressure to recycle a name quickly and generosity here costs
nothing.** A SysOp choosing to leave stops their own renewal and DNS-
updater contact immediately; the name itself does not become claimable
by a different registrant until the cooldown elapses. Exact cooldown
length is an implementation-time parameter (see the closing note
below), not fixed here, but it is the same parameter for both exit
paths, not two.

**Settled without being a real fork:** how this interacts with existing
Link node addressing — a full peer's descriptor already advertises a
host/port (`advertised_host`/`advertised_port`) for other nodes to dial;
a stable managed hostname is exactly what belongs there instead of a raw
dynamic IP. No new addressing concept, no conflict with fingerprint-
based node identity, which stays the actual trust root regardless. This
covers only the Link-to-Link dial path, not the caller-facing address a
human dials — see Decision 6.

**Decision 6 (locked in) — the managed hostname's caller-facing address
is standard ports on a fixed, documented convention, not a new discovery
mechanism.** Code review follow-up (PR #218): an A/AAAA record alone
cannot tell a human caller which transport or port to use, and
`advertised_host`/`advertised_port` (Decision 5's "settled without being
a real fork" above) describes only the Link HTTP listener — a
Link-disabled or outgoing-only board has no dialable address there at
all, so reusing it doesn't by itself deliver on "myboard.netbbs.org" as
a caller-facing promise. Resolved by convention rather than a new
protocol: a managed subdomain implies the node's Telnet (23) and SSH
(22) listeners sit on their standard ports, the same assumption every
plain hostname-based BBS address already carries. Web is the same
convention with one added, non-optional requirement: `netbbs.net.
nodeconfig`'s own web listener provides no TLS of its own, only through
an external TLS-terminating reverse proxy, so "web (443)" specifically
means that proxy bound to 443 and forwarding to the node's loopback web
listener — never NetBBS's own listener bound to 443 directly, which
would silently serve plaintext HTTP (including password entry) on the
port every caller assumes is HTTPS (code review follow-up, PR #221).
A board that cannot or will not run on standard ports (web's TLS-proxy
requirement included) keeps its managed DNS record (useful for the
dynamic-IP-tracking half of this feature alone) but does not get a bare
`myboard.netbbs.org` caller address as part of it; publishing a
nonstandard port remains the SysOp's own responsibility to communicate,
same as today.

*The convention is advisory and unverifiable, and it is stated where the
name is (issue #603).* Neither the node nor the service can see a
port-forward, a proxy or a firewall in front of a listener, so nothing
enforces this and nothing pretends to. What the node *does* know with
certainty is its own configured ports — and NetBBS's shipped defaults
are 2323/2222/8080 on purpose, since binding below 1024 needs privilege
this process should not want, so the default node is exactly the one
this decision says gets no bare caller address. Until issue #603 the
convention lived in one help panel behind a `[W]eb behind HTTPS proxy`
field whose answer was discarded, and the status screen showed
`myboard.netbbs.org` with a LIVE badge and no port in sight. Now the
registration editor and the Managed DNS status screen both state the
convention and measure it against the listeners the node recorded at
its last startup: "SSH: this node is configured for 2222, so a caller dialling
22 needs a port-forward or proxy in front of it", "Telnet: not enabled
on this node", and for web whether `[web] public_url` names an HTTPS
front — the one statement a SysOp has already made about TLS, which
replaces the discarded question. "Cannot verify" is not "cannot
mention". The LIVE badge itself is now made only once the service has
confirmed a published record; `matured` alone reads "NOT YET PUBLISHED".

**Decision 7 (locked in, implemented) — the managed-DNS credential is
in scope for node backup/restore, as an addition to §13.4's contract,
not a separate ceremony.** Code review follow-up (PR #218): §13.4
already treats a node's recoverable state as one atomic set of specific
artifacts (database, node identity, SSH host key, banners) precisely
because a partial backup silently loses things a SysOp needs after
restoring from disk loss. The managed-DNS credential (Decision 2) is
exactly that kind of durable node state — without it, a restored node
cannot update, voluntarily release, or benefit from Decision 5's
same-owner reclaim window for its existing registration. Its credential
file (`netbbs.managed_dns.credential`) joined §13.4's backup manifest as
its thirteenth artifact, the same plain-file-copy handling already used
for node identity and the SSH host key — see §13.4's own table.

**Decision 8 (locked in) — a node learns the managed service's address
from a shipped constant, overridable per node; it is not waiting on an
instruction from an operator there was never a way to give.** Issue
#583: a freshly bootstrapped 7.6.0 node accepted the opt-in — the
pre-set answer under issue #219 Decision 7 — and stopped immediately at "ask your
operator to set the service address." `netbbs.managed_dns.state.
set_service_url` had no caller anywhere in the installed package: no CLI
flag, no `netbbs.toml` key, no admin screen. The address had no route
into the database every other part of the feature reads it from, so
every node that accepted the offer dead-ended identically, and the
message sent each SysOp looking for an operator who was themselves. The
original reasoning — that the production address is an operational
decision independent of this client code — was right about *where the
decision lives* and silent about *how it travels*; an operational
decision still has to be expressible somewhere. Both halves exist now.
`DEFAULT_SERVICE_URL` (`netbbs.managed_dns.state`) carries the project's
own instance, the same shape and the same reason as `netbbs.link.
reliable_nodes.RELIABLE_NODES_URL` — a project-run service a node must
not need to be told about in order to use. `[managed_dns] service_url`
(or `--managed-dns-service-url`) points a node at a different one, which
is what a self-hoster running their own `services.managed_dns`, or the
project pointing a node at a staging deployment, needs. The configured
value is mirrored into the node database once per startup, *including
its absence*: an operator who removes the setting returns that node to
the shipped address rather than leaving it pinned to an override it was
told about once.

Making the address configurable makes it *changeable*, which the
credential's own design (Decision 2) has to answer: a managed-DNS
credential is a bearer secret for one service's registration, so a node
that already holds one must never present it to a service that did not
issue it. The issuing address is recorded in the same transaction as the
registration it belongs to, and every path that would send the
credential checks it first: the updater pauses its heartbeat (saying so
once in the log), release, rename and cancellation refuse and name the
two addresses that disagree, and registration against a new service
starts over as a fresh registration rather than presenting the old
secret — behind one confirmation, because it replaces the credential
file and leaves the registration at the old service to lapse on its own.
For the same reason the setting is restricted to `https://` unless it
names a loopback address: the service's own process speaks plain HTTP
behind a TLS-terminating proxy, so the node's side of it is the proxy's
address, and a plaintext hop to a remote host would put the credential
on the wire on every heartbeat.

`DEFAULT_SERVICE_URL` is `https://dns.netbbs.org`: the project's
instance on Roanoke, behind the same Apache that serves the website,
against the same BIND that serves the zone (roadmap tracker #612, step
2). From v7.7.0 until that deployment the constant was `None` and a
node simply had no service to register against. The opt-in was still
asked, and still recorded exactly once, per Decision 1 and issue #219
Decision 7; what differed was only that the node said plainly that the
service was not running yet, and it still says so if the constant is
ever reverted. Deliberately not "hide the question until the service
exists": the decision being asked is whether this node may contact
project infrastructure at all, the first-run screen is the one moment
that question is naturally in front of a SysOp, and deferring it would
mean either re-opening a settled consent question later or enrolling a
node that had said yes to something narrower.

**Decision 9 (locked in, implemented) — a SysOp may change their
managed name, and the change is make-before-break: the old name stays
live until the replacement has matured and published, then enters the
ordinary cooldown.** The workflow shipped with its invariants recorded
(the closing paragraphs below) but without the decision above them
(issue #601), so a reader asking "can I change my board's name, and
what happens to the old one?" found the guarantees only by knowing to
look among implementation notes. The decision: `Change [N]ame` on the
DNS screen reserves the replacement as a second, pending registration
linked to the current one (`replaces_name`), holding a second bearer
credential on disk beside the first. The node heartbeats both. While the
replacement matures — the same age gate as any registration — the old
name is canonical and keeps resolving; the status screen shows the
current name and the reserved one, and `[C]ancel change` is offered the
whole time. The heartbeat that first publishes the replacement's record
is the one that releases the old name, in that order (publish, delete,
then commit both rows), so callers never see a gap; the old name then
sits in Decision 5's cooldown, reclaimable only by the credential that
held it. Why a second credential and a transition journal rather than
something simpler: the two rows are two registrations the service
authenticates independently, a crash between "new credential issued"
and "node knows about it" would otherwise strand one of them, and the
journal is what makes the swap replayable in either direction. The
alternative — release, then register the new name — goes dark for the
whole age gate and forfeits the old name's reclaim window, which is why
the SysOp handbook says not to use it as a rename shortcut.

**Decision 10 (locked in, implemented) — every managed name requires
continuous contact from the node to stay alive; `dynamic` selects only
whether the published record follows the node's address; and a name the
service has abandoned is reclaimed by the node itself when it returns.**
Issue #600 found the Goal describing a "registration without the
updater" that the code had never offered: going live requires a day of
uninterrupted contact, staying live requires contact within every
abandonment window, and the node has no setting that stops the pass
while keeping the name. That is the right design, so the Goal was
amended rather than the code: the heartbeat is the liveness signal the
abandonment sweep uses to free squatted names, which is a Decision 3
abuse control, not a dynamic-DNS implementation detail, and a static
registration that never had to check in would be a name nothing could
ever reclaim. What a static board gets instead is that its record is
written once and never rewritten.

The real cost of that rule was a node back from an outage. Take a board
down for a fortnight's holiday: the sweep abandons the name after a
week, the record leaves DNS, and on the node's first heartbeat back the
service answers 401 — at which point the updater used to stop for good.
The name was held for the node for the whole cooldown, reclaimable by
one keystroke, but nothing said so; a SysOp who did not happen to open
the DNS screen lost it when the cooldown purged it. Abandonment is the
service noticing the node was away, not the SysOp choosing to leave, so
the node now performs that keystroke itself: on every pass while its
cached status is `abandoned` (and no rename is outstanding), it sends
`POST /reclaim` with the credential it still holds, and on success
carries straight on into the ordinary heartbeat. The route is the
safety of it: the service performs the reclaim the credential entitles
the node to, or refuses — it never registers afresh on the node's
behalf. A fresh registration mints a new credential, spends a
rate-limit token and, once the cooldown has purged the row, is for a
name that may no longer be this node's; all of that stays the SysOp's
own `[R]egister`. It is a route of its own rather than a flag on
`/register` so that a service older than it answers 404 and the node
fails closed, where an older service ignoring an unknown flag would
have registered afresh from a background task. A refusal is recorded
as a recovery note the DNS screen shows beside the ABANDONED badge, with
the sentence the service gave — and the screen then stops claiming the
name is held. Which refusals are retried follows from what can change
without the SysOp: the service at capacity, unreachable, or without the
route (an upgrade will bring it) are retried next pass; a 409 — the row
purged, held by another credential, or revoked — is *final*, since no
later pass can change what this credential is entitled to, and the
updater sends nothing more for that name until the SysOp's own
`[R]egister`. Three refusals are answers rather than failures: a row that is *already
active* under the same credential is the retry of a reclaim whose
response was lost, and is answered with the row's state so the node
resumes heartbeating; a `released` row is refused with that word, since
release is the SysOp's decision to stop (Decision 5) and a node
restored from a backup taken before it would otherwise undo it on its
first pass — the node adopts the service's word instead; and a
`revoked` row refuses the automatic path exactly as it refuses the
manual one (Decision 4).

*A check-in that does not happen is never silent (issue #640).* Because
the name depends on contact, a node that holds a `pending` or `matured`
name and did not reach the service says so on the DNS screen: "Last
contact: never" rather than no row, the reason the latest attempt
failed, and no "nothing to do". Two independent v7.9.0 nodes registered,
were told the name would go live once the node had stayed in contact,
and never contacted the service again, and every way that can happen was
silent: a credential file the node's account cannot read (written by
`netbbs.admin` run as another account) raised and ended the updater task
for the rest of the node's uptime, and a node whose only route out is an
HTTP proxy failed every check-in into the log. Hence three rules. The first check-in is
sent by the register flow itself, the moment a registration succeeds, so
the SysOp is told of a failure while still looking rather than promised
a wait. Every reason a pass sends nothing for such a name — unreadable
or missing credential, no service address, a credential another service
issued, an opt-in that is not `accepted`, a request that got no usable
answer, a pass that raised — is recorded for that screen, and the task
survives a pass that raises, since what raises there is repaired without
a restart. And the heartbeat's direct connection stays: the service
publishes the address a check-in arrives from, which through a forward
proxy is the proxy's, so `/register` honouring `HTTPS_PROXY` while
`/heartbeat` does not is deliberate — it is stated where the failure is
shown, since a SysOp cannot guess it. A node with no direct route cannot
hold a managed name today; letting one declare a static address instead
is an open question, not something the node works around.

**Implemented.** Node-side client (`src/netbbs/managed_dns/`, shipped
inside the installable `netbbs` package: opt-in prompt, credential
storage, the periodic heartbeat/updater task, the SysOp status/register/
release admin screen) and the managed-service backend
(`services/managed_dns/`, a separate deployable the project itself
operates, never packaged into a node install, the same relationship the
already-live netbbs.org website has to its own separate deployment) —
including a real `Rfc2136DnsProvider` (TSIG-signed RFC 2136 dynamic
updates against a self-hosted BIND server, this project's own
infrastructure choice, not a commercial DNS API) behind the same
`DnsProvider` interface a `LoggingDnsProvider` satisfies for every
automated test. Decision 3's originally-locked review queue was dropped
during implementation planning (see that decision's own closing
paragraph above) in favor of a simpler, symmetric hard-reject shape for
both the rate limit and the cumulative cap. Shipped implementation-time
parameters, all constructor-injectable and reasoned defaults rather than
values this design doc fixes: a 24-hour minimum-age qualifying period
(Decision 3), a 5-per-hour-refilling, 5-capacity service-wide
registration rate limit (Decision 3), a cumulative cap of 1000 active
registrations (Decision 3), a 7-day no-contact abandonment threshold
before a registration is swept as abandoned, and a 90-day cooldown
shared by both voluntary release and abandonment (Decision 5, "on the
order of 90 days" as locked in above). A fourth terminal status, `revoked`, and the
token-gated `/admin/revoke` behind it carry Decision 4's manual dispute
process (issue #599); `MANAGED_DNS_CONTACT` is the channel Decision 3's
refusals name (issue #598). Actually standing the backend up
— a host, DNS delegation, a real BIND server's `allow-update` ACL and
matching TSIG key — is an operational step the code does not perform on
its own; see `services/managed_dns/README.md`. It is done: the project
instance answers at `https://dns.netbbs.org`, and the shipped
`DEFAULT_SERVICE_URL` names it (Decision 8). Turning the zone dynamic
had two consequences the runbook now records: hand edits to the zone
file go through `rndc freeze`/`thaw`, and every static name in the zone
is in the service's blocklist first, because the provider *replaces* a
name's address records rather than adding to them.

The minimum-age period measures uninterrupted successful heartbeat
contact, not wall-clock time since registration: first contact starts
the window and a gap beyond the abandonment threshold resets it. DNS
provider mutations run outside the HTTP event loop and share one bounded
transition lane with their surrounding database mutation. A sweep,
heartbeat, release, or reclaim therefore cannot commit from state made
stale by another provider await; concurrent HTTP transitions receive a
retryable rejection before entering the worker queue. Publication and
deletion failures remain retryable state (release is not finalized until
deletion succeeds, and a failed static publication is retried). Successful
replacement maturation carries that publication result through the rest of the
heartbeat and never issues a second publication from the stale pre-transition
row. The service-wide token-bucket state survives backend restarts.

Node-side updater passes and interactive registration, release, rename, and
cancellation transitions share one process-local transition lock per node
database. The lock covers the remote mutation and its local reconciliation, but
never a human prompt, so a heartbeat response from an older snapshot cannot
overwrite a completed SysOp transition. Node-side rename state and interactive
registration/reclaim results are each one local database transaction: the
replacement name, pending/unpublished state, and previous-name presentation state
never become visible in a partial combination, nor can a reclaimed status appear
with a stale published flag. Heartbeat reconciliation follows the same
rule for the active and previous names, statuses, publication flags, and contact
time. A credential-specific HTTP 401 is authoritative independently of the
other credential's transient result, and all inactive outcomes from one pass
are applied in that single transaction. Cancellation and updater-led recovery
first commit the remotely revived
previous name as a coherent local state, then use the credential transition
journal in reverse to restore the previous bearer secret and remove the
replacement-era extra secret. The service's cancellation response supplies the
revived name's authoritative publication state; a failed republish cannot revive
a stale local publication claim. A crash on either side remains recoverable by
the retained two credentials and the updater. Point-in-time restore likewise
removes any primary, previous, or journal credential artifact absent from the
backup generation. A replacement abandoned during a rename remains eligible
for credential-recovery retry only while its own release/abandonment cooldown
is still open, and reactivation restarts its contact window at the retry time.
Recovery of a replacement which is still marked pending also records fresh
contact and restarts its maturation window only if the prior contact gap crossed
the abandonment threshold. A successful standalone registration or reclaim
clears any expired previous-name presentation state and obsolete previous
credential rather than leaving a phantom cancellable transition.
Rename completion releases or deletes the previous row only when it still belongs
to the same node fingerprint; a cooldown-expired name reissued to another node is
never mutated through the stale replacement link. Cancellation performs the
same ownership check before any provider deletion, including when the reissued
previous row is already active. Rename retry likewise treats an existing
`replaces_name` relationship as recoverable only when the replacement and
currently authenticated registration have the same node fingerprint; reissue
never transfers control of an old replacement credential. If its heartbeat
becomes
authoritatively inactive while the old
name's heartbeat fails transiently, the node continues heartbeating the retained
old credential on later passes rather than letting the still-live name expire.
A successful old-name heartbeat is also reconciled when the replacement
heartbeat fails transiently: the replacement's cached state remains unchanged,
while the old name's authoritative status and publication result are committed
atomically.
An authenticated successful rename also refreshes the current registration's
contact window before reserving or recovering its replacement, so a waiting
abandonment sweep cannot immediately withdraw the promised old name. An inactive
rename target whose cooldown has elapsed is deleted and admitted as a fresh
replacement immediately, matching ordinary registration rather than waiting for
the periodic sweep. Successful cancellation refreshes the retained
previous registration's last-contact time even when it was still active. An
uninterrupted pending name preserves its earned maturation window, while one whose
last contact crossed the abandonment threshold restarts that window just like a
heartbeat.
