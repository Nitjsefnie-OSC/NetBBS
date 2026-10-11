"""Multi-selection reuses the picker without losing its page or Find filter."""

import asyncio

import pytest

from netbbs.net.picker import pick_item
from tests.test_bot_settings_ui import Screen


@pytest.mark.parametrize("width", [40, 80])
@pytest.mark.parametrize("description_level", ["off", "brief"])
def test_selection_count_and_instructions_fit_with_many_resources(width, description_level):
    items = [f"channel {i:02d}" for i in range(50)]
    session = Screen(["b"], width=width, height=24)

    async def toggle(item):
        pass

    asyncio.run(pick_item(
        session, items, name_of=str, stable_id_of=items.index,
        title="Channels", empty_message="None", on_toggle=toggle,
        masthead="Select rows to toggle; [B]ack returns to the draft. Save the bot to apply changes.",
        live_label=lambda: "32 selected", description_level=description_level,
    ))
    assert len(session.plain.splitlines()) <= 24


def test_toggles_keep_the_page_and_accept_numbers_enter_and_space():
    items = [f"channel {i:02d}" for i in range(50)]
    toggled = []
    session = Screen([">", "0", "1", "1", "ENTER", "DOWN", "ENTER", " ", "b"], height=24)

    async def toggle(item):
        toggled.append(item)

    asyncio.run(pick_item(
        session, items, name_of=str, stable_id_of=items.index,
        title="Channels", empty_message="None", on_toggle=toggle,
    ))
    assert len(toggled) == 4 and len(set(toggled)) == 1
    assert toggled[0] != items[0]


def test_find_never_grants_access_and_number_toggle_keeps_the_filter():
    toggled = []
    session = Screen(["/", "0", "1", "0", "1", "b"], lines=["beta"])

    async def toggle(item):
        toggled.append(item)

    asyncio.run(pick_item(
        session, ["alpha", "beta"], name_of=str, stable_id_of=lambda name: len(name),
        title="Channels", empty_message="None", on_toggle=toggle,
    ))
    assert toggled == ["beta", "beta"]


def test_finding_one_match_and_back_leaves_selection_untouched():
    toggled = []
    session = Screen(["/", "b"], lines=["beta"])

    async def toggle(item):
        toggled.append(item)

    asyncio.run(pick_item(
        session, ["alpha", "beta"], name_of=str, stable_id_of=lambda name: len(name),
        title="Channels", empty_message="None", on_toggle=toggle,
    ))
    assert not toggled
