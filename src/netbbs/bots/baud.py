"""Bundled rule-based bot. Standalone stdlib program using only BOT API v1.

Run by filename so it also works from a source checkout without an installed
package. It has no database imports, filesystem access, or special host grants.
It is written on botkit (issue #1374), the same helper any other bot may use;
botkit is a sibling module, imported from this directory when Baud runs by
filename.
"""

import datetime as dt
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
PHRASE_KEYS = ("greeting", "help", "busy", "limit")
PRESETS = {
    "greeting": {"friendly": "Welcome, {name}!", "terse": "Welcome, {name}.",
                 "retro": "Carrier detected: welcome, {name}!"},
    "help": {"friendly": "Happy to help!", "terse": "", "retro": "Carrier detected."},
    "busy": dict.fromkeys(("friendly", "terse", "retro"), "Busy saving reminders; try again shortly."),
    "limit": dict.fromkeys(("friendly", "terse", "retro"),
                           "Reminder limit reached (8 per caller, 48 per bot). Cancel an old reminder first."),
}


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
        self.state = state
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

    def on_door(self, event):
        if self.announce_doors:
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
                      "this channel if you are away (within 7 days).")
        elif command in {"", "help"} and topic == "tell":
            msg.reply("tell <name> <text>: a note of up to 160 characters for a local account. I say it "
                      "publicly here when they next join this channel (within 14 days). tells lists yours; "
                      "cancel tell <id> withdraws one. Nobody who blocks you or takes no direct messages "
                      "gets your tells.")
        elif command in {"", "help"}:
            opening = self.phrase("help", msg.name).strip()
            msg.reply((opening + " " if opening else "") + "help; remind <when> <text>; reminders; cancel <id>; "
                      "tell <name> <text>; tells; cancel tell <id>. "
                      + ("stats; " if "stats" in self.queries else "")
                      + ("seen <name>; " if "seen" in self.queries else "")
                      + ("top <door>; " if "top" in self.queries else "") +
                      "help remind|tell: more. /sysop <reason>: page staff. " +
                      ("Conversation is enabled; reset clears your temporary context here. Replies are public." if self.model_conversation
                       else "No language model is enabled."))
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
        elif command == "top":
            if command not in self.queries:
                msg.reply("The SysOp has not enabled this query ability.")
            else:
                msg.query("top", rest.strip(), then=lambda result: self._top(msg, result))
        elif command in {"reminders", "cancel", "remind"}:
            # These read the reminders: each waits for any save in flight.
            handler = getattr(self, "_" + command)
            if not self.change(lambda: handler(msg, rest.strip())):
                msg.reply(self.phrase("busy", msg.name))
        elif command == "sysop":
            # Paging is the host's /sysop (decision 1377): a ! line has
            # already gone out publicly, so this only points the way.
            msg.reply("I can't page anyone, and this line was public. "
                      "Type /sysop <reason>: only the SysOp and staff see it.")
        elif command == "reset" and self.model_conversation:
            msg.model_reset()
        elif self.model_conversation:
            msg.model()
        else:
            known = ["help", "remind", "reminders", "cancel", "tell", "tells", *sorted(self.queries & {"seen", "stats", "top"})]
            msg.reply(f"I know {', '.join(known[:-1])} and {known[-1]}. Conversation is not enabled.")

    def _top(self, msg, result):
        # Issue #1408: a door's standings, as the host filtered them. A
        # titled block where the host takes blocks (#1373), else one line.
        value = result.value if result.ok and isinstance(result.value, dict) else None
        if value is None:
            msg.reply(result.get("error", "Query unavailable."))
        elif value.get("rows") and getattr(self, "blocks", False):
            msg.reply_block(f"{value['door']} · {value['title']}", [
                (f"{row['rank']} {row['name']}", row["value"] + (f" · {row['label']}" if row.get("label") else ""))
                for row in value["rows"]])
        else:
            msg.reply(value["text"])

    def _own(self, msg):
        return [r for r in self.state["reminders"] if r["user"] == msg.caller["id"]]

    def _reminders(self, msg, rest):
        # Do not reveal reminder text from a different, possibly private channel.
        here = [r for r in self._own(msg) if r["channel"] == msg.channel]
        msg.reply("; ".join(f"#{r['id']}: {r['text'][:35]}"
                            + (f" (waiting since {self.clock(r['due'])})" if r.get("held") else "")
                            for r in here) or "No reminders here.")

    def _cancel(self, msg, rest):
        match = next((r for r in self._own(msg) if str(r["id"]) == rest and r["channel"] == msg.channel), None)
        if match is None:
            msg.reply("No reminder with that id belongs to you in this channel.")
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
            self.store({"next": ordinal + 1, "reminders": [*self.state["reminders"], reminder]},
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


def main():
    botkit.run(Baud)


if __name__ == "__main__":
    main()
