"""Tally: karma counted from whole-channel reading. An example bot on botkit.

It shows what Baud does not (issue #1374): it reads every line of the
channels where the SysOp granted **Read whole channels**, and counts
``name++`` and ``name--`` in them. Such an observation carries no request
token, so there is nobody to reply to: Tally says its line to the channel.
Addressed, ``!karma [name]`` answers the caller as any request does.

Shipped installed and off. To run it, create a bot in Chat bots and enter
the node's interpreter under Program arguments, for example
``["/usr/pkg/bin/python3.11", "-u", "-m", "netbbs.bots.tally"]``, or run
this file by name from a source checkout.
"""

import re
import sys

try:
    from . import botkit
except ImportError:  # run by filename: sys.path[0] is this directory
    import botkit

# The state stays far below the host's 16 KiB: at most this many names, the
# one nearest zero forgotten first.
MAX_NAMES = 200
MAX_PER_LINE = 5
KARMA = re.compile(r"(?<![\w@.+-])([A-Za-z0-9_][A-Za-z0-9_.-]{1,23})(\+\+|--)(?![\w+-])")


class Tally(botkit.Bot):
    def __init__(self, hello, emit=None):
        super().__init__(hello, emit)
        state = self.state if isinstance(self.state, dict) else {}
        karma = state.get("karma")
        # Each entry: lower-case name -> [name as last written, score].
        self.state = {"karma": karma if isinstance(karma, dict) else {}}

    def on_channel(self, event):
        if event.kind not in {"message", "action"}:
            return
        speaker = (event.caller or {}).get("name", "").split("@", 1)[0].lower()
        changes = []
        for name, sign in KARMA.findall(event.text or ""):
            name = name.rstrip(".")
            if len(name) >= 2 and name.lower() != speaker:
                changes.append((name, 1 if sign == "++" else -1))
        if changes:
            self.change(lambda: self.count(event.channel, changes[:MAX_PER_LINE]))

    def count(self, channel, changes):
        karma = dict(self.state["karma"])
        for name, delta in changes:
            karma[name.lower()] = [name, karma.get(name.lower(), [name, 0])[1] + delta]
        while len(karma) > MAX_NAMES:
            del karma[min(karma, key=lambda key: abs(karma[key][1]))]
        named = list(dict.fromkeys(name.lower() for name, _ in changes))
        text = "; ".join(f"{karma[key][0]} now has {karma[key][1]} karma" for key in named if key in karma) + "."

        def saved(result):
            if result.ok:
                self.say(channel, text)
            else:
                # Seen in the console's Try; a live bot's stderr is discarded.
                print(f"Tally could not save karma: {result.error}", file=sys.stderr, flush=True)
        self.save({"karma": karma}, then=saved)

    def on_message(self, msg):
        command, _, rest = msg.text.strip().lstrip("!").partition(" ")
        command = command.lower()
        if command == "karma":
            if not self.change(lambda: msg.reply(self.report(rest.strip() or msg.name))):
                msg.reply("Busy counting; try again shortly.")
        elif command in {"", "help"}:
            if self.reading_channels:
                msg.reply("I count name++ and name-- in " + ", ".join("#" + c for c in self.reading_channels)
                          + ". karma [name] shows a score.")
            else:
                msg.reply("I count karma only where the SysOp lets me read every line, and nowhere is granted yet.")
        else:
            msg.reply("I know help and karma [name].")

    def report(self, name):
        karma = self.state["karma"]
        top = sorted(karma.values(), key=lambda entry: (-entry[1], entry[0].lower()))[:3]
        shown, score = karma.get(name.lower(), [name, 0])
        board = ", ".join(f"{entry[0]} {entry[1]}" for entry in top)
        return f"{shown} has {score} karma" + (f" (top: {board})." if board else ".")


def main():
    botkit.run(Tally)


if __name__ == "__main__":
    main()
