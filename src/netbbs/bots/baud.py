"""Bundled rule-based bot. Standalone stdlib program using only BOT API v1.

Run by filename so it also works from a source checkout without an installed
package. It has no database imports, filesystem access, or special host grants.
It is written on botkit (issue #1374), the same helper any other bot may use;
botkit is a sibling module, imported from this directory when Baud runs by
filename.
"""

import datetime as dt
import hashlib
import random
import re
import time

try:
    from . import botkit
except ImportError:  # run by filename: sys.path[0] is this directory
    import botkit

VERSION = botkit.API_VERSION
# A refused reminder is retried with backoff, then dropped; it never blocks
# the reminders behind it (a deleted channel or removed grant never heals).
MAX_DELIVERY_ATTEMPTS = 8
LINE_LIMIT = botkit.LINE_LIMIT
HORIZON = 30 * 86400
# A due reminder whose owner is not in its channel is held by the host this
# long, then dropped silently (decision #1375).
HELD_SECONDS = 7 * 86400
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WHEN_USAGE = ("Use remind <when> <text of 1-160 characters>; when is 10m, 2h, 20:00, tomorrow 09:00, "
              "fri 20:00 or 2026-11-01 20:00.")
TELL_USAGE = "Use tell <name> <text of 1-160 characters>."


class WhenError(ValueError):
    """A time the caller wrote that Baud will not guess at; the text says why."""


def zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tzdata, or an unknown name: the UTC clock
        return dt.timezone.utc


def _clock(text):
    match = re.fullmatch(r"([01]?[0-9]|2[0-3]):([0-5][0-9])", text)
    return (int(match[1]), int(match[2])) if match else None


def _instant(day, hour, minute, tz):
    # As backup_schedule._slot_on: a wall time in a spring-forward gap is read
    # at its pre-transition offset, a repeated one at its first occurrence.
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz).astimezone(dt.timezone.utc).timestamp()


