"""Help-desk mode (§16, issue #1382): the shipped handbook, retrieval, node
facts and their visibility, the source tag, modes and the tolerant loader.
The provider is the fake local endpoint of tests/test_bot_model.py."""
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from netbbs.boards.boards import create_board
from netbbs.bots import helpdesk, model
from netbbs.bots.registry import BotError, save_bot
from netbbs.communities import create_community
from netbbs.config import set_config, set_node_display_name
from netbbs.files.areas import create_file_area
from tests.test_bot_model import configure, exchange, provider
from tests.test_bot_runtime import db, lane, hub, sysop, alice, channel, install  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


# -- the shipped corpus ------------------------------------------------------

def test_shipped_handbook_copy_is_current():
    """Fails when docs/NetBBS-User-Handbook.md changed without
    `python scripts/build_helpdesk_handbook.py`."""
    text = (ROOT / helpdesk.HANDBOOK_SOURCE).read_text(encoding="utf-8")
    shipped = json.loads((ROOT / "src" / "netbbs" / "bots" / helpdesk.CORPUS_FILE).read_text(encoding="utf-8"))
    assert shipped == json.loads(helpdesk.corpus_document(text)), \
        "Stale help-desk handbook: run python scripts/build_helpdesk_handbook.py"
    assert list(helpdesk.load_corpus()) == shipped["sections"]


def test_corpus_is_plain_text_split_at_headings():
    sections = helpdesk.load_corpus()
    assert len(sections) > 40
    for section in sections:
        for markup in ("<kbd>", "</kbd>", "**", "`", "> [!", "| ---", "<details>", "](#", "&lt;", "&gt;", "&amp;"):
            assert markup not in section["text"], (section["title"], markup)
        assert section["title"] and section["chapter"] and section["text"]
    exchange_files = next(s for s in sections if s["title"] == "Exchange files")
    assert "[U] Upload" in exchange_files["text"] and "Where: [F] Files on the main menu" in exchange_files["text"]
    password = next(s for s in sections if s["title"] == "Change your password")
    assert password["chapter"] == "Connect and sign in"
    # Alerts keep their words without the marker; a chapter's contents list
    # only repeats headings and is dropped.
    built = helpdesk.build_corpus("## A\n\n> [!TIP]\n> Use SSH.\n\nIn this chapter: [B](#b) ·\n[C](#c)\n\nBody.\n")
    assert built == [{"chapter": "A", "title": "A", "text": "Tip:\nUse SSH.\n\nBody."}]


@pytest.mark.parametrize("prompt,title,chapter", [
    ("how do I upload a file?", "Exchange files", "Exchange files"),
    ("I forgot my password", "Change your password", "Connect and sign in"),
    ("what is a poll", "Polls", "Read and post"),
    ("what is MRC", "MRC: chat across BBSes", "Chat and mail"),
    ("zmodem with syncterm", "Zmodem", "Exchange files"),
    ("where can I play games", "Play a door game", "Play a door game"),
    ("how do I log out", "Logging off and reporting problems", "Preferences and help"),
    ("change my username", "Change your username", "Connect and sign in"),
])
def test_keyword_retrieval_picks_the_section(prompt, title, chapter):
    picked = helpdesk.retrieve(prompt)
    assert picked[0]["title"] == title and picked[0]["chapter"] == chapter
    assert 1 <= len(picked) <= helpdesk.MAX_EXCERPTS
    assert sum(len(helpdesk.excerpt_text(s).encode()) + 2 for s in picked) <= helpdesk.EXCERPT_BYTES


def test_retrieval_without_match_and_oversized_best_section():
    assert helpdesk.retrieve("what is the weather") == []
    sections = [{"chapter": "Big", "title": "Upload", "text": "upload " * 2000},
                {"chapter": "Small", "title": "Other", "text": "upload once"}]
    picked = helpdesk.retrieve("upload", sections, cap=500)
    assert len(picked) == 1 and picked[0]["title"] == "Upload"
    assert len(helpdesk.excerpt_text(picked[0]).encode()) <= 500


def test_source_tag_fits_the_reply_cap():
    shown, answer = helpdesk.with_source("x" * 600, "Exchange files", 400)
    assert len(shown) == 400 and shown.endswith(" (Handbook: Exchange files. May be wrong; ? for help)")
    assert shown.startswith(answer) and len(answer) < 400
    shown, _ = helpdesk.with_source("x" * 600, "Aliases and names from other BBSes", 80)
    assert len(shown) == 80 and shown.endswith("(From the handbook. May be wrong; ? for help)")
    assert helpdesk.with_source("short", None, 80) == ("short", "short")


# -- settings: modes and the tolerant loader --------------------------------

