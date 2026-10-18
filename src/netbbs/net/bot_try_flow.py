"""The console's Try screen for a chat bot (issue #1374, decision 4).

Reached from the bot editor's **Try** field (the draft) and the Chat bots
list's `[T]ry selected` (the saved instance). The bot runs on a scratch node
(`netbbs.bots.trial`); the SysOp types as a caller and sees each host event,
each bot action with its result, the chat lines as callers would see them,
and a bounded stderr excerpt. Nothing is kept, so nothing is asked first.
"""

from __future__ import annotations

import json

from netbbs.auth.users import AuthError
from netbbs.bots.registry import BotError
from netbbs.bots.trial import STDERR_LIMIT, Trial, TrialSpec
from netbbs.chat.bots import BotNotice
from netbbs.net.char_input import InputCancelled
from netbbs.net.location import crumbs, entered
from netbbs.net.notices import announce
from netbbs.rendering import (
    ACCENT_COLOR, ERROR_COLOR, LABEL_COLOR, METADATA_COLOR, MUTED_COLOR, SUCCESS_COLOR, VALUE_COLOR,
    WARNING_COLOR, colored, sanitize_text,
)
from netbbs.rendering.layout import screen_title

_SHOWN = 160
TRY_COMMANDS = "/join <name>, /leave <name>, /post <title>, /file <name>, /quit"


def _cut(text, limit=_SHOWN):
    text = sanitize_text(str(text))
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _quoted(text):
    return '"' + _cut(text) + '"'