def parse_when(rest, now, tz):
    """`(due timestamp, text)` from `remind`'s arguments: a duration, `HH:MM`,
    `tomorrow HH:MM`, a weekday `HH:MM` or `YYYY-MM-DD HH:MM`, in the node clock."""
    words = rest.split(" ", 2)
    first = words[0].lower()
    duration = re.fullmatch(r"([1-9][0-9]{0,6})([smhd])", first)
    if duration:
        seconds = int(duration[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[duration[2]]
        if seconds > HORIZON:
            raise WhenError("Reminders reach at most 30 days ahead.")
        return now + seconds, " ".join(words[1:])
    today = dt.datetime.fromtimestamp(now, tz).date()
    clock = _clock(first)
    if clock is not None:
        due = _instant(today, *clock, tz)
        if due <= now:  # already past today: tomorrow
            due = _instant(today + dt.timedelta(days=1), *clock, tz)
        return due, " ".join(words[1:])
    if re.fullmatch(r"[0-9]{1,4}[./][0-9]{1,2}([./][0-9]{0,4})?", first):
        raise WhenError("Write dates as YYYY-MM-DD: " + first + " could be day or month first.")
    clock = _clock(words[1]) if len(words) > 1 else None
    text = words[2] if len(words) > 2 else ""
    if first == "tomorrow" and clock is not None:
        due = _instant(today + dt.timedelta(days=1), *clock, tz)
    elif first in WEEKDAYS and clock is not None:
        ahead = (WEEKDAYS.index(first) - today.weekday()) % 7
        due = _instant(today + dt.timedelta(days=ahead), *clock, tz)
        if due <= now:  # that weekday is today and the time has passed
            due = _instant(today + dt.timedelta(days=ahead + 7), *clock, tz)
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", first) and clock is not None:
        try:
            day = dt.date.fromisoformat(first)
        except ValueError:
            raise WhenError(f"There is no date {first}.") from None
        due = _instant(day, *clock, tz)
        if due <= now:
            raise WhenError("That time has already passed.")
    else:
        raise WhenError(WHEN_USAGE)
    if due - now > HORIZON:
        raise WhenError("Reminders reach at most 30 days ahead.")
    return due, text


def describe(when, now, tz):
    """`Fri 20:00 CET`, with the date when it is a week or more away."""
    local = dt.datetime.fromtimestamp(when, tz)
    abbreviation = local.strftime("%Z") or "UTC"
    day = DAY_NAMES[local.weekday()]
    if when - now >= 6 * 86400:
        day += local.strftime(" %Y-%m-%d")
    return f"{day} {local:%H:%M} {abbreviation}"

# Issue #1381: the temperament presets are only defaults. The SysOp may
# override each phrase per instance; the host passes the overrides in the
# hello's "phrases" object, and an empty or missing one falls back to the
# preset. "{name}" is the caller's name; "{{" and "}}" are literal braces.
PHRASE_KEYS = ("greeting", "help", "busy", "limit", "full")
PRESETS = {
    "greeting": {"friendly": "Welcome, {name}!", "terse": "Welcome, {name}.",
                 "retro": "Carrier detected: welcome, {name}!"},
    "help": {"friendly": "Happy to help!", "terse": "", "retro": "Carrier detected."},
    "busy": dict.fromkeys(("friendly", "terse", "retro"), "Busy saving reminders; try again shortly."),
    "limit": dict.fromkeys(("friendly", "terse", "retro"),
                           "Reminder limit reached (8 per caller, 48 per bot). Cancel an old reminder first."),
    # Issue #1379: a quote, poll or vote whose save the host refused, which
    # in practice is the 16 KiB state limit. A limit is said, never silent.
    "full": dict.fromkeys(("friendly", "terse", "retro"), "Baud's memory is full; ask the SysOp."),
}

# Issue #1379: dice bounds, quote and poll caps (design doc §16).
MAX_DICE, MAX_SIDES, MAX_MODIFIER = 20, 1000, 1000
QUOTE_TEXT, QUOTES_PER_BOT, QUOTES_PER_ADDER = 160, 30, 10
POLL_QUESTION, POLL_ANSWER, MIN_ANSWERS, MAX_ANSWERS = 100, 30, 2, 6
POLLS_PER_BOT, POLL_VOTERS = 4, 100
POLL_DEFAULT_SECONDS, POLL_MAX_SECONDS = 600, 86400
_dice = random.SystemRandom()


def preset(key, temperament):
    return PRESETS[key].get(temperament, PRESETS[key]["friendly"])


class Baud(botkit.Bot):
    def __init__(self, hello, emit=None):
        super().__init__(hello, emit)
        self.temperament = hello.get("temperament", "friendly")
        phrases = hello.get("phrases")
        self.phrases = phrases if isinstance(phrases, dict) else {}
        state = self.state or {"next": 1, "reminders": []}
        if not isinstance(state, dict) or not isinstance(state.get("reminders", []), list):
            raise ValueError("Invalid Baud reminder state")
        state.setdefault("next", 1)
        state.setdefault("reminders", [])
        if not isinstance(state.get("quotes", []), list) or not isinstance(state.get("polls", []), list):
            raise ValueError("Invalid Baud quote or poll state")
        self.state = state
        # Issue #1379: the SysOp's two switches, the channels shared over
        # Link (where a poll names its explicit address), and this node's
        # name for that address.
        self.quotes_on = bool(hello.get("quotes", False))
        self.polls_on = bool(hello.get("polls", False))
        self.linked = frozenset(hello.get("linked_channels") or [])
        self.node = hello.get("node") or ""
        # The poll whose close is being said, one at a time, as reminders.
        self.closing = None
        self.poll_dropping = None
        self.poll_failures = {}
        self.failures = {}
        # The reminder whose delivery is waiting for its result: one at a
        # time, so a slow result never delivers the same reminder twice.
        self.delivering = None
        self.forgetting = None
        self.tz = zone(self.timezone)
        # Held reminders the host delivered or expired, to remove on the next
        # save; and held ids to record once a save in flight is done.
        self.dropping = set()
        self.marking = {}
        self.reconciled = False

    def phrase(self, key, name):
        # The host validates every phrase on Save; a hand-edited or older
        # host's phrase that does not format still never takes Baud down.
        custom = self.phrases.get(key)
        if isinstance(custom, str) and custom.strip():
            try:
                return custom.format(name=name)
            except Exception:  # noqa: BLE001 - any formatting failure means the preset
                pass
        return preset(key, self.temperament).format(name=name)

    def store(self, state, msg=None, text=None):
        def saved(result):
            if msg is not None:
                msg.reply(text if result.ok else "Could not save that reminder; ask the SysOp.")
        self.save(state, then=saved)

    def delivered(self, reminder_id, ok):
        attempts = self.failures.pop(reminder_id, (0, 0))[0] + (0 if ok else 1)
        if not ok and attempts < MAX_DELIVERY_ATTEMPTS:
            self.failures[reminder_id] = (attempts, time.time() + min(3600, 60 * 2 ** (attempts - 1)))
            self.delivering = None
            return
        self.forget(reminder_id)

    def forget(self, reminder_id):
        # A reminder saved while this one was being said is still in flight:
        # removing it waits for that save, or it would build on the state
        # before it and lose it (#1419 review). Until then `delivering` stays
        # set, so no tick says this reminder again.
        def step():
            self.delivering = None
            self.forgetting = None
            # Forget it in memory first: if the store fails, the next tick must
            # not deliver it again, and the next successful store omits it.
            self.state = {**self.state, "reminders": [r for r in self.state["reminders"] if r["id"] != reminder_id]}
            self.store(self.state)
        if not self.change(step):
            self.forgetting = reminder_id  # the queue is full: retried on the next tick

    def on_post(self, event):
        if self.announce_posts:
            self.announce(event)

    def on_file(self, event):
        if self.announce_files:
            self.announce(event)

    def on_presence(self, event):
        if self.greetings and event.kind == "join":
            self.say(event.channel, self.phrase("greeting", event.caller["name"]))

    def clock(self, when):
        return dt.datetime.fromtimestamp(when, self.tz).strftime("%H:%M")

    def on_held(self, event):
        # The host said a held reminder on its owner's return, or it expired.
        if any(r.get("held") == event.held_id for r in self.state["reminders"]):
            self.dropping.add(event.held_id)
            self.settle_held()

    def settle_held(self):
        """Record held ids and drop finished held reminders, in one save that
        waits for any save in flight (`change`)."""
        def step():
            if not (self.marking or self.dropping):
                return
            reminders = [{**r, "held": self.marking[r["id"]]} if r["id"] in self.marking else r
                         for r in self.state["reminders"] if r.get("held") not in self.dropping]
            if self.delivering in self.marking:
                self.delivering = None
            self.marking, self.dropping = {}, set()
            self.state = {**self.state, "reminders": reminders}
            self.store(self.state)
        if (self.marking or self.dropping) and not self.change(step):
            pass  # the queue is full: retried on the next tick

    def said(self, reminder_id, result):
        value = result.value if result.ok and isinstance(result.value, dict) else {}
        if value.get("held"):
            # Not in the channel: the host holds it for their next join.
            self.marking[reminder_id] = value["held_id"]
            self.settle_held()
        else:
            self.delivered(reminder_id, result.ok)

    def reconcile(self, result):
        # Held lines the host delivered or expired while Baud was not running.
        if result.ok and isinstance(result.value, list):
            waiting = {line.get("held_id") for line in result.value}
            self.dropping |= {r["held"] for r in self.state["reminders"] if r.get("held") and r["held"] not in waiting}
            self.settle_held()

    def on_tick(self, event):
        self._close_due_poll(event.time)
        if not self.reconciled:
            self.reconciled = True
            if any(r.get("held") for r in self.state["reminders"]):
                self.held_lines(self.reconcile)
        if self.forgetting is not None and not self.saving:
            self.forget(self.forgetting)
            return
        if (self.marking or self.dropping) and not self.saving:
            self.settle_held()
            return
        if self.delivering is not None or self.saving:
            return
        for reminder in self.state["reminders"]:
            if (not reminder.get("held") and reminder["due"] <= event.time
                    and self.failures.get(reminder["id"], (0, 0))[1] <= event.time):
                self.delivering = ident = reminder["id"]
                prefix = f"{reminder['name']}: reminder #{ident}"
                self.say_for(reminder["channel"], reminder["user"], f"{prefix}: {reminder['text']}",
                             held_text=f"{prefix} (due {self.clock(reminder['due'])}, held until you came back): "
                                       f"{reminder['text']}",
                             expires=HELD_SECONDS, then=lambda result, ident=ident: self.said(ident, result))
                break

    def on_message(self, msg):
        command, _, rest = msg.text.strip().lstrip("!").partition(" ")
        command = command.lower()
        topic = rest.strip().lower()
        if command in {"", "help"} and topic == "remind":
            msg.reply(f"remind <when> <text>: when is 10m, 2h, 20:00, tomorrow 09:00, fri 20:00 or "
                      f"2026-11-01 20:00, 24-hour, in the node clock ({dt.datetime.now(self.tz).strftime('%Z') or 'UTC'}"
                      "), up to 30 days ahead. I say it publicly here when it is due, or when you next join "
                      "this channel if you are away (within 7 days).", private=msg.private or None)
        elif command in {"", "help"} and topic == "tell":
            msg.reply("tell <name> <text>: a note of up to 160 characters for a local account. I say it "
                      "publicly here when they next join this channel (within 14 days). tells lists yours; "
                      "cancel tell <id> withdraws one. Nobody who blocks you or takes no direct messages "
                      "gets your tells.", private=msg.private or None)
        elif command in {"", "help"}:
            opening = self.phrase("help", msg.name).strip()
            if self.blocks:
                return self._help_block(msg, opening)
            msg.reply((opening + " " if opening else "") + "help; remind <when> <text>; reminders; "
                      "tell <name> <text>; tells; cancel <id>; "
                      + ("stats; " if "stats" in self.queries else "")
                      + ("seen <name>; " if "seen" in self.queries else "")
                      # Issue #1379: compact, so the line fits whole with every
                      # grant and switch after the longest opening, with room
                      # left for another command.
                      + "roll|coin; " + ("quote; " if self.quotes_on else "")
                      + ("poll; " if self.polls_on else "") +
                      "help remind|tell: more. /sysop <reason>: page staff. " +
                      ("Conversation is on; reset clears your context. " if self.model_conversation
                       else "No language model is enabled. ") + self._privacy_note(msg),
                      private=msg.private or None)
        elif command == "tell":
            self._tell(msg, rest.strip())
        elif command == "tells":
            msg.tells(then=lambda result: msg.reply(
                "; ".join(f"tell #{t['tell_id']} to {t['to']}: {t['text'][:35]}" for t in result.value)
                or "No tells of yours wait here.") if result.ok else None)
        elif command == "cancel" and topic.split(" ")[0] == "tell":
            ident = topic[4:].strip().lstrip("#")
            if not ident.isdigit():
                msg.reply("Use cancel tell <id>; tells lists yours.")
            else:
                msg.cancel_tell(int(ident), then=lambda result: msg.reply(
                    f"Cancelled tell #{ident}." if result.value.get("cancelled")
                    else "No waiting tell with that id is yours in this channel.") if result.ok else None)
        elif command in {"stats", "seen"}:
            if command not in self.queries:
                msg.reply("The SysOp has not enabled this query ability.")
            else:
                msg.query(command, rest.strip(), then=lambda result: msg.reply(
                    result.value["text"] if result.ok else result.get("error", "Query unavailable.")))
        elif command in {"reminders", "cancel", "remind"}:
            # These read the reminders: each waits for any save in flight.
            handler = getattr(self, "_" + command)
            if not self.change(lambda: handler(msg, rest.strip())):
                msg.reply(self.phrase("busy", msg.name))
        elif command in {"roll", "coin"}:
            getattr(self, "_" + command)(msg, rest.strip())
        elif command in self._social_commands(msg.channel):
            # Quotes and polls read the state: each waits for a save in flight.
            handler = getattr(self, "_" + command)
            if not self.change(lambda: handler(msg, rest.strip())):
                msg.reply(self.phrase("busy", msg.name))
        elif command == "sysop":
            # Paging is the host's /sysop (decision 1377): a ! line has
            # already gone out publicly, so this only points the way.
            msg.reply("I can't page anyone" + ("" if msg.asked_privately else ", and this line was public") +
                      ". Type /sysop <reason>: only the SysOp and staff see it.")
        elif command == "reset" and self.model_conversation:
            msg.model_reset()
        elif self.model_conversation:
            msg.model()
        else:
            known = ["help", "remind", "reminders", "cancel", "tell", "tells", *sorted(self.queries & {"seen", "stats"}),
                     "roll", "coin", *(["quote"] if self.quotes_on else []), *(["poll", "vote"] if self.polls_on else [])]
            msg.reply(f"I know {', '.join(known[:-1])} and {known[-1]}. Conversation is not enabled.")

    def _privacy_note(self, msg):
        # What is true for this caller (issue #1372). A caller a private
        # answer can reach gets this help, and reminders, privately.
        if not msg.private:
            return "Replies are public."
        name = self.name or "Baud.bot"
        if str(msg.caller["id"]).startswith("link:"):
            name += "@Node"
        return f"Ask privately: /msg {name} <text>."

    def _help_block(self, msg, opening):
        # Issue #1373: the command list as a titled block; the opening
        # phrase heads it, and the line form above is for an older host.
        # At most 12 rows: the opening, five command rows (listing and
        # cancelling share one, issue #1379), the queries, the social
        # commands, /sysop and the model line, which ends with what is
        # private (issue #1372).
        rows = [opening] if opening else []
        rows += [("help", "this list; help remind or help tell for more"),
                 ("remind <when> <text>", "a reminder here, shown publicly"),
                 ("reminders, cancel <id>", "your reminders in every channel; cancel one" if msg.private
                  else "your reminders here; cancel one of yours"),
                 ("tell <name> <text>", "a note for someone's next join here"),
                 ("tells, cancel tell <id>", "your waiting tells; withdraw one")]
        queries = [(name, text) for name, text in (("stats", "who is on, uptime"),
                                                   ("seen <name>", "when someone was last on"))
                   if name.split(" ")[0] in self.queries]
        if queries:
            rows.append((", ".join(name for name, _ in queries), "; ".join(text for _, text in queries)))
        rows.append(("roll [NdS+M], coin", "dice and a coin, said to everyone here"))
        if self.quotes_on:
            rows.append(("quote [<n>|add|del]", "this channel's quotes: one at random, number n, add or delete"))
        if self.polls_on:
            rows.append(("poll, vote <n>", "a quick poll here: poll <question> | <a> | <b>; results at close"))
        rows.append(("/sysop <reason>", "page staff"))
        rows.append(("Conversation is enabled; reset clears your temporary context here. " if self.model_conversation
                     else "No language model is enabled. ") + self._privacy_note(msg))
        msg.reply_block("Commands", rows, private=msg.private or None)

    def _own(self, msg):
        return [r for r in self.state["reminders"] if r["user"] == msg.caller["id"]]

    def _reminders(self, msg, rest):
        def entry(r, where=""):
            return (f"#{r['id']}{where}: {r['text'][:35]}"
                    + (f" (waiting since {self.clock(r['due'])})" if r.get("held") else ""))
        if msg.private:
            # Answered to the caller alone, so every channel's reminders.
            msg.reply("; ".join(entry(r, " " + r["channel"]) for r in self._own(msg))
                      or "You have no reminders.", private=True)
            return
        # A public answer must not reveal reminder text from a different,
        # possibly private channel.
        here = [r for r in self._own(msg) if r["channel"] == msg.channel]
        msg.reply("; ".join(entry(r) for r in here) or "No reminders here.")

    def _cancel(self, msg, rest):
        # Where the private listing shows every channel, cancelling reaches
        # every channel too; a public answer keeps to this one.
        match = next((r for r in self._own(msg) if str(r["id"]) == rest
                      and (msg.private or r["channel"] == msg.channel)), None)
        if match is None:
            msg.reply("No reminder with that id is yours." if msg.private
                      else "No reminder with that id belongs to you in this channel.")
        else:
            if match.get("held"):
                self.cancel_held(match["held"])
            self.store({**self.state, "reminders": [r for r in self.state["reminders"] if r != match]},
                       msg, f"Cancelled reminder #{match['id']}.")

    def _remind(self, msg, rest):
        now = time.time()
        try:
            due, text = parse_when(rest, now, self.tz)
        except WhenError as exc:
            msg.reply(str(exc))
            return
        if not 1 <= len(text.strip()) <= 160:
            msg.reply(WHEN_USAGE)
        elif len(self._own(msg)) >= 8 or len(self.state["reminders"]) >= 48:
            # Held reminders still count: they are waiting for their owner.
            msg.reply(self.phrase("limit", msg.name))
        else:
            ordinal = self.state["next"]
            reminder = {"id": ordinal, "user": msg.caller["id"], "name": msg.name,
                        "channel": msg.channel, "due": due, "text": text.strip()}
            # The rest of the state (quotes, polls) is kept as it is.
            self.store({**self.state, "next": ordinal + 1, "reminders": [*self.state["reminders"], reminder]},
                       msg, f"Saved reminder #{ordinal} for {describe(due, now, self.tz)}. I'll say it here then, "
                            "or when you next join if you're away.")

    def _tell(self, msg, rest):
        to, _, text = rest.partition(" ")
        to = to.rstrip(":,")
        if not to or not 1 <= len(text.strip()) <= 160:
            msg.reply(TELL_USAGE)
            return

        def told(result):
            # A refusal (no such account, a block, a limit) was already shown
            # to the caller alone by the host; nothing public follows it.
            if not result.ok:
                return
            name = result.value.get("name", to)
            if result.value.get("present"):
                msg.reply(f"{name} is here now.")
            else:
                msg.reply(f"I'll tell {name} here, publicly, when they next join this channel "
                          "(within 14 days). tells lists yours.")
        msg.tell(to, text.strip(), then=told)


    # -- social commands (issue #1379, design doc §16)

    def _social_commands(self, channel):
        # A remote caller's !vote reaches their own node's Baud, which points
        # them at the poll's bot even with polls off here (Decision 3).
        return ({"quote"} if self.quotes_on else set()) | ({"poll", "vote"} if self.polls_on else set()) | (
            {"vote"} if channel in self.linked else set())

    def _privately(self, msg, text):
        # A private answer (#1372) where one can reach the caller; a Link
        # caller whose node cannot take one gets it publicly instead.
        msg.reply(text, private=msg.private or None)

    def _publicly(self, msg, text, note):
        # Said in the channel even when asked privately: a poll's results and
        # a moderator's deletion are for everyone there.
        if msg.get("asked_privately") is True:
            self.say(msg.channel, text)
            msg.reply(note)
        else:
            msg.reply(text)

    def _keep(self, msg, state, then):
        def saved(result):
            if result.ok:
                then()
            else:
                msg.reply(self.phrase("full", msg.name))
        self.save(state, then=saved)

    def _roll(self, msg, rest):
        match = re.fullmatch(r"([0-9]{0,4})d([0-9]{1,5})(?:([+-])([0-9]{1,5}))?", rest.lower() or "1d6")
        count, sides = (int(match[1] or 1), int(match[2])) if match else (0, 0)
        modifier = int(match[4] or 0) * (-1 if match and match[3] == "-" else 1) if match else 0
        if not (1 <= count <= MAX_DICE and 2 <= sides <= MAX_SIDES and abs(modifier) <= MAX_MODIFIER):
            msg.reply(f"Use roll [NdS+M]: N 1-{MAX_DICE} dice of S 2-{MAX_SIDES} sides, M 0-{MAX_MODIFIER}, "
                      "for example roll 2d6+1.")
            return
        dice = [_dice.randint(1, sides) for _ in range(count)]
        spec = f"{count}d{sides}" + (f"{match[3]}{abs(modifier)}" if match[3] else "")
        total = sum(dice) + modifier
        if count == 1 and not match[3]:
            msg.reply(f"rolled {spec}: {total}.")
            return
        sum_text = " + ".join(map(str, dice)) + (f" {'-' if modifier < 0 else '+'} {abs(modifier)}" if match[3] else "")
        msg.reply(f"rolled {spec}: {sum_text} = {total}.")

    def _coin(self, msg, rest):
        msg.reply(f"flips a coin: {_dice.choice(('heads', 'tails'))}.")

    def _quote(self, msg, rest):
        word, _, argument = rest.partition(" ")
        word, argument = word.lower(), argument.strip()
        quotes = self.state.get("quotes", [])
        here = [q for q in quotes if q["c"] == msg.channel]
        if not rest:
            if not here:
                msg.reply("No quotes here yet. Add one with quote add <text>.")
            else:
                msg.reply(_quote_line(_dice.choice(here)))
        elif word == "add":
            if msg.caller.get("guest") is True:
                msg.reply("Guests cannot add quotes.")
            elif not 1 <= len(argument) <= QUOTE_TEXT:
                msg.reply(f"Use quote add <text of 1-{QUOTE_TEXT} characters>.")
            elif (sum(q["u"] == msg.caller["id"] for q in quotes) >= QUOTES_PER_ADDER
                  or len(quotes) >= QUOTES_PER_BOT):
                msg.reply(f"Quote limit reached ({QUOTES_PER_ADDER} per caller, {QUOTES_PER_BOT} per bot). "
                          "Delete an old quote first.")
            else:
                ordinal = self.state.get("qnext", 1)
                quote = {"n": ordinal, "c": msg.channel, "u": msg.caller["id"], "a": msg.name, "t": argument}
                self._keep(msg, {**self.state, "qnext": ordinal + 1, "quotes": [*quotes, quote]},
                           lambda: msg.reply(f"Saved quote #{ordinal} (added by {msg.name})."))
        elif word == "del":
            match = next((q for q in here if str(q["n"]) == argument), None)
            if match is None:
                msg.reply(f"No quote #{argument} here." if _number(argument) else "Use quote del <n>.")
            elif match["u"] != msg.caller["id"] and msg.caller.get("moderator") is not True:
                msg.reply(f"Only the person who added quote #{match['n']} or a channel moderator can delete it.")
            else:
                ordinal = match["n"]
                own = match["u"] == msg.caller["id"]
                self._keep(msg, {**self.state, "quotes": [q for q in quotes if q is not match]},
                           lambda: msg.reply(f"Deleted quote #{ordinal}.") if own else self._publicly(
                               msg, f"Quote #{ordinal} deleted by {msg.name}.", f"Deleted quote #{ordinal}."))
        elif _number(word) and not argument:
            match = next((q for q in here if str(q["n"]) == word), None)
            msg.reply(_quote_line(match) if match else f"No quote #{word} here.")
        else:
            msg.reply(f"Use quote, quote <n>, quote add <text of 1-{QUOTE_TEXT} characters> or quote del <n>.")

    def _poll_here(self, channel):
        return next((p for p in self.state.get("polls", []) if p["c"] == channel), None)

    def _elsewhere(self, channel):
        if channel not in self.linked:
            return ""
        return " A poll opened on another BBS takes its own bot's address, as its opening line shows: Baud.bot@<BBS>: vote <n>."

    def _poll(self, msg, rest):
        here = self._poll_here(msg.channel)
        word, _, argument = rest.partition(" ")
        if not rest:
            if here is None:
                msg.reply("No poll is open here. Open one with poll [<1m-24h>] <question> | <answer> | <answer>."
                          + self._elsewhere(msg.channel))
            else:
                voters = len(here["v"])
                msg.reply(f"Poll #{here['n']}: {here['q']} {voters} voter{'' if voters == 1 else 's'} so far, "
                          f"closes {self.clock(here['due'])}. Results then.")
        elif word.lower() == "close" and not argument:
            self._close_poll(msg, here)
        else:
            self._open_poll(msg, here, rest)

    def _open_poll(self, msg, here, rest):
        duration, _, body = rest.partition(" ")
        match = re.fullmatch(r"([1-9][0-9]{0,4})([mh])", duration.lower())
        if match:
            seconds = int(match[1]) * (60 if match[2] == "m" else 3600)
        else:
            seconds, body = POLL_DEFAULT_SECONDS, rest
        parts = [part.strip() for part in body.split("|")]
        question, answers = parts[0], parts[1:]
        if msg.caller.get("guest") is True:
            msg.reply("Guests can vote but cannot open polls.")
        elif (not 60 <= seconds <= POLL_MAX_SECONDS or not 1 <= len(question) <= POLL_QUESTION
              or not MIN_ANSWERS <= len(answers) <= MAX_ANSWERS
              or not all(1 <= len(a) <= POLL_ANSWER for a in answers)):
            msg.reply(f"Use poll [<1m-24h>] <question> | <answer> | <answer>, with {MIN_ANSWERS}-{MAX_ANSWERS} "
                      f"answers of up to {POLL_ANSWER} characters and a question of up to {POLL_QUESTION}.")
        elif here is not None:
            msg.reply(f"Poll #{here['n']} is already open here; it closes at {self.clock(here['due'])}.")
        elif len(self.state.get("polls", [])) >= POLLS_PER_BOT:
            msg.reply(f"{POLLS_PER_BOT} polls are already open; try again when one closes.")
        else:
            ordinal = self.state.get("pnext", 1)
            poll = {"n": ordinal, "c": msg.channel, "u": msg.caller["id"], "q": question, "o": answers,
                    "due": time.time() + seconds, "v": {}, "x": 0}
            remote = (f", or from another BBS: {self.name}@{self.node}: vote <n>"
                      if msg.channel in self.linked and self.node else "")
            text = (f"Poll #{ordinal} is open for {_span(seconds)}: {question} "
                    + ", ".join(f"{i} {a}" for i, a in enumerate(answers, 1))
                    + f". Vote with !vote <n>{remote}. Votes are public.")
            self._keep(msg, {**self.state, "pnext": ordinal + 1, "polls": [*self.state.get("polls", []), poll]},
                       lambda: msg.reply(text))

    def _close_poll(self, msg, here):
        if here is None:
            msg.reply("No poll is open here.")
        elif here["u"] != msg.caller["id"] and msg.caller.get("moderator") is not True:
            msg.reply(f"Only the person who opened poll #{here['n']} or a channel moderator can close it early.")
        elif here["n"] == self.closing or here["due"] <= time.time():
            msg.reply(f"Poll #{here['n']} is closing now.")
        else:
            # Made due now: the next tick says the results with the same
            # retries as a poll that ran its course, so a refused line (the
            # hourly limit, a mute) never loses them (review of #1425).
            self.poll_failures.pop(here["n"], None)
            poll = {**here, "due": time.time()}
            self._keep(msg, {**self.state, "polls": [poll if p is here else p for p in self.state["polls"]]},
                       lambda: msg.reply(f"Closing poll #{here['n']}; the results follow in the channel."))

    def _vote(self, msg, rest):
        here = self._poll_here(msg.channel) if self.polls_on else None
        if here is None:
            msg.reply("No poll is open here." + self._elsewhere(msg.channel))
            return
        if here["n"] == self.closing or here["due"] <= time.time():
            msg.reply(f"Poll #{here['n']} has closed; the results follow.")
            return
        answers = here["o"]
        if not (_number(rest) and 1 <= int(rest) <= len(answers)):
            msg.reply(f"Vote with vote 1-{len(answers)} on poll #{here['n']}: {here['q']}")
            return
        voter = _voter(msg.caller["id"])
        votes = dict(here["v"])
        if voter not in votes and len(votes) >= POLL_VOTERS:
            poll = {**here, "x": here.get("x", 0) + 1}
            text = f"Poll #{here['n']} already has {POLL_VOTERS} voters; your vote was not counted."
        else:
            votes[voter] = int(rest) - 1
            poll = {**here, "v": votes}
            text = f"Vote counted: {rest} {answers[int(rest) - 1]}. Change it until {self.clock(here['due'])}."
        self._keep(msg, {**self.state, "polls": [poll if p is here else p for p in self.state["polls"]]},
                   lambda: self._privately(msg, text))

    def _close_due_poll(self, now):
        # A due poll's results are said on a tick, with the reminder backoff
        # and retries, and the poll is removed once they are published.
        if self.poll_dropping is not None:
            if not self.saving:
                self._drop_poll(self.poll_dropping)
            return
        if self.closing is not None or self.saving or not isinstance(now, (int, float)):
            return
        for poll in self.state.get("polls", []):
            if poll["due"] <= now and self.poll_failures.get(poll["n"], (0, 0))[1] <= now:
                self.closing = poll["n"]
                self.say(poll["c"], _results(poll), then=lambda result, n=poll["n"]: self._closed(n, result.ok))
                break

    def _closed(self, ordinal, ok):
        attempts = self.poll_failures.pop(ordinal, (0, 0))[0] + (0 if ok else 1)
        if not ok and attempts < MAX_DELIVERY_ATTEMPTS:
            self.poll_failures[ordinal] = (attempts, time.time() + min(3600, 60 * 2 ** (attempts - 1)))
            self.closing = None
            return
        self._drop_poll(ordinal)

    def _drop_poll(self, ordinal):
        # As forget(): removal waits for any save in flight, and `closing`
        # stays set until then, so no tick says these results twice.
        def step():
            self.closing = None
            self.poll_dropping = None
            self.state = {**self.state, "polls": [p for p in self.state.get("polls", []) if p["n"] != ordinal]}
            self.save(self.state)
        if not self.change(step):
            self.poll_dropping = ordinal


def _number(text):
    return re.fullmatch(r"[0-9]{1,6}", text) is not None


def _quote_line(quote):
    return f"#{quote['n']} \"{quote['t']}\" (added by {quote['a']})"


def _voter(caller_id):
    # Caller ids are opaque and a Link one is long; a short digest keeps 100
    # voters within the shared 16 KiB state.
    return hashlib.sha256(str(caller_id).encode("utf-8")).hexdigest()[:16]


def _span(seconds):
    hours, minutes = divmod(round(seconds / 60), 60)
    return " ".join(part for part in (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "") if part)


def _results(poll):
    counts = [0] * len(poll["o"])
    for choice in poll["v"].values():
        if isinstance(choice, int) and 0 <= choice < len(counts):
            counts[choice] += 1
    voters = sum(counts)
    if voters:
        tally = ", ".join(f"{a} {c} ({round(100 * c / voters)}%)" for a, c in zip(poll["o"], counts))
        line = f"Poll #{poll['n']} closed: {poll['q']} {tally}. {voters} voter{'' if voters == 1 else 's'}."
    else:
        line = f"Poll #{poll['n']} closed: {poll['q']} No votes."
    uncounted = poll.get("x", 0)
    if uncounted:
        line += f" {uncounted} vote{'' if uncounted == 1 else 's'} not counted (limit {POLL_VOTERS} voters)."
    return line


def main():
    botkit.run(Baud)


if __name__ == "__main__":
    main()