def test_tolerant_loader_ignores_unknown_and_defaults_missing_keys():
    old_default = model.DEFAULT_PERSONAS[model.COMPANION]
    # A node saved before modes existed: no mode, the old shipped persona.
    legacy = model.load_settings(json.dumps({"enabled": True, "model": "m", "persona": old_default}))
    assert legacy.mode == model.HELPDESK and legacy.persona == model.DEFAULT_PERSONAS[model.HELPDESK]
    assert legacy.enabled and legacy.reply_chars == model.ModelSettings().reply_chars
    # A newer wheel's field and mode never break this reader.
    newer = model.load_settings(json.dumps({"mode": "librarian", "future_field": [1], "persona": "Mine."}))
    assert newer.mode == model.HELPDESK and newer.persona == "Mine." and not hasattr(newer, "future_field")
    assert model.load_settings(None) == model.ModelSettings() == model.load_settings("[]")
    companion = model.load_settings(json.dumps({"mode": "companion", "persona": model.DEFAULT_PERSONAS[model.HELPDESK]}))
    assert companion.persona == old_default


def test_mode_validation_persona_follows_mode_and_save_bumps_revision(db, sysop):
    with pytest.raises(BotError, match="help desk or companion"):
        model.validate_settings(replace(model.ModelSettings(), mode="other"))
    first = model.save_settings(db, model.ModelSettings(), actor=sysop, api_key="")
    assert first.mode == model.HELPDESK and model.settings(db) == first
    second = model.save_settings(db, replace(first, mode=model.COMPANION), actor=sysop, api_key="")
    assert second.persona == model.DEFAULT_PERSONAS[model.COMPANION] and second.revision != first.revision
    custom = model.save_settings(db, replace(second, persona="Be terse.", mode=model.HELPDESK), actor=sysop, api_key="")
    assert custom.persona == "Be terse." and model.settings(db) == custom


def test_disclosure_names_the_grounding():
    helpdesk_text = model.disclosure(model.ModelSettings())
    assert "handbook excerpts and node facts" in helpdesk_text and "you can see" not in helpdesk_text
    assert "boards and areas you can see" in model.disclosure(model.ModelSettings(), private=True)
    companion = replace(model.ModelSettings(), mode=model.COMPANION)
    assert "handbook" not in model.disclosure(companion, private=True)
    longest = replace(model.ModelSettings(), endpoint="https://" + "x" * 172, context_lines=20, log_prompts=True)
    assert len(model.disclosure(longest, private=True)) <= 500


# -- grounding through the fake provider ------------------------------------

def _resources(db, sysop):
    create_board(db, "Lobby talk", description="Anything goes", creator=sysop)
    create_board(db, "Members only", min_read_level=10, creator=sysop)
    hidden = create_community(db, "Back room", hidden=True, creator=sysop)
    create_board(db, "Hidden board", community_id=hidden.id, creator=sysop)
    create_file_area(db, "Uploads", description="Public files", creator=sysop)
    create_file_area(db, "Staff files", min_read_level=200, creator=sysop)


def _system(calls):
    return calls[-1][0]["messages"][0]["content"]


def test_helpdesk_grounding_public_answer_and_source_tag(db, lane, sysop, alice, channel):
    _resources(db, sysop)
    set_node_display_name(db, "Roanoke BBS")
    set_config(db, "mrc_enabled", "1")
    bot = install(db, sysop, channel, model_conversation=True)
    caller = {"id": alice.id, "name": alice.username}

    async def run():
        async with provider() as (endpoint, calls, control):
            configure(db, sysop, endpoint)
            control["text"] = "Open [F] Files, pick an area, then press [U] Upload."
            service = model.ModelService(lane)
            assert "handbook excerpts" in await exchange(service, bot, channel, caller, "notice")
            text = await exchange(service, bot, channel, caller, "how do I upload a file?")
            assert text == ("Open [F] Files, pick an area, then press [U] Upload. "
                            "(Handbook: Exchange files. May be wrong; ? for help)")
            messages = calls[-1][0]["messages"]
            assert [m["content"] for m in messages[1:]] == ["how do I upload a file?"]
            system = messages[0]["content"]
            assert system.startswith(model.DEFAULT_PERSONAS[model.HELPDESK])
            assert helpdesk.RULES in system and "[Exchange files]" in system
            assert "Node name: Roanoke BBS." in system and "Run by SysOp sysop." in system
            assert "This node has MRC inter-BBS chat." in system and "no browser links" in system
            assert "Lobby talk (Anything goes)" in system and "Uploads (Public files)" in system
            for private in ("Members only", "Hidden board", "Staff files"):
                assert private not in system
            assert "Right now" not in system  # no stats grant, no presence
            # Context keeps the model's words, not the host's tag.
            lines = service.contexts[(bot.id, str(alice.id), channel.id)].lines
            assert lines[-1]["content"] == "Open [F] Files, pick an area, then press [U] Upload."
            # Grounding stays within its caps.
            assert len(system.encode()) <= len(model.DEFAULT_PERSONAS[model.HELPDESK]) + 200 + helpdesk.GROUNDING_BYTES
            control["text"] = "I do not know; try [?] Help."
            assert await exchange(service, bot, channel, caller, "what is the weather") == "I do not know; try [?] Help."
            assert "none matched" in _system(calls)
    asyncio.run(run())


