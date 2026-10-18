"""Bundled rule-based bot. Standalone stdlib program using only BOT API v1.

Run by filename so it also works from a source checkout without an installed
package. It has no database imports, filesystem access, or special host grants.
It is written on botkit (issue #1374), the same helper any other bot may use;
botkit is a sibling module, imported from this directory when Baud runs by
filename.
"""

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

    def on_tick(self, event):
        if self.forgetting is not None and not self.saving:
            self.forget(self.forgetting)
            return
        if self.delivering is not None or self.saving:
            return
        for reminder in self.state["reminders"]:
            if reminder["due"] <= event.time and self.failures.get(reminder["id"], (0, 0))[1] <= event.time:
                self.delivering = reminder["id"]
                self.say(reminder["channel"], f"{reminder['name']}: reminder #{reminder['id']}: {reminder['text']}",
                         then=lambda result, ident=reminder["id"]: self.delivered(ident, result.ok))
                break

    def on_message(self, msg):
        command, _, rest = msg.text.strip().lstrip("!").partition(" ")
        command = command.lower()
        if command in {"", "help"}:
            opening = self.phrase("help", msg.name).strip()
            msg.reply((opening + " " if opening else "") + "help; remind <1s-30d> <text>; reminders; cancel <id>. "
                      + ("stats; " if "stats" in self.queries else "")
                      + ("seen <local-account>; " if "seen" in self.queries else "")
                      + ("top <door>; " if "top" in self.queries else "") +
                      "Reminders appear publicly here, including after restart. "
                      "/sysop <reason>: page staff. " +
                      ("Conversation is enabled; reset clears your temporary context here. Replies are public." if self.model_conversation
                       else "No language model is enabled."))
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
            known = ["help", "remind", "reminders", "cancel", *sorted(self.queries & {"seen", "stats", "top"})]
            msg.reply(f"I know {', '.join(known[:-1])} and {known[-1]}. Conversation is not enabled.")

    def _top(self, msg, result):
        # Issue #1408: a door's standings, as the host filtered them.
        msg.reply(result.value["text"] if result.ok else result.get("error", "Query unavailable."))

    def _own(self, msg):
        return [r for r in self.state["reminders"] if r["user"] == msg.caller["id"]]

    def _reminders(self, msg, rest):
        # Do not reveal reminder text from a different, possibly private channel.
        here = [r for r in self._own(msg) if r["channel"] == msg.channel]
        msg.reply("; ".join(f"#{r['id']}: {r['text'][:35]}" for r in here) or "No reminders here.")

    def _cancel(self, msg, rest):
        match = next((r for r in self._own(msg) if str(r["id"]) == rest and r["channel"] == msg.channel), None)
        if match is None:
            msg.reply("No reminder with that id belongs to you in this channel.")
        else:
            self.store({**self.state, "reminders": [r for r in self.state["reminders"] if r != match]},
                       msg, f"Cancelled reminder #{match['id']}.")

    def _remind(self, msg, rest):
        duration, _, text = rest.partition(" ")
        match = re.fullmatch(r"([1-9][0-9]{0,6})([smhd])", duration.lower())
        seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]] if match else 0
        if not 1 <= seconds <= 30 * 86400 or not 1 <= len(text.strip()) <= 160:
            msg.reply("Use remind <1s-30d> <text of 1-160 characters>.")
        elif len(self._own(msg)) >= 8 or len(self.state["reminders"]) >= 48:
            msg.reply(self.phrase("limit", msg.name))
        else:
            ordinal = self.state["next"]
            reminder = {"id": ordinal, "user": msg.caller["id"], "name": msg.name,
                        "channel": msg.channel, "due": time.time() + seconds, "text": text.strip()}
            self.store({"next": ordinal + 1, "reminders": [*self.state["reminders"], reminder]},
                       msg, f"Saved reminder #{ordinal}; it will appear publicly here.")


def main():
    botkit.run(Baud)


if __name__ == "__main__":
    main()
