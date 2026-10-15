"""Node-wide provider settings and visible model accounting for SysOps."""
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from netbbs.bots.model import (
    FIELD_LABELS, LIMIT_LABELS, MONEY_KEYS, ModelSettings, SECRET_KEY, settings, save_settings, validate_settings,
    usage_summary, disclosure,
)
from netbbs.bots.registry import BotError, require_sysop
from netbbs.config import get_config
from netbbs.net.confirm import prompt_yes_no
from netbbs.net.menu_description_preference import menu_description_level
from netbbs.net.resource_editor import (
    FieldSpec, InputCancelled, bool_field, bool_step, choice_field, choice_step,
    edit_resource_draft, inline_field, read_field_line, text_field, write_field_prompt,
)
from netbbs.rendering import menu_key, sanitize_text
from netbbs.rendering.detail import Field, Note, Section, Table

MONEY = MONEY_KEYS


async def model_editor(session, lane, actor, chrome):
    from netbbs.net.admin_fields import _int_field

    await lane.run(require_sysop, actor)
    original = await lane.run(settings)
    draft = asdict(original)
    for key, value in list(draft.items()):
        if key in MONEY:
            draft[key] = str(Decimal(value) / 1000000)
    draft["api_key"] = await lane.run(get_config, SECRET_KEY, "")

    @inline_field
    async def secret(session, lane, values):
        await write_field_prompt(session, "API key (typed unseen; empty clears):")
        try:
            values["api_key"] = (await read_field_line(session, initial="", echo=False)).strip()
        except InputCancelled:
            return

    async def save(values):
        converted = {k: v for k, v in values.items() if k != "api_key"}
        for key, default in asdict(ModelSettings()).items():
            if type(default) is int:
                try:
                    number = Decimal(str(values[key])) * (1000000 if key in MONEY else 1)
                    if not number.is_finite() or number != number.to_integral_value():
                        raise ValueError
                    converted[key] = int(number)
                except (InvalidOperation, ValueError, OverflowError):
                    kind = "an amount with up to 6 decimal places" if key in MONEY else "a whole number"
                    raise BotError(f"{FIELD_LABELS[key]} must be {kind}.") from None
        value = validate_settings(ModelSettings(**converted))
        if value.enabled and (not original.enabled or any(getattr(value, k) != getattr(original, k)
                                                          for k in ("endpoint", "model", "context_lines", "log_prompts"))):
            if not await prompt_yes_no(session, disclosure(value) + " Enable these provider settings?", default=False):
                raise BotError("Provider approval was not granted. Your draft has been kept.")
        return await lane.run(save_settings, value, actor=actor, api_key=values["api_key"])

    def text(key, help, section):
        return FieldSpec(key, FIELD_LABELS[key], lambda d: sanitize_text(str(d[key])) or "(none)", text_field(key),
                         help=help, section=section)

    def number(key, help, section):
        # Checked as it is typed, like every other whole-number console field.
        return FieldSpec(key, FIELD_LABELS[key], lambda d: f"{d[key]:,}", _int_field(key, FIELD_LABELS[key]),
                         help=help, section=section)

    def switch(key, help, section):
        return FieldSpec(key, FIELD_LABELS[key], lambda d: "yes" if d[key] else "no", bool_field(key),
                         step=bool_step(key), help=help, section=section)

    fields = [
        switch("enabled", "Off by default. Each bot also needs its own conversation grant.", "Provider"),
        text("endpoint", "Local first: http://127.0.0.1:11434/v1 for Ollama, or your llama.cpp server's /v1 URL. A remote endpoint receives addressed conversation. Use HTTPS for remote providers.", "Provider"),
        text("model", "An installed local model or the name your provider accepts.", "Provider"),
        FieldSpec("api_key", "API key", lambda d: "(set; hidden)" if d["api_key"] else "(none)", secret,
                  help="Stored like FTN passwords in the protected database and backups. Never sent to bot programs or shown here.", section="Provider"),
        FieldSpec("token_parameter", FIELD_LABELS["token_parameter"], lambda d: d["token_parameter"],
                  choice_field("token_parameter", ["max_completion_tokens", "max_tokens"]),
                  step=choice_step("token_parameter", ["max_completion_tokens", "max_tokens"]),
                  help="Defaults to max_tokens for Ollama. Select max_completion_tokens for current OpenAI models that require it.", section="Provider"),
        text("persona", "Baud's host-owned persona, 1-2,000 characters. No ordinary channel history is included.", "Privacy and access"),
        number("context_lines", "0-20; only this caller's addressed inputs and replies in this channel. Memory only; 30-minute idle expiry and reset.", "Privacy and access"),
        switch("log_prompts", "Off by default. When on, stores each submitted input with the bounded usage ledger. Callers are told. Does not log unrelated chat or provider responses.", "Privacy and access"),
        number("min_level", "0-255. For Link callers, this is the level asserted by their authenticated home node; it grants no other local privilege.", "Privacy and access"),
        switch("allow_guests", "Off by default. Guests share their node's guest account quota and context; they are not separate authenticated people.", "Privacy and access"),
        number("day_tokens", "1-1,000,000,000 tokens; UTC day. Includes reservations for interrupted/unmetered requests.", "Limits"),
        number("month_tokens", "1-10,000,000,000 tokens; UTC month. Reported usage replaces the reserved estimate.", "Limits"),
        number("user_daily", "1-1,000, across all bot instances and channels on this node.", "Limits"),
        number("cooldown", "0-3,600; across all bot instances and channels.", "Limits"),
        number("channel_per_minute", "1-60; additional to each bot's normal output limit.", "Limits"),
        number("reply_chars", "80-500. Model replies remain public and use ordinary wrapped chat output.", "Limits"),
        number("output_tokens", "32-4,096. Passed to the provider and included in each advance reservation.", "Limits"),
        text("input_price", "Your chosen currency; up to 6 decimal places. Zero for local/free use. NetBBS does not look up prices.", "Optional cost accounting"),
        text("output_price", "Use the same currency as input prices and budgets. Provider invoices may include other charges.", "Optional cost accounting"),
        text("day_money", "Zero disables this extra limit; token budgets always apply. Requires configured prices.", "Optional cost accounting"),
        text("month_money", "Zero disables this extra limit. All money figures use your configured currency.", "Optional cost accounting"),
    ]
    saved = await edit_resource_draft(
        session, lane, title="Model conversation", fields=fields,
        draft=draft, save=save, error_type=BotError, save_menu_text=menu_key("S", "ave"), back_menu_text=menu_key("B", "ack"),
        preamble="Optional. A local model (Ollama, llama.cpp) keeps chat on this node. Only addressed conversation is "
                 "sent, after the caller is told. Needs netbbs[bots].",
        redraw_in_place=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
        collapsed=chrome.collapsed, header_color=chrome.header_color,
        accent_color=chrome.accent_color, description_level=await lane.run(menu_description_level, actor),
    )
    if saved is not None:
        from netbbs.net.notices import announce
        announce(session, "Model settings saved. They apply from the next request.")
    return saved