def test_private_answer_names_the_callers_own_resources(db, lane, sysop, alice, channel):
    _resources(db, sysop)
    bot = install(db, sysop, channel, model_conversation=True)

    async def run():
        async with provider() as (endpoint, calls, control):
            configure(db, sysop, endpoint)
            service = model.ModelService(lane)
            for caller, private, seen, unseen in (
                ({"id": alice.id, "name": alice.username}, True, ["Members only", "Lobby talk"],
                 ["Hidden board", "Staff files"]),
                ({"id": sysop.id, "name": sysop.username}, True, ["Hidden board", "Staff files"], []),
                ({"id": alice.id, "name": alice.username}, False, ["Lobby talk"], ["Members only"]),
            ):
                assert "Submit again" in await exchange(service, bot, channel, caller, "notice", private=private)
                await exchange(service, bot, channel, caller, "which boards are there", private=private)
                system = _system(calls)
                assert all(name in system for name in seen), system
                assert not any(name in system for name in unseen), system
    asyncio.run(run())


def test_link_caller_always_gets_the_public_set(db, sysop):
    _resources(db, sysop)
    link_caller = {"id": "link:" + "a" * 64 + ":remote", "name": "remote"}
    boards, areas = helpdesk.resources_for(db, link_caller, private=True)
    assert [b.name for b in boards] == ["Lobby talk"] and [a.name for a in areas] == ["Uploads"]


def test_stats_only_with_the_grant(db, lane, sysop, alice, channel):
    bot = install(db, sysop, channel, model_conversation=True)
    caller = {"id": alice.id, "name": alice.username}

    async def run():
        async with provider() as (endpoint, calls, control):
            configure(db, sysop, endpoint)
            service = model.ModelService(lane)
            await exchange(service, bot, channel, caller, "notice")
            await exchange(service, bot, channel, caller, "is anyone online", online={"alice"}, uptime_seconds=3720)
            assert "Right now" not in _system(calls)
            granted = save_bot(db, actor=sysop, bot_id=bot.id, name=bot.name, channels=[channel.name],
                               defaults=[channel.name], enabled=True, model_conversation=True, queries=["stats"])
            await exchange(service, granted, channel, caller, "is anyone online", online={"alice"}, uptime_seconds=3720)
            assert "Right now: " in _system(calls) and "1 human accounts online; uptime 1h 2m" in _system(calls)
    asyncio.run(run())


def test_companion_mode_sends_no_grounding_and_adds_no_tag(db, lane, sysop, alice, channel):
    create_board(db, "Lobby talk", creator=sysop)
    bot = install(db, sysop, channel, model_conversation=True)
    caller = {"id": alice.id, "name": alice.username}

    async def run():
        async with provider() as (endpoint, calls, control):
            configure(db, sysop, endpoint, mode=model.COMPANION)
            service = model.ModelService(lane)
            assert "handbook" not in await exchange(service, bot, channel, caller, "notice")
            assert await exchange(service, bot, channel, caller, "how do I upload a file?") == "A short model answer."
            assert _system(calls) == (model.DEFAULT_PERSONAS[model.COMPANION] +
                                      f"\nYour chat identity is {bot.label}. Use this name when identifying yourself.")
    asyncio.run(run())


def test_editor_mode_field_swaps_only_a_default_persona(db, lane, sysop, monkeypatch):
    from netbbs.net import bot_model_flow
    from netbbs.net.admin_ui import _load_chrome
    from tests.test_chat_flow_moderation import FakeSession

    async def editor(session, lane, **kwargs):
        draft, fields = kwargs["draft"], {f.key: f for f in kwargs["fields"]}
        assert draft["mode"] == model.HELPDESK and fields["mode"].render(draft) == "Help desk (node handbook)"
        await fields["mode"].prompt(session, lane, draft)
        assert draft["mode"] == model.COMPANION and draft["persona"] == model.DEFAULT_PERSONAS[model.COMPANION]
        fields["mode"].step(draft, 1)
        assert draft["persona"] == model.DEFAULT_PERSONAS[model.HELPDESK]
        draft["persona"] = "My own words."
        fields["mode"].step(draft, -1)
        assert draft["mode"] == model.COMPANION and draft["persona"] == "My own words."
        assert "help desk" in fields["day_tokens"].help
        return await kwargs["save"](draft)
    monkeypatch.setattr(bot_model_flow, "edit_resource_draft", editor)

    async def run():
        result = await bot_model_flow.model_editor(FakeSession(), lane, sysop, await _load_chrome(lane, sysop))
        assert result.mode == model.COMPANION and result.persona == "My own words."
        assert asdict(model.settings(db)) == asdict(result)
    asyncio.run(run())
