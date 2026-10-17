# NetBBS Link v1 interoperability corpus

This document is the implementation-facing contract for the independent
Link-v1 compatibility check. It is intentionally narrower than the complete
Link product and protocol design: it specifies the bytes that are hashed or
signed, and the wire-input rules needed before those bytes are produced.

## Canonical event envelope

Every signed event is an object with these members:

```json
{"netbbs_protocol":1,"object_type":"...","payload":{}}
```

The content ID and signature cover the complete object. `object_type` is part
of the signed bytes and provides domain separation. The protocol version is an
integer, not a floating-point value.

Canonical JSON uses compact separators, ASCII escapes, UTF-8 encoding, and no
insignificant whitespace. Object keys are sorted by Unicode code point after
recursive NFC normalization. Strings, including object keys, are normalized
recursively. A normalization collision between two object keys is rejected.

Booleans and null retain their JSON types. Fields which do not apply are
omitted; omission and explicit `null` are different values. Floating-point
numbers are forbidden. Integers must be in the inclusive range
`[-(2^53 - 1), 2^53 - 1]`. Supplementary Unicode characters are represented as
JSON surrogate-pair escapes in the canonical UTF-8 bytes.

The canonical bytes are hashed with BLAKE2b-256 and represented as lowercase
hex for a content ID. Ed25519 signs the canonical bytes directly; signatures
are represented as lowercase hex in this corpus.

## Wire input rules

An implementation must reject duplicate object keys before canonicalization,
including duplicates nested inside payloads. It must reject malformed JSON,
trailing data, floats, unsafe integers, and unsupported protocol versions.
Unknown fields are retained when the object is canonicalized: an implementation
must not silently discard a field which it does not semantically interpret.
Whether an unknown field is accepted by a particular event schema is a separate
validation decision; if it is accepted, it remains inside the signed bytes.

## Corpus contract

`tests/fixtures/link_interop_vectors.json` contains fixed fixture-only key
material and vectors. The private seed exists only to make the corpus
reproducible and must never be used as a node credential. Each signed vector
records the envelope, exact canonical bytes, content ID, signature, and the
expected verification result, as well as accepted and rejected wire-input
cases. The command below runs the independent implementation against the
corpus; the Python test suite cross-checks the same signed vectors against the
reference implementation. The signed corpus includes key transitions, board
posts, channel messages, file descriptors, and Link messages; it validates
envelope compatibility, not the full semantic authorization rules for each
event family.

The dependency-free reference consumer is:

```text
node scripts/link_interop.mjs tests/fixtures/link_interop_vectors.json
```

The complete local gate, which runs both the independent consumer and the
Python reference tests against this corpus, is:

```text
python scripts/link_interop_check.py
```

The runner uses the invoking Python interpreter for the reference tests and
requires only `node` on `PATH` for the independent implementation. It is
intentionally a documented deterministic command rather than a hosted CI
requirement; the corpus must remain runnable on the project's supported
development platforms.