def _money(micros):
    return f"{Decimal(micros) / 1000000:,.2f}" if micros % 10000 == 0 else f"{Decimal(micros) / 1000000:,.6f}"


def _usage_sections(value):
    sections = []
    for key, title in (("day", "Today (UTC)"), ("month", "This month (UTC)")):
        row = value[key]
        rows = [Field("Requests", f"{row['calls']:,}"), Field("Callers", f"{row['users']:,}"),
                Field("Tokens", f"{row['tokens']:,}"), Field("Cost", _money(row["money"]))]
        if row["estimated"]:
            rows.append(Field("Estimated", f"{row['estimated']:,} requests"))
        sections.append(Section(title, rows, paired=True))
    if value["users"]:
        sections.append(Section("Callers this month", [Table(
            ["Caller", "Requests", "Tokens"],
            [[sanitize_text(r["caller"]), f"{r['calls']:,}", f"{r['tokens'] or 0:,}"] for r in value["users"]],
            right_aligned=frozenset({1, 2}),
        )]))
    if value["limits"]:
        sections.append(Section("Limits reached", [Table(
            ["Limit", "Times"],
            [[LIMIT_LABELS.get(r["reason"], r["reason"]), f"{r['hits']:,}"] for r in value["limits"]],
            right_aligned=frozenset({1}),
        )]))
    sections.append(Section(None, [Note(
        "Cost uses your configured prices and currency; no provider invoice is fetched. Estimates count "
        "interrupted or unmetered requests at their reservation. Up to 20,000 requests a UTC month are kept."
    )]))
    return sections


async def model_usage(session, lane, actor):
    from netbbs.net.admin_ui import _show_report

    await lane.run(require_sysop, actor)
    value = await lane.run(usage_summary)
    await _show_report(
        session, lane, actor, "Model usage", sections=_usage_sections(value),
        help_title="Model usage",
        help_about="What model conversation has cost this node: requests, callers, tokens and the limits "
                   "callers ran into.",
    )
