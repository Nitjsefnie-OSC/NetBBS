"""Cross-check the independent Link-v1 corpus against the Python authority."""

from __future__ import annotations

import json
from pathlib import Path

from nacl.signing import VerifyKey

from netbbs.link.events import canonical_bytes, event_content_id


_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "link_interop_vectors.json").read_text(encoding="utf-8")
)


def test_python_matches_every_independent_signed_vector():
    verify_key = VerifyKey(bytes.fromhex(_CORPUS["fixture_public_key_hex"]))
    for vector in _CORPUS["signed_event_vectors"]:
        envelope = vector["envelope"]
        canonical = canonical_bytes(envelope)
        assert canonical.hex() == vector["canonical_hex"], vector["name"]
        assert event_content_id(envelope) == vector["content_id"], vector["name"]
        if vector["signature_valid"]:
            verify_key.verify(canonical, bytes.fromhex(vector["signature_hex"]))
        else:
            try:
                verify_key.verify(canonical, bytes.fromhex(vector["signature_hex"]))
            except Exception:
                pass
            else:
                raise AssertionError(f"invalid signature was accepted: {vector['name']}")
