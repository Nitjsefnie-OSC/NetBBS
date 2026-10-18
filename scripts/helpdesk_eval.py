"""Run the help desk's fixed evaluation prompts against a local model.

Usage, from the repository root, with a model server running:

    python scripts/helpdesk_eval.py --model llama3.2:3b
    python scripts/helpdesk_eval.py --endpoint http://127.0.0.1:8080/v1 --model local

A release step for help-desk mode (§16, issue #1382): small models invent menu
paths, so before a release each prompt below is asked with the same system
message the node builds (default persona, rules, handbook excerpts and a
sample of node facts), and every [key] in an answer that the excerpts do not
contain is flagged. Read the answers as well; the flag catches only invented
keys. Uses the standard library only and never runs in the test suite.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from netbbs.bots import helpdesk  # noqa: E402
from netbbs.bots.model import DEFAULT_PERSONAS, HELPDESK  # noqa: E402

PROMPTS = (
    "how do I upload a file?",
    "I forgot my password, what now?",
    "how do I change my username?",
    "where can I play games?",
    "how do I send mail to someone on another BBS?",
    "what is MRC?",
    "how do I block someone?",
    "how do I reply to a post?",
    "how do I make a poll?",
    "how do I log off?",
    "how do I talk to the sysop?",
    "how do I download with SyncTERM?",
    "is anyone online?",
    "what is the weather like?",
)

SAMPLE_FACTS = (
    "Node name: Example BBS.\nRun by SysOp sysop. Write to them with E-mail To: sysop.\n"
    "This node has door games. It has no MRC inter-BBS chat, browser links for file transfers, FTN networks and "
    "netmail; handbook text about them does not apply here.\nRight now: Example BBS: 2 human accounts online; "
    "uptime 5h 12m.\nMessage boards: General (Anything goes); Retro (Old hardware).\nFile areas: Uploads."
)


def ask(endpoint, model, system, prompt, api_key):
    body = json.dumps({"model": model, "stream": False, "max_tokens": 256, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", body, headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()
    flagged = 0
    for prompt in PROMPTS:
        excerpts = helpdesk.retrieve(prompt)
        system = (DEFAULT_PERSONAS[HELPDESK] + "\nYour chat identity is Baud.bot. Use this name when identifying "
                  "yourself.\n\n" + helpdesk.RULES + "\n\nNode facts:\n" + SAMPLE_FACTS + "\n\n" +
                  ("Handbook excerpts:\n" + "\n\n".join(helpdesk.excerpt_text(s) for s in excerpts) if excerpts
                   else "Handbook excerpts: none matched this question."))
        answer = " ".join(ask(args.endpoint, args.model, system, prompt, args.api_key).split())
        shown, _ = helpdesk.with_source(answer, excerpts[0]["title"] if excerpts else None, 400)
        grounded = system + " [?]"
        invented = sorted({k for k in re.findall(r"\[[^\]\s]{1,8}\]", answer) if k not in grounded})
        flagged += bool(invented)
        print(f"Q: {prompt}\nA: {shown}" + (f"\n!! keys not in the excerpts: {', '.join(invented)}" if invented else "")
              + "\n")
    print(f"{flagged} of {len(PROMPTS)} answers name keys the excerpts do not contain.")


if __name__ == "__main__":
    main()
