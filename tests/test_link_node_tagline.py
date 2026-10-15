"""
A node page's tagline (issue #1172, design doc §8.13): one line of the
SysOp's own words, carried in the signed endpoint descriptor while the page
is not off, and held to a hard shape on both sides because it is remote text
published on our domain.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from netbbs.__main__ import _build_own_hello_provider
from netbbs.link.events import EndpointDescriptor, build_endpoint_descriptor, verify_endpoint_descriptor
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.node_page import (
    MAX_TAGLINE,
    NODE_PAGE_OFF,
    NODE_TAGLINE_CONFIG_KEY,
    advertised_tagline,
    clean_tagline,
    get_node_tagline,
    own_node_page_facts,
    set_node_page_without_commit,
    set_node_tagline_without_commit,
)
from netbbs.config import set_config
from netbbs.link.protocol import HelloMessage, LinkNode
from netbbs.moderation.log import list_recent_actions
from netbbs.net.char_input import InputCancelled
from netbbs.net.nodeconfig import LinkConfig
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from tests.test_admin_flow import FakeSession
from tests.test_link_node_page import CREATED_AT, _dns_screen, _live_name, db, lane, sysop  # noqa: F401


def _save(db, value):
    stored = set_node_tagline_without_commit(db, value)
    db.connection.commit()
    return stored


# -- the shape -------------------------------------------------------------------


def test_a_plain_line_is_kept_with_runs_of_spaces_folded():
    assert clean_tagline("  Retro games,   art and   late-night chat  ") == "Retro games, art and late-night chat"


def test_markup_is_kept_as_text_for_the_page_to_escape():
    assert clean_tagline("<b>Bold</b> & <a href=x>link</a>") == "<b>Bold</b> & <a href=x>link</a>"


@pytest.mark.parametrize("value", [
    "Line one\nline two",           # control character
    "Ring\x07the bell",              # control character
    "\x1b[31mred\x1b[0m",            # an escape sequence
    "Right‮to left",            # bidi override (a format character)
    "Zero​width",               # zero-width space (a format character)
    "x" * (MAX_TAGLINE + 1),         # too long
    "",                              # nothing
    "    ",                          # nothing but spaces
    None, 42, ["a list"],            # not text
])
def test_anything_else_is_refused_rather_than_repaired(value):
    assert clean_tagline(value) is None


def test_exactly_the_cap_is_allowed():
    assert clean_tagline("x" * MAX_TAGLINE) == "x" * MAX_TAGLINE


# -- the stored setting ------------------------------------------------------------


def test_never_saved_is_none_and_saving_reads_back(db):
    assert get_node_tagline(db) is None
    assert _save(db, "  A board  for  ANSI art ") == "A board for ANSI art"
    assert get_node_tagline(db) == "A board for ANSI art"


def test_blank_clears(db):
    _save(db, "Something")
    assert _save(db, "   ") is None
    assert get_node_tagline(db) is None


@pytest.mark.parametrize("value,reason", [
    ("x" * (MAX_TAGLINE + 1), "at most"),
    ("Tab\there", "printable"),
])
def test_text_that_cannot_be_a_tagline_is_refused_with_its_reason(db, value, reason):
    _save(db, "Kept")
    with pytest.raises(ValueError, match=reason):
        set_node_tagline_without_commit(db, value)
    db.connection.rollback()
    assert get_node_tagline(db) == "Kept"


def test_a_damaged_stored_value_reads_as_none(db):
    set_config(db, NODE_TAGLINE_CONFIG_KEY, "bad\x1bvalue")
    assert get_node_tagline(db) is None


def test_a_page_that_is_off_publishes_no_tagline(db):
    _save(db, "Hello there")
    assert own_node_page_facts(db).tagline == "Hello there"
    set_node_page_without_commit(db, NODE_PAGE_OFF)
    db.connection.commit()
    assert own_node_page_facts(db).tagline is None


# -- the descriptor ----------------------------------------------------------------


def test_the_descriptor_carries_it_and_still_verifies():
    identity = bootstrap_node_identity("tagline-signer")
    descriptor = build_endpoint_descriptor(
        signing_identity=identity.signing_key, subject_fingerprint=identity.fingerprint,
        addresses=None, outgoing_only=True, created_at=CREATED_AT, tagline="Retro games and art",
    )
    restored = EndpointDescriptor.from_dict(json.loads(json.dumps(descriptor.to_dict())))
    assert verify_endpoint_descriptor(restored, identity.signing_key.verify_key)
    assert advertised_tagline(restored.payload) == "Retro games and art"


def test_no_tagline_is_not_carried():
    identity = bootstrap_node_identity("tagline-none")
    descriptor = build_endpoint_descriptor(
        signing_identity=identity.signing_key, subject_fingerprint=identity.fingerprint,
        addresses=None, outgoing_only=True, created_at=CREATED_AT,
    )
    assert "tagline" not in descriptor.payload
    assert advertised_tagline(descriptor.payload) is None


def test_a_hostile_tagline_from_a_peer_is_dropped_and_the_hello_still_accepted():
    sender = LinkNode(identity=bootstrap_node_identity("tagline-sender"))
    receiver = LinkNode(identity=bootstrap_node_identity("tagline-receiver"))
    hello = sender.build_hello(
        addresses=None, outgoing_only=True, created_at=CREATED_AT, tagline="evil\x1b]0;title\x07" + "x" * 200,
    )
    peer = receiver.handle_hello(HelloMessage.from_dict(json.loads(json.dumps(hello.to_dict()))))
    assert peer.fingerprint == sender.identity.fingerprint
    assert advertised_tagline(peer.descriptor.payload) is None


def test_the_hello_provider_carries_the_saved_tagline_after_a_refresh(tmp_path):
    db = Database(tmp_path / "provider.db")
    node = LinkNode(identity=bootstrap_node_identity("tagline-provider"))
    provider = _build_own_hello_provider(node, LinkConfig(enabled=True), db)
    lane = DatabaseLane(db.path)
    try:
        assert "tagline" not in provider().descriptor.payload
        _save(db, "Night owls welcome")
        asyncio.run(provider.refresh(lane))
        assert provider().descriptor.payload["tagline"] == "Night owls welcome"
    finally:
        lane.close()
        db.close()


# -- the DNS screen ----------------------------------------------------------------


def test_the_dns_screen_shows_the_tagline_and_its_key(db, lane, sysop):
    _live_name(db)
    text = _dns_screen(lane, sysop, [])
    assert "Tagline" in text and "(none)" in text
    assert "[T]agline" in text


def test_tagline_saves_clears_and_audits(db, lane, sysop):
    _live_name(db)
    text = _dns_screen(lane, sysop, ["t", "Retro games and ANSI art"])
    assert get_node_tagline(db) == "Retro games and ANSI art"
    assert "Tagline saved." in text
    assert "Retro games and ANSI art" in text

    text = _dns_screen(lane, sysop, ["t", ""])
    assert get_node_tagline(db) is None
    assert "Tagline cleared." in text

    audited = [entry.detail for entry in list_recent_actions(db) if entry.action == "set_node_tagline"]
    assert sorted(audited) == sorted(["Retro games and ANSI art", "(cleared)"])


def test_a_refused_tagline_says_why_and_keeps_the_old_one(db, lane, sysop):
    _live_name(db)
    _save(db, "Kept")
    text = _dns_screen(lane, sysop, ["t", "x" * (MAX_TAGLINE + 5)])
    assert get_node_tagline(db) == "Kept"
    assert f"A tagline is at most {MAX_TAGLINE} characters. Tagline unchanged." in text


def test_escape_keeps_the_tagline(db, lane, sysop):
    from netbbs.net.admin_flow import admin_menu
    from tests.test_admin_flow import _normalized_visible, _written_text

    class EscapingSession(FakeSession):
        async def read_line(self, echo=True, history=None, completer=None, **kwargs):
            line = await super().read_line(echo, history, completer, **kwargs)
            if line == "ESCAPE":
                raise InputCancelled()
            return line

    _live_name(db)
    _save(db, "Kept")
    session = EscapingSession(["d", "t", "ESCAPE", "b", "b"])
    asyncio.run(admin_menu(session, lane, sysop))
    assert get_node_tagline(db) == "Kept"
    assert "Tagline unchanged." in _normalized_visible(_written_text(session))


def test_a_node_without_an_active_name_has_no_tagline_key(db, lane, sysop):
    from netbbs.managed_dns.state import OptIn, set_opt_in

    set_opt_in(db, OptIn.DECLINED)
    text = _dns_screen(lane, sysop, [])
    assert "[T]agline" not in text
