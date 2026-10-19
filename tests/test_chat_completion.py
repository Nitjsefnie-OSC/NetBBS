"""
Tests for the BBS-specific Tab completer built in netbbs.net.chat_flow:
command-name completion (permission-aware for
moderation commands), and username completion for /msg, /private
(online accounts only) vs /whois, /finger (any registered account
works when typed in full, but *completion suggestions* are narrowed to
this channel's current roster -- offering every registered account on
a node with hundreds of users from one typed character is noise, not
help). /msg and /private also offer, after the people, the bots that
would answer the caller in the channel (issue #1372). The generic word-replacement mechanics themselves are covered
separately in tests/test_char_input_completion.py; this file only
exercises what `_build_completer`'s closure returns for a given line of
text. `/query` has been removed as a command.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from netbbs.auth.users import create_user
from netbbs.bots import link as bot_link_module
from netbbs.bots.registry import mute_bot, save_bot
from netbbs.chat.bots import mute_remote_bot, set_channel_bot_policy
from netbbs.chat.channels import create_channel
from netbbs.chat.hub import ChatHub, ParticipantId
from netbbs.chat.presence import PresenceRegistry
from netbbs.moderation import ChannelPermission, grant_permissions
from netbbs.net import chat_flow
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def lane(db):
    database_lane = DatabaseLane(db.path)
    yield database_lane
    database_lane.close()


@pytest.fixture
def hub():
    return ChatHub()


@pytest.fixture
def presence():
    return PresenceRegistry()


@pytest.fixture
def alice(db):
    return create_user(db, "alice", password="hunter2", user_level=10)


@pytest.fixture
def bob(db):
    return create_user(db, "bob", password="hunter2", user_level=10)


@pytest.fixture
def carol(db):
    return create_user(db, "carol", password="hunter2", user_level=10)


@pytest.fixture
def channel(db, alice):
    return create_channel(db, "lobby", creator=alice)


# -- command-name completion --------------------------------------------


def test_bare_slash_lists_every_visible_command(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/")
    assert "/quit" in candidates
    assert "/help" in candidates
    assert "/msg" in candidates


def test_command_prefix_matches_only_names_starting_with_it(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/m")
    assert set(candidates) <= {"/msg", "/me", "/mute", "/members"}
    assert "/msg" in candidates
    assert "/me" in candidates
    assert "/members" in candidates  # always visible -- not gated on MANAGE_MEMBERS


def test_command_completion_is_case_insensitive(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert "/quit" in completer("/QU")


def test_non_moderator_does_not_see_moderation_commands_suggested(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/m")
    assert "/mute" not in candidates


def test_moderator_does_see_moderation_commands_suggested(db, lane, hub, presence, alice, channel):
    grant_permissions(
        db, alice, object_type="channel", object_id=channel.id,
        permissions=ChannelPermission.MODERATE, granted_by=alice,
    )
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/m")
    assert "/mute" in candidates


def test_moderation_commands_stay_hidden_from_completion_after_a_moderate_grant_elsewhere(
    db, lane, hub, presence, alice, bob, channel
):
    # A grant on a *different* channel must not leak visibility here --
    # confirms the check is scoped to ctx.channel.id, not global.
    other = create_channel(db, "offtopic", creator=alice)
    grant_permissions(
        db, bob, object_type="channel", object_id=other.id,
        permissions=ChannelPermission.MODERATE, granted_by=alice,
    )
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, bob))
    assert "/mute" not in completer("/m")


def test_unrelated_command_prefix_matches_nothing(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/zzz") == []


# -- membership-admin commands (design doc §8) -------------------------------


def test_non_manager_does_not_see_membership_admin_commands_suggested(db, lane, hub, presence, alice, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/")
    assert "/uninvite" not in candidates
    assert "/grantaccess" not in candidates
    assert "/revokeaccess" not in candidates
    # /invite is a distinct predicate -- checked separately below.
    assert "/invite" not in candidates


def test_manager_does_see_membership_admin_commands_suggested(db, lane, hub, presence, alice, channel):
    grant_permissions(
        db, alice, object_type="channel", object_id=channel.id,
        permissions=ChannelPermission.MANAGE_MEMBERS, granted_by=alice,
    )
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    candidates = completer("/")
    assert "/invite" in candidates
    assert "/uninvite" in candidates
    assert "/grantaccess" in candidates
    assert "/revokeaccess" in candidates


def test_invite_is_suggested_to_a_plain_member_when_the_channel_opts_in(db, lane, hub, presence, alice, bob, channel):
    from netbbs.chat.membership import add_member

    grant_permissions(
        db, alice, object_type="channel", object_id=channel.id,
        permissions=ChannelPermission.MANAGE_MEMBERS, granted_by=alice,
    )
    open_channel = create_channel(
        db, "welcoming", creator=alice, members_only=True, allow_member_invites=True
    )
    grant_permissions(
        db, alice, object_type="channel", object_id=open_channel.id,
        permissions=ChannelPermission.MANAGE_MEMBERS, granted_by=alice,
    )
    add_member(db, open_channel, bob, granted_by=alice)

    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, open_channel, bob))
    candidates = completer("/")
    assert "/invite" in candidates
    # bob has no MANAGE_MEMBERS -- the other three admin commands stay hidden.
    assert "/uninvite" not in candidates


# -- /msg, /private: online usernames only ------------------------------


def test_msg_completes_against_online_usernames(db, lane, hub, presence, alice, bob, channel):
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg bo") == ["bob"]


def test_msg_does_not_suggest_an_offline_user(db, lane, hub, presence, alice, bob, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg bo") == []


def test_private_completes_the_same_way_as_msg(db, lane, hub, presence, alice, bob, channel):
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/private bo") == ["bob"]


def test_query_is_no_longer_a_recognized_command_prefix(db, lane, hub, presence, alice, bob, channel):
    # /query has been removed -- no longer completes at all.
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/query bo") == []
    assert "/query" not in completer("/")


def test_msg_completion_is_case_insensitive(db, lane, hub, presence, alice, bob, channel):
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg BO") == ["bob"]


def test_msg_completion_stops_once_a_full_argument_is_typed(db, lane, hub, presence, alice, bob, channel):
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    # A trailing space after the username means we've moved on to the
    # message body -- free text, no candidates.
    assert completer("/msg bob ") == []


# -- /whois, /finger: the command accepts any registered user, but
# completion *suggestions* are narrowed to this channel's current roster
# (`_roster_usernames`) -- a wall of hundreds of registered usernames
# from one typed character isn't a helpful suggestion list. -------------


def test_whois_completes_against_users_currently_in_the_channel(db, lane, hub, presence, alice, bob, channel):
    hub.join(channel.name, ParticipantId(username="bob", session_key=1))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/whois bo") == ["bob"]


def test_finger_completes_against_users_currently_in_the_channel(db, lane, hub, presence, alice, bob, channel):
    hub.join(channel.name, ParticipantId(username="bob", session_key=1))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/finger bo") == ["bob"]


def test_whois_does_not_suggest_a_registered_user_who_is_not_in_the_channel(
    db, lane, hub, presence, alice, bob, channel
):
    # bob is registered (and could still be /whois'd by typing his name
    # in full -- the command itself is unrestricted) but never joined
    # this channel's roster, so completion has nothing to suggest.
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/whois bo") == []


def test_whois_completion_matches_multiple_candidates(db, lane, hub, presence, alice, bob, carol, channel):
    hub.join(channel.name, ParticipantId(username="alice", session_key=1))
    hub.join(channel.name, ParticipantId(username="bob", session_key=2))
    hub.join(channel.name, ParticipantId(username="carol", session_key=3))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert set(completer("/whois ")) == {"alice", "bob", "carol"}


# -- /invite: registered users who aren't already members --------------------


def test_invite_completes_against_offline_users_too(db, lane, hub, presence, alice, bob, channel):
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/invite bo") == ["bob"]


def test_invite_excludes_users_already_a_member(db, lane, hub, presence, alice, bob, channel):
    from netbbs.chat.membership import add_member
    from netbbs.moderation import ChannelPermission, grant_permissions

    grant_permissions(
        db, alice, object_type="channel", object_id=channel.id,
        permissions=ChannelPermission.MANAGE_MEMBERS, granted_by=alice,
    )
    add_member(db, channel, bob, granted_by=alice)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/invite bo") == []


# -- plain chat text: no candidates --------------------------------------


def test_plain_chat_text_has_no_candidates(db, lane, hub, presence, alice, bob, channel):
    presence.enter("bob")
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("hello bo") == []


# -- /msg, /private: the bots that would answer (issue #1372) ------------

LINKED = "a" * 64
OTHER = "b" * 64


@pytest.fixture
def sysop(db):
    return create_user(db, "sysop", password="hunter2", user_level=255)


def _bot(db, sysop, channel, name, *, enabled=True):
    return save_bot(db, actor=sysop, name=name, channels=[channel.name], defaults=[], enabled=enabled)


def _runtime(*bots, link=None):
    return SimpleNamespace(running_ids=frozenset(bot.id for bot in bots), link=link)


def _link(monkeypatch, channel, roster, *, private=(LINKED, OTHER)):
    """The real `BotLink.visible_roster` -- with its own mute and policy
    filters -- over a roster as a live subscription would have filled it;
    the subscription authorization it also checks is assumed granted."""
    monkeypatch.setattr(bot_link_module, "_decide_channel_subscribe_authorization", lambda db, **kwargs: None)
    monkeypatch.setattr(bot_link_module, "_roster_author_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr(chat_flow, "short_node_name",
                        lambda db, fingerprint: {LINKED: "Relay", OTHER: "Cats Den"}[fingerprint])
    link = object.__new__(bot_link_module.BotLink)
    source = SimpleNamespace(closed=asyncio.Event(), remote_fingerprint=LINKED)
    link.remote = {(channel.channel_id, home): (source, labels) for home, labels in roster.items()}
    link.remote_readers = {}
    link.capable = lambda fingerprint, capability=None: fingerprint in private
    return link


def test_msg_and_private_offer_a_running_local_bot(db, lane, hub, presence, alice, sysop, channel):
    hub.bot_runtime = _runtime(_bot(db, sysop, channel, "Baud"))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg ba") == ["Baud.bot"]
    assert completer("/private BA") == ["Baud.bot"]


def test_msg_offers_a_remote_bot_as_name_at_node(monkeypatch, db, lane, hub, presence, alice, sysop, channel):
    link = _link(monkeypatch, channel, {LINKED: ["Echo.bot"], OTHER: ["Eliza.bot"]})
    hub.bot_runtime = _runtime(link=link)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    # A node name with a space is quoted, so /msg reads the address back whole.
    assert completer("/msg e") == ['Echo.bot@Relay', 'Eliza.bot@"Cats Den"']
    assert completer("/private echo.bot@") == ["Echo.bot@Relay"]


def test_a_remote_bot_whose_node_cannot_answer_privately_is_not_offered(
    monkeypatch, db, lane, hub, presence, alice, channel
):
    link = _link(monkeypatch, channel, {LINKED: ["Echo.bot"], OTHER: ["Eliza.bot"]}, private=(LINKED,))
    hub.bot_runtime = _runtime(link=link)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg e") == ["Echo.bot@Relay"]


def test_muted_bots_are_not_offered(monkeypatch, db, lane, hub, presence, alice, sysop, channel):
    baud, quill = _bot(db, sysop, channel, "Baud"), _bot(db, sysop, channel, "Quill")
    mute_bot(db, channel, baud, actor=sysop, mute=True)
    mute_remote_bot(db, channel, "Echo.bot", LINKED, actor=sysop, mute=True)
    link = _link(monkeypatch, channel, {LINKED: ["Echo.bot", "Ernie.bot"]})
    hub.bot_runtime = _runtime(baud, quill, link=link)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg ") == ["Quill.bot", "Ernie.bot@Relay"]


@pytest.mark.parametrize("policy, offered", [("local", ["Baud.bot"]), ("none", [])])
def test_bots_the_channel_policy_hides_are_not_offered(
    monkeypatch, db, lane, hub, presence, alice, sysop, channel, policy, offered
):
    baud = _bot(db, sysop, channel, "Baud")
    hub.bot_runtime = _runtime(baud, link=_link(monkeypatch, channel, {LINKED: ["Echo.bot"]}))
    set_channel_bot_policy(db, channel, policy, changed_by=sysop)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg ") == offered


def test_a_stopped_bot_is_not_offered(db, lane, hub, presence, alice, sysop, channel):
    stopped = _bot(db, sysop, channel, "Stopped", enabled=False)
    not_running = _bot(db, sysop, channel, "Starting")
    running = _bot(db, sysop, channel, "Baud")
    hub.bot_runtime = _runtime(stopped, running)
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert not_running.label not in completer("/msg ")
    assert completer("/msg s") == []
    assert completer("/msg ") == ["Baud.bot"]


def test_people_come_before_bots(monkeypatch, db, lane, hub, presence, alice, bob, sysop, channel):
    create_user(db, "Zed", password="hunter2", user_level=10)
    presence.enter("bob")
    presence.enter("Zed")
    hub.bot_runtime = _runtime(_bot(db, sysop, channel, "Baud"), _bot(db, sysop, channel, "anna"),
                               link=_link(monkeypatch, channel, {LINKED: ["Echo.bot"]}))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg ") == ["Zed", "bob", "anna.bot", "Baud.bot", "Echo.bot@Relay"]
    assert completer("/msg b") == ["bob", "Baud.bot"]


def test_whois_finger_and_invite_never_offer_bots(monkeypatch, db, lane, hub, presence, alice, sysop, channel):
    hub.bot_runtime = _runtime(_bot(db, sysop, channel, "Baud"),
                               link=_link(monkeypatch, channel, {LINKED: ["Echo.bot"]}))
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    for command in ("/whois ", "/finger ", "/invite "):
        assert not any(".bot" in name for name in completer(command)), command


def test_without_a_bot_runtime_msg_offers_people_only(db, lane, hub, presence, alice, bob, sysop, channel):
    _bot(db, sysop, channel, "Baud")
    presence.enter("bob")
    assert hub.bot_runtime is None
    completer = asyncio.run(chat_flow._build_completer(lane, hub, presence, channel, alice))
    assert completer("/msg ") == ["bob"]
