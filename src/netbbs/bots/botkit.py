"""botkit: the NetBBS bot API in one standard-library file.

A chat bot is a program that reads one JSON object per line on stdin and
writes one per line on stdout (docs/NetBBS-bot-api.md). This helper owns the
plumbing, so a bot is only its own handlers:

- the handshake: it answers ``ready`` only to a ``hello`` of the one API
  version it speaks, and refuses any other with a line on stderr;
- action ids, and matching each result to the callback waiting for it, with
  any number of results outstanding;
- dispatch by event type: ``on_message``, ``on_channel``, ``on_presence``,
  ``on_tick``, ``on_post``, ``on_file``, ``on_held``,
  ``on_door``; a type without a handler
  is ignored;
- a state cache whose writes are saved in order (``save`` and ``change``);
- ``reply`` with the caller's name in front, cut to the 500-character limit,
  and ``reply_block`` for a titled block, cut to the block's bounds;
- flushing every line.

A minimal bot::

    from botkit import Bot, run      # or: from netbbs.bots.botkit import ...

    class Echo(Bot):
        def on_message(self, msg):
            msg.reply("you said " + msg.text)

    run(Echo)

Waiting for a result is a callback::

    msg.query("stats", then=lambda result: msg.reply(result.text))

It imports nothing from NetBBS. Copy it next to your bot to run it with any
Python 3.9 or later; the copy must match the host's BOT_API_VERSION.
"""

import json
import sys
from collections import deque

API_VERSION = 1
LINE_LIMIT = 500
# A titled block's bounds (issue #1373): its title, its rows, a row's label
# and text. The host refuses a block outside them; reply_block cuts to them.
BLOCK_TITLE_LIMIT = 40
BLOCK_ROWS_LIMIT = 12
BLOCK_LABEL_LIMIT = 24
BLOCK_TEXT_LIMIT = 200
# Commands that wait in change() for earlier saves; one more is refused.
MAX_CHANGES = 32


class Event(dict):
    """One host event. Fields read as keys or attributes (``event.channel``);
    a field the host did not send reads as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)


class Result(Event):
    """The host's answer to one action: ``ok``, ``value`` and ``error``."""

    @property
    def text(self):
        """A query's text, or the reason the action was refused."""
        if not self.get("ok"):
            return self.get("error") or ""
        value = self.get("value")
        return value.get("text", "") if isinstance(value, dict) else ""


class Message(Event):
    """An addressed request. Its token lets the bot answer this caller, ask
    the node a query for them, or hand them to the model."""

    bot = None

    @property
    def name(self):
        return (self.get("caller") or {}).get("name", "")

    def reply(self, text, then=None):
        return self.bot.act("reply", then, request=self["request"], text=f"{self.name}: {text}"[:LINE_LIMIT])

    def reply_block(self, title, rows, then=None, private=None):
        """Answer with a titled block, drawn like ``/who``: ``rows`` are
        ``(label, text)`` pairs or plain texts, a label of ``""`` meaning
        none. Cut to the block's bounds: a title of 40 characters, 12 rows,
        a label of 24 and a text of 200. The host titles it with the bot's
        name, so no caller name goes in front. Check ``bot.blocks`` first:
        a host that does not take blocks refuses the action."""
        shaped = []
        for row in list(rows)[:BLOCK_ROWS_LIMIT]:
            label, text = row if isinstance(row, (tuple, list)) else ("", row)
            text = " ".join(str(text).split())[:BLOCK_TEXT_LIMIT] or "-"
            label = " ".join(str(label or "").split())[:BLOCK_LABEL_LIMIT]
            shaped.append({"label": label, "text": text} if label else {"text": text})
        fields = {"request": self["request"], "block": {"title": " ".join(str(title).split())[:BLOCK_TITLE_LIMIT],
                                                         "rows": shaped}}
        if private is not None:
            fields["private"] = bool(private)
        return self.bot.act("reply", then, **fields)

    def query(self, query, target="", then=None):
        return self.bot.act("query", then, query=query, request=self["request"], target=target)

    def model(self, then=None):
        return self.bot.act("model", then, request=self["request"])

    def model_reset(self, then=None):
        return self.bot.act("model.reset", then, request=self["request"])

    def tell(self, to, text, then=None):
        """Hold a note from this caller for local account ``to``, said in this
        channel on their next join. The host proves the sender."""
        return self.bot.act("tell", then, request=self["request"], to=to, text=text)

    def tells(self, then=None):
        """This caller's own waiting tells in this channel."""
        return self.bot.act("tell.list", then, request=self["request"])

    def cancel_tell(self, tell_id, then=None):
        return self.bot.act("tell.cancel", then, request=self["request"], tell_id=tell_id)


