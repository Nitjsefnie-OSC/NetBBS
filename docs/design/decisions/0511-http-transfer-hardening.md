# Issue #511 — HTTP transfer hardening — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Split out of PR #508, whose fixes did not converge. Normative description: §6.2.

**Decision 1 — two ceilings on an HTTP upload.** `max_upload_bytes` keeps
meaning file bytes, as over Zmodem; the request gets that plus a fixed framing
allowance. Rejected: one ceiling on the whole body, which makes a browser's own
framing refuse files within the configured maximum and, with a small enough
setting, makes browser upload impossible.

**Decision 2 — `HEAD` tells the truth.** It answers as a `GET` would, without
spending the grant. It used to answer 204 for every token so as not to be an
oracle for guessing them; with 256-bit tokens an oracle offers a guesser
nothing, and the page needs the answer. Rejected: a separate check route, which
exposes the same information on a second path.

**Decision 3 — probe, then stream.** The page probes with `HEAD` and saves
through `<a download>` only on a yes. Rejected: `fetch` plus `blob()`, which
buffers the whole file in the page; a CORS header on the endpoint, which widens
who may read a transfer response; and the `public_url` link as given, which
fails cross-origin. The page resolves the token against its own address
instead, which is same-origin and keeps a reverse-proxy prefix.

**Not done, deliberately.** The probe is not a reservation: a file deleted, or a
slot taken, between probe and `GET` still saves that `GET`'s error body. The
window is one round trip.
