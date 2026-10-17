# Issue #628 — real-time Link through an HTTP `CONNECT` proxy

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Decided during the roadmap re-evaluation of 2026-09-18 (issue #612): a network
that permits outbound traffic only through an HTTP proxy is one of the settings
Link is meant for, and there asynchronous Link works (every outbound
`aiohttp.ClientSession` sets `trust_env=True`) while live chat does not.
Normative description: §8.10.

What the code does today, which set the shape of the answer:

- **Three places open a real-time socket, and they all end the same way.**
  `dial_realtime_session` (direct dials of a peer, a relay, an anchor or a
  linked channel's origin), `attach_relayed_session` (a party joining a
  relayed bridge, which writes the plaintext `NETBBS-BRIDGE/1` attach record
  before Noise starts) and `RealtimeRelay._handle_upstream_ready` (a relay
  forwarding to an upstream relay, issue #270, which never runs Noise at all).
  Each calls `asyncio.open_connection(host, port)` on an address taken from the
  peer's signed descriptor and then works on the stream pair. Nothing below
  that line knows or cares where the bytes go, so a tunnelled stream
  substitutes cleanly — including for the attach preamble, which is just more
  bytes written ahead of the handshake.
- **An outgoing-only node dials directly as well as through relays.** No
  real-time dial checks `outgoing_only`: a DM tries the target's advertised
  real-time addresses first, a linked channel dials its origin, and every
  participating node keeps anchor connections to the reliable nodes. So the
  gap is not the relay leg alone; on a proxy-only network every real-time
  path fails.
- **The advertised port is already independent of the bound one.**
  `realtime_advertised_port` exists and is what the signed descriptor carries,
  so a node can bind 8862 and advertise 443 behind a forwarder today.

**Decision 1 — one helper opens every real-time socket, and it tunnels when a
proxy applies.** All three sites call a single function in place of
`asyncio.open_connection`. When the environment names a proxy for the target,
the helper connects to the proxy, sends `CONNECT host:port HTTP/1.1` with a
`Host: host:port` header, requires a `2xx` answer, and returns the same stream
pair; Noise, the attach preamble and the relay pipe then run over it unchanged.
The target comes from a peer's descriptor, which is signed but whose addresses
nothing validates today, so the helper accepts only a strict authority — a DNS
hostname, an IPv4 literal or a bracketed IPv6 literal, and a port in range —
and refuses anything else before a byte reaches the proxy; otherwise a peer
could inject request lines into the operator's authenticated proxy. The target
host is sent to the proxy unresolved, because on a proxy-only network the local
resolver commonly cannot resolve outside names. Of the Link traffic, the proxy
sees what any on-path observer of a direct connection sees: the dialled address
and, on a relayed attach, the plaintext `NETBBS-BRIDGE/1` record with its
single-use attach token, which travels in the clear on a direct connection too.
Everything after it is Noise, authenticated end to end (§8.10). What a direct
connection does not have is the proxy's own credentials: with Basic
authentication (decision 4) they cross the `http://` leg to the proxy in the
clear, exactly as they already do for the asynchronous half's proxied requests. The descriptor-pinning rules for relay
attach addresses are unaffected because they compare the address before the
socket is opened. The upstream-relay site
is included although a relay is by definition reachable: the rule "every
real-time socket is opened here" is what keeps a fourth site from quietly
reopening the gap. Rejected: a proxy-aware variant of each caller, which is
three implementations of one handshake.

**Decision 2 — the proxy comes from the environment only, and it is the one
asynchronous Link uses.** A peer's Link endpoint is advertised as `http`, so
`aiohttp` sends that peer's boards and mail through `HTTP_PROXY`; the tunnel
uses the same variable, falling back to `HTTPS_PROXY` when only that is set, and
`NO_PROXY` exempts targets, all read with the standard library's own rules
rather than a parser of ours. Using the proxy that already carries this node's
asynchronous traffic is what keeps the two halves from disagreeing when the
variables differ. Only `http://` proxy URLs are supported. A SOCKS or `https://`
proxy URL that applies to a target fails that dial with a visible configuration
error and is never treated as absent: a configured proxy is the only way out,
and falling back to a direct dial would bypass the operator's egress policy
wherever direct traffic happens to be possible. Rejected: a `[link] proxy`
setting. It would be a second source of truth beside the one the
async half of Link already reads, and the two halves of one node could then
disagree about how that node reaches the network.

**Decision 3 — when a proxy applies, the tunnel is the only attempt.** There is
no direct dial first and no fallback to direct afterwards. A network that needs
a proxy usually drops rather than refuses direct outbound connections, so a
direct-first rule would make every live dial wait out its full timeout, once
per advertised address, before the path that works. This is also what `aiohttp`
does for the async half: with a proxy configured it uses it. A SysOp whose
proxy should not carry some peers says so with `NO_PROXY`, which is the
standard lever for exactly that. Rejected: tunnel-on-failure, for the latency
above and because it makes the path a dial takes depend on timing rather than
on configuration.

**Decision 4 — proxy authentication is Basic, from the same sources
asynchronous Link reads, and nothing else.** Credentials in the proxy URL's
userinfo, or failing that the `~/.netrc` entry for the proxy host (which
`aiohttp` consults under `trust_env`), become a `Proxy-Authorization: Basic`
header on the `CONNECT`. NTLM, Negotiate and Kerberos are out of scope: the
async half of Link does not speak them either (`aiohttp` does not), so building
them for live chat alone would leave a node with live chat and no boards. The
supported answer for such a network is a local authenticating shim that
presents an unauthenticated proxy on loopback, which serves both halves at
once; the operator documentation names that pattern.

**Decision 5 — no NetBBS change for the port; it is an operator recipe.**
Corporate proxies commonly allow `CONNECT` only to 443 (Squid's stock
`deny CONNECT !SSL_ports`). A reliable node that wants proxy-only callers to
reach it live advertises its real-time address on 443 with
`realtime_advertised_port` and forwards that port to its real-time listener.
NetBBS does not bind 443 itself — the same rule as the web transport (issue
#201) — and does not multiplex Noise with TLS on one port; a host whose 443 is
already taken by an HTTPS front end needs a second address, or a protocol
demultiplexer in front of both, and either is the operator's choice. Rejected for now: a
second advertised real-time address per node. The descriptor already carries a
list and dialers already try each entry in order, so adding one later is a
compatible change, but nothing needs it until a reliable node cannot move its
only real-time port.

**Decision 6 — the tunnel is bounded, and its failures are recorded where they
happen.** One timeout of the helper's own covers connecting to the proxy and
reading its answer, and the answer has a header-size ceiling, since
`dial_realtime_session` itself has no overall timeout and the anchor connector
calls it without one; a proxy that blackholes the connection, or accepts it and
then says nothing, must not hold a dial open. On every path that does not
return the stream — timeout, an oversized or malformed answer, a refusal,
cancellation — the helper closes the proxy connection and waits for it to close
before raising, since no caller holds the writer yet; anchor retries would
otherwise leak a socket per attempt. A refusal
(`403`, `407`, `502`, anything not `2xx`) raises a distinct transport error
naming the status. The helper also records the last tunnel outcome on a
node-owned status object, and a dial whose tunnel opened but whose handshake
then failed records that too — "tunnel opened, handshake failed" is its own
outcome and never shows as a success, because the anchor connector swallows every dial
exception and a caller's `DirectChatUnreachable` deliberately carries no
reason; the Link status screen shows the proxy in use and that last outcome
("tunnel refused: 407 Proxy Authentication Required"), and a change of outcome
is logged at WARNING once rather than on every retry. Callers keep the generic
unreachable message: a caller cannot fix a proxy and the SysOp now can see it.

**Decision 7 — a TLS-inspecting proxy is a known limit.** A proxy that
terminates or inspects TLS inside the tunnel (Squid `ssl_bump`, most
"SSL inspection" appliances) will reject a stream that is not TLS, and Noise is
not TLS. That shows up as a tunnel that opens and then fails its handshake, which is
the outcome decision 6 records. Wrapping real-time Link in TLS to pass such a proxy is
not part of this decision; it would be a separate one, taken only if a real
deployment meets it.

**Testing.** A real loopback `CONNECT` proxy in the suite (an asyncio server
that parses the request, rejects one without `Host`, optionally demands Basic
credentials, and pipes bytes), per the rule about real boundaries: a direct dial, a relayed attach
with its preamble, and an upstream-relay leg each through it; `NO_PROXY`
bypass; a `407` surfacing as the recorded outcome; a silent proxy and a
blackholed one both bounded by the timeout; an unsupported proxy URL failing
the dial rather than dialling direct; a descriptor address with CR/LF refused
before the proxy sees it; the proxy observing EOF after each failed setup. A ratchet test that no `asyncio.open_connection` remains in
`netbbs.link` outside the helper. The live check is the proxy-only node joining
the dogfood deployment and holding an anchor session to a reliable node.

**Not done, deliberately.** Managed DNS stays as it is: its heartbeat connects
direct on purpose, because the service publishes the address a check-in
arrives from and a proxy-only node has no address worth publishing (issue
#201). This
decision does not change that asymmetry, and the helper is not used there.
Implementation is its own issue.
