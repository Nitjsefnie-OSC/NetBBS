"""Cross-check the independent Link-v1 corpus against the Python authority."""

from __future__ import annotations

import json
from pathlib import Path

from nacl.signing import VerifyKey

from netbbs.link.events import canonical_bytes, event_content_id, strict_json_loads


_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "link_interop_vectors.json").read_text(encoding="utf-8")
)

_MAX_SAFE_INTEGER = 2**53 - 1


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point value is forbidden: {value}")


def _safe_integer(value: str) -> int:
    parsed = int(value)
    if not -_MAX_SAFE_INTEGER <= parsed <= _MAX_SAFE_INTEGER:
        raise ValueError(f"unsafe integer: {value}")
    return parsed


def _parse_wire_vector(vector: dict) -> dict:
    raw = bytes.fromhex(vector["json_hex"]) if "json_hex" in vector else vector["json"]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    value = strict_json_loads(raw, parse_float=_reject_float, parse_int=_safe_integer)
    if not isinstance(value, dict) or value.get("netbbs_protocol") != 1:
        raise ValueError("unsupported or malformed protocol envelope")
    return value


def test_python_matches_every_independent_signed_vector():
    verify_key = VerifyKey(bytes.fromhex(_CORPUS["fixture_public_key_hex"]))
    for vector in _CORPUS["canonical_edge_vectors"]:
        assert canonical_bytes(vector["fields"]).hex() == vector["canonical_hex"], vector["name"]
        assert event_content_id(vector["fields"]) == vector["content_id"], vector["name"]
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


def test_python_matches_wire_vector_acceptance():
    for vector in _CORPUS["wire_vectors"]:
        try:
            value = _parse_wire_vector(vector)
            accepted = True
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            accepted = False
        assert accepted is vector["accepted"], vector["name"]
        if accepted and "canonical_hex" in vector:
            assert canonical_bytes(value).hex() == vector["canonical_hex"], vector["name"]