class Bot:
    """Subclass this and add ``on_<event type>`` methods."""

    def __init__(self, hello, emit=None):
        self.hello = Event(hello)
        self.name = hello.get("name", "")
        self.channels = list(hello.get("channels") or [])
        # What the SysOp granted. Read these lists, not "scope": the host
        # always sends "addressed" there, whatever else is granted.
        self.queries = frozenset(hello.get("queries") or [])
        self.reading_channels = list(hello.get("reading_channels") or [])
        self.event_channels = list(hello.get("event_channels") or [])
        self.board_sources = list(hello.get("board_sources") or [])
        self.file_sources = list(hello.get("file_sources") or [])
        self.door_sources = list(hello.get("door_sources") or [])
        self.announcement_channel = hello.get("announcement_channel")
        self.greetings = bool(hello.get("greetings", False))
        self.announce_posts = bool(hello.get("announce_posts", False))
        self.announce_files = bool(hello.get("announce_files", False))
        self.announce_doors = bool(hello.get("announce_doors", False))
        self.model_conversation = bool(hello.get("model_conversation", False))
        # Whether the host takes a reply's titled block (issue #1373).
        self.blocks = bool(hello.get("blocks", False))
        # The IANA name of the node's display clock ("UTC" from an older host).
        self.timezone = hello.get("timezone") or "UTC"
        self.state = hello.get("state")
        self.emit = emit or _print
        self._sequence = 0
        self._waiting = {}
        self._unsaved = 0
        self._changes = deque()

    # -- actions. Every kind goes through act(), so a new one is one method.

    def act(self, action, then=None, **fields):
        """Send one action. ``then(result)`` runs when its result arrives.
        Returns the action's id."""
        self._sequence += 1
        identity = str(self._sequence)
        if then is not None:
            self._waiting[identity] = then
        self.emit({"id": identity, "action": action, **fields})
        return identity

    def say(self, channel, text, then=None):
        return self.act("say", then, channel=channel, text=text[:LINE_LIMIT])

    def announce(self, event, then=None):
        return self.act("announce", then, event=event["event"])

    def say_for(self, channel, caller, text, held_text=None, expires=7 * 86400, private=False, then=None):
        """Say ``text`` for one caller now if they are in ``channel``, or hold
        ``held_text`` (default ``text``) for their next join there, for at most
        ``expires`` seconds; ``private`` shows it to that local caller alone.
        The outcome of a held line arrives as ``on_held``."""
        fields = {"channel": channel, "caller": caller, "text": text[:LINE_LIMIT], "expires": expires}
        if private:
            fields["private"] = True
        if held_text is not None:
            fields["held_text"] = held_text[:LINE_LIMIT]
        return self.act("say.for", then, **fields)

    def held_lines(self, then):
        """This bot's lines still held for absent callers (``held.list``)."""
        return self.act("held.list", then)

    def cancel_held(self, held_id, then=None):
        return self.act("held.cancel", then, held_id=held_id)

    def load(self, then):
        """Read the saved state again (``state.get``)."""
        return self.act("state.get", then)

    def save(self, value, then=None):
        """Save ``value`` as the bot's state. ``self.state`` becomes it once
        the host has stored it; a refused save leaves ``self.state`` as it was."""
        self._unsaved += 1

        def saved(result):
            self._unsaved -= 1
            if result.ok:
                self.state = value
            if then is not None:
                then(result)
            while not self._unsaved and self._changes:
                self._changes.popleft()()

        return self.act("state.set", saved, value=value)

    def change(self, step):
        """Run ``step()`` once every save already sent has its result, so it
        reads the saved state. A command that reads the state and saves a
        new one goes through here, so two of them never build on the same
        stale copy. Returns False, running nothing, when MAX_CHANGES are
        already waiting."""
        if not self._unsaved and not self._changes:
            step()
            return True
        if len(self._changes) >= MAX_CHANGES:
            return False
        self._changes.append(step)
        return True

    @property
    def saving(self):
        """True while a save is outstanding or a change waits for one."""
        return bool(self._unsaved or self._changes)

    # -- events

    def handle(self, event):
        kind = event.get("type")
        if kind == "result":
            then = self._waiting.pop(event.get("id"), None)
            if then is not None:
                then(Result(event))
            return
        if not isinstance(kind, str) or not kind.isidentifier():
            return
        handler = getattr(self, "on_" + kind, None)
        if handler is None:
            return
        if kind == "message":
            event = Message(event)
            event.bot = self
        else:
            event = Event(event)
        handler(event)


def _print(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def run(bot_class, stdin=None):
    """Speak the protocol on stdin and stdout until the host closes stdin."""
    stdin = stdin or sys.stdin
    try:
        hello = json.loads(stdin.readline() or "{}")
    except ValueError:
        hello = {}
    if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("version") != API_VERSION:
        version = hello.get("version") if isinstance(hello, dict) else None
        print(f"botkit speaks BOT API version {API_VERSION}; the host offered version {version!r}. "
              "Use the botkit.py that ships with this NetBBS.", file=sys.stderr, flush=True)
        sys.exit(1)
    bot = bot_class(hello)
    _print({"type": "ready", "version": API_VERSION})
    for line in stdin:
        if line.strip():
            bot.handle(json.loads(line))