def _compact(value):
    return _cut(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def describe_host(event):
    kind = event.get("type")
    caller = (event.get("caller") or {}).get("name", "")
    if kind == "hello":
        return f"hello version {event.get('version')}, channels " + (
            ", ".join("#" + c for c in event.get("channels", [])) or "none")
    if kind == "message":
        return f"message {caller} {_quoted(event.get('text', ''))}"
    if kind == "channel":
        return f"channel {event.get('kind')} {caller} {_quoted(event.get('text', ''))}"
    if kind == "presence":
        return f"presence {event.get('kind')} {caller} #{event.get('channel')}"
    if kind in {"post", "file"}:
        return f"{kind} {_quoted(event.get('text', ''))}"
    if kind == "result":
        if event.get("ok"):
            value = event.get("value")
            return f"result {event.get('id')} ok" + ("" if value is None else " " + _compact(value))
        return f"result {event.get('id')} refused: {_cut(event.get('error', ''))}"
    return _compact(event)


def describe_bot(action):
    if action.get("type") == "ready":
        return f"ready version {action.get('version')}"
    kind = action.get("action")
    if kind == "say":
        return f"say #{action.get('channel')} {_quoted(action.get('text', ''))}"
    if kind == "reply":
        return f"reply {_quoted(action.get('text', ''))}"
    if kind == "state.set":
        return "state.set " + _compact(action.get("value"))
    if kind == "query":
        return f"query {action.get('query')} {_cut(action.get('target') or '')}".rstrip()
    if kind in {"announce", "state.get", "model", "model.reset"}:
        return str(kind)
    return _compact(action)


async def _render(session, lane_db_render, trial, entry):
    if entry.kind == "host":
        return colored("host  > ", fg_color=METADATA_COLOR) + colored(describe_host(entry.value), fg_color=(
            ERROR_COLOR if entry.value.get("type") == "result" and not entry.value.get("ok") else MUTED_COLOR))
    if entry.kind == "bot":
        return colored("bot   > ", fg_color=ACCENT_COLOR) + colored(describe_bot(entry.value), fg_color=VALUE_COLOR)
    if entry.kind == "status":
        state, error = entry.value
        color = SUCCESS_COLOR if state == "running" else ERROR_COLOR if state == "restarting" else WARNING_COLOR
        return colored(f"status: {state}" + (f" ({_cut(error)})" if error else ""), fg_color=color)
    message = entry.value
    if isinstance(message, BotNotice):
        return colored(("(to you) " if message.private else "") + sanitize_text(message), fg_color=MUTED_COLOR)
    if isinstance(message, str):
        return colored(sanitize_text(message), fg_color=MUTED_COLOR)
    return await lane_db_render(message)


async def _flush(session, trial, shown, stderr_shown):
    from netbbs.net.chat_flow import _render_channel_message

    async def render_chat(message):
        return await trial.lane.run(_render_channel_message, trial.channel, trial.user, message,
                                    self_message=message.author_label == trial.user.username)

    for entry in trial.log.since(shown):
        line = await _render(session, render_chat, trial, entry)
        if line:
            await session.write_line(line)
    errors = trial.log.stderr_since(stderr_shown)
    if errors:
        await session.write_line(colored(f"stderr (last {STDERR_LIMIT // 1024} KiB kept):", fg_color=LABEL_COLOR))
        for row in errors.rstrip("\n").splitlines():
            await session.write_line(colored("  " + sanitize_text(row), fg_color=WARNING_COLOR))
    return trial.log.count, trial.log.stderr_total


async def try_bot(session, lane, actor, spec: TrialSpec, chrome):
    from netbbs.bots.model import settings as model_settings
    label = f"{spec.name}.bot"
    with entered(session, f"Try {label}"):
        model_enabled = spec.model_conversation and (await lane.run(model_settings)).enabled
        trial = Trial(spec, caller=actor.username, model_enabled=model_enabled)
        try:
            await trial.start()
        except (BotError, AuthError, ValueError, OSError) as exc:
            # Carried into the draft's or list's redraw (§3.5).
            announce(session, f"Try could not start {label}: {exc}", tone="error")
            return
        try:
            dot = " · " if chrome.unicode_style else " - "
            await session.write_line("\r\n" + screen_title(
                f"Try {label}{dot}scratch #{trial.channel.name}", breadcrumb=crumbs(session, own=True),
                width=session.terminal_width, clear=chrome.redraw_in_place, unicode_style=chrome.unicode_style,
                collapsed=chrome.collapsed, header_color=chrome.header_color,
                node_name_gradient=session.node_name_gradient))
            await session.write_line(colored(
                "A scratch node: its own database, state and channel. Nothing here reaches your node or its "
                "callers, and model requests are rehearsed, never sent.", fg_color=MUTED_COLOR))
            await session.write_line(colored("Grants: ", fg_color=LABEL_COLOR)
                                     + colored(", ".join(spec.grants()), fg_color=VALUE_COLOR))
            await session.write_line(colored(
                f"Type lines as {actor.username}, addressed as in a channel. {TRY_COMMANDS}. "
                "Enter alone shows what happened since.", fg_color=MUTED_COLOR))
            await trial.wait_ready()
            await trial.settle()
            shown = stderr_shown = 0
            while True:
                await trial.note_status()
                shown, stderr_shown = await _flush(session, trial, shown, stderr_shown)
                await session.write(colored(f"{actor.username} > ", fg_color=ACCENT_COLOR))
                try:
                    text = (await session.read_line(cancellable=True)).strip()
                except InputCancelled:
                    break
                command, _, rest = text.partition(" ")
                rest = rest.strip()
                try:
                    if command.lower() in {"/quit", "/q"}:
                        break
                    elif command.lower() in {"/join", "/leave"} and rest:
                        await trial.presence_event(command[1:].lower(), rest)
                    elif command.lower() == "/post" and rest:
                        await trial.post(rest)
                    elif command.lower() == "/file" and rest:
                        await trial.file(rest)
                    elif text.startswith("/"):
                        await session.write_line(colored(f"Try knows {TRY_COMMANDS}.", fg_color=MUTED_COLOR))
                    elif text:
                        await trial.line(text)
                except BotError as exc:
                    await session.write_line(colored(sanitize_text(str(exc)), fg_color=ERROR_COLOR))
                except Exception as exc:  # noqa: BLE001 - a scratch write refused (a bad file name, say)
                    await session.write_line(colored(_cut(f"Try could not do that: {exc}"), fg_color=ERROR_COLOR))
                await trial.settle()
        finally:
            await trial.close()
        announce(session, f"Try of {label} ended. Nothing it did was kept.", tone="muted")
