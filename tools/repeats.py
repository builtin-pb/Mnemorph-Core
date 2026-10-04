#!/usr/bin/env python3
"""Find the corrections the user has had to repeat, from the record of their words.

`trace` writes the next packet of the user's work messages since a time that no one
has labelled yet; label it, then trace again. Each packet carries the catalog of correction kinds found so far
and, for each message, the earlier messages most like it. A fresh agent labels
each message with `verdict`: `new` (a correction of a kind not yet in the
catalog, with a one-line description), `repeat` (a correction of a catalogued
kind) or `none` (not a correction), naming its own model. `count` reports
repeats per 100 work messages by host and model, and by the model that
labelled them. Labels and the catalog live in the instance's
src/research/repeats.jsonl; they hold message ids and one-line kind
descriptions, never message text. Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = Path("src/research/repeats.jsonl")
PACKETS = Path(".git/mnemorph-repeats/packets")
START = ""  # the whole record
LABELS = ("new", "repeat", "none")
TEXT = 700
CANDIDATE_TEXT = 300
WORD = re.compile(r"[a-z0-9][a-z0-9'-]*|[一-鿿]", re.I)
STOP = set("""the a an and or but if to of in on for with at by from is are was were be been it
this that these those i you we he she they me my your our do does did not no so as can will
would should could just like what how why when where which who there here then than also about
into out up down over more most some any all get got make made let please ok okay yeah yes""".split())

BRIEF = """Label each message below. It is a message the user typed to a coding agent,
with `context`: the end of what the agent had just said.

- `none`: the message does not correct the agent or restate an expectation about its work
  (new requests, answers, questions and thanks are `none`).
- `repeat`: it corrects the agent, and the same expectation is already in the catalog of
  correction kinds, even when worded differently or about different work. Give that kind's id.
- `new`: it corrects the agent with an expectation not yet in the catalog. Give a one-line,
  general description of the expectation, so later messages can be matched to it.

`similar` lists earlier messages that share words with this one, with their labels when
they have them; they help you find the kind but are not a verdict. The same text sent twice
(to two hosts, or restated before the agent acted) is one correction: label the second `none`.
A message with two corrections gets a second label with --also. Judge only from the texts given.

Record each label with the tool, in order, since a `new` label adds a kind that later
messages in the packet may repeat. MODEL is your own model id, as your host states it:
  python3 tools/repeats.py verdict MESSAGE_ID none --labeller MODEL
  python3 tools/repeats.py verdict MESSAGE_ID repeat --kind KIND_ID --labeller MODEL
  python3 tools/repeats.py verdict MESSAGE_ID new --describe "one-line expectation" --labeller MODEL
  python3 tools/repeats.py verdict MESSAGE_ID repeat --of EARLIER_ID [--describe "one-line expectation"] --labeller MODEL
The last form is for a repeat of an earlier correction (for example one in `similar`); give
--describe when that earlier message has no kind yet, and it becomes the kind.
Then return the number of messages labelled and any you could not decide."""


def load_record(root: Path) -> list[dict]:
    entries = []
    for path in sorted((root / "src" / "record").glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            entries += [json.loads(raw) for raw in handle if raw.strip()]
    return sorted(entries, key=lambda e: e["time"])


def is_work(entry: dict) -> bool:
    return entry["kind"] not in ("question_reply", "goal", "command") and entry["stance"] not in ("acceptance", "pasted")


def read_ledger(root: Path) -> list[dict]:
    path = root / LEDGER
    if not path.exists():
        return []
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def catalog(ledger: list[dict]) -> list[dict]:
    return [{"kind": e["kind"], "describe": e["describe"], "first": e.get("message")}
            for e in ledger if e["label"] in ("new", "seed")]


def next_kind(ledger: list[dict]) -> str:
    numbers = [int(e["kind"][1:]) for e in ledger if re.fullmatch(r"k\d+", e.get("kind", ""))]
    return f"k{max(numbers, default=0) + 1:03d}"


def now() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    return w


def words(text: str) -> list[str]:
    out = []
    for m in WORD.finditer(text):
        w = m.group(0).lower()
        if re.match(r"[\u4e00-\u9fff]", w):
            out.append(w)
        elif w not in STOP and len(w) > 1:
            out.append(stem(w))
    return out


def similar(target: dict, earlier: list[dict], df: collections.Counter, n: int, top: int,
            labels: dict) -> list[dict]:
    t = set(words(target["text"]))
    scored = []
    for e in earlier:
        ew = set(words(e["text"]))
        shared = t & ew
        if not shared:
            continue
        # normalise by length so long diaries do not crowd out short corrections
        score = sum(math.log(1 + n / df[w]) for w in shared) / math.sqrt(len(ew))
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, e in scored[:top]:
        item = {"id": e["id"], "time": e["time"][:16], "text": clip(e["text"], CANDIDATE_TEXT)}
        known = labels.get(e["id"])
        if known:
            item["label"] = ", ".join(f"{k['label']} {k.get('kind', '')}".strip() for k in known)
        out.append(item)
    return out


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " […]"


def trace(args) -> dict:
    record = load_record(args.root)
    judged = {e["message"] for e in read_ledger(args.root) if e.get("message") and e["label"] != "seed"}
    work = [e for e in record if is_work(e)]
    df = collections.Counter(w for e in work for w in set(words(e["text"])))
    pending = [e for e in work if e["time"] >= args.since and e["id"] not in judged]
    out = args.root / PACKETS
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    ledger = read_ledger(args.root)
    cat = catalog(ledger)
    labels = collections.defaultdict(list)
    for e in ledger:
        if e.get("message"):
            labels[e["message"]].append(e)
    # One packet at a time: its labels extend the catalog the next packet carries.
    batch = pending[:args.batch]
    items = []
    for e in batch:
        earlier = [x for x in work if x["time"] < e["time"]]
        context = e.get("context") or {}
        items.append({"id": e["id"], "time": e["time"][:16], "host": e["host"],
                      "text": clip(e["text"], TEXT),
                      "context": clip(str(context.get("excerpt") or context.get("text") or ""), 400),
                      "similar": similar(e, earlier, df, len(work), args.top, labels)})
    path = out / "next.json"
    if items:
        path.write_text(json.dumps({"brief": BRIEF, "catalog": cat, "messages": items},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return {"since": args.since, "pending": len(pending), "in_packet": len(items),
            "packet": str(path) if items else None, "catalog_kinds": len(cat)}


def verdict(args) -> dict:
    record = {e["id"]: e for e in load_record(args.root)}
    if args.message not in record:
        raise ValueError(f"unknown message id {args.message}")
    ledger = read_ledger(args.root)
    mine = [e for e in ledger if e.get("message") == args.message and e["label"] != "seed"]
    if mine and not args.also:
        raise ValueError(f"{args.message} is already labelled; use --also for a second correction in it")
    entry = {"message": args.message, "time": record[args.message]["time"], "labelled": now(),
             "labeller": args.labeller, "label": args.label}
    kinds = {k["kind"] for k in catalog(ledger)}
    appended = []
    if args.label == "repeat" and args.kind:
        if args.kind not in kinds:
            raise ValueError(f"unknown kind {args.kind}")
        entry["kind"] = args.kind
    elif args.label == "repeat":
        if not args.of or args.of not in record or record[args.of]["time"] >= entry["time"]:
            raise ValueError("repeat needs --kind, or --of an earlier message id (with --describe if uncatalogued)")
        prior = [e for e in ledger if e.get("message") == args.of and e.get("kind")]
        if prior:
            entry["kind"] = prior[0]["kind"]
        elif args.describe:
            first = {"message": args.of, "time": record[args.of]["time"], "labelled": now(),
                     "labeller": args.labeller, "label": "new",
                     "kind": next_kind(ledger), "describe": " ".join(args.describe.split()),
                     "note": "catalogued when first repeated"}
            appended.append(first)
            entry["kind"] = first["kind"]
        else:
            raise ValueError(f"{args.of} has no kind yet; add --describe")
    elif args.label == "new":
        if not args.describe:
            raise ValueError("new needs --describe")
        entry["kind"] = next_kind(ledger)
        entry["describe"] = " ".join(args.describe.split())
    if args.note:
        entry["note"] = " ".join(args.note.split())
    (args.root / LEDGER).parent.mkdir(parents=True, exist_ok=True)
    with (args.root / LEDGER).open("a", encoding="utf-8") as handle:
        for e in appended + [entry]:
            handle.write(json.dumps(e, ensure_ascii=False) + "\n")
    return entry


def _record_tool(root: Path):
    spec = importlib.util.spec_from_file_location("record_tool", root / "tools" / "record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def count(args) -> dict:
    record = [e for e in load_record(args.root) if e["time"] >= args.since]
    ledger = read_ledger(args.root)
    labels = collections.defaultdict(list)
    for e in ledger:
        if e.get("message") and e["label"] != "seed":
            labels[e["message"]].append(e)
    kinds = {k["kind"]: k["describe"] for k in catalog(ledger)}
    rec = _record_tool(args.root)
    cache, by_model, repeats, new_kinds = {}, collections.defaultdict(collections.Counter), [], []
    by_labeller = collections.defaultdict(collections.Counter)
    for e in record:
        if not is_work(e):
            continue
        model = rec.model_at(e["source"], e["line"], cache, codex=e["host"] == "codex")
        c = by_model[f"{e['host']}/{model}"]
        c["work_messages"] += 1
        found = labels.get(e["id"])
        if not found:
            c["unlabelled"] += 1
            continue
        c["labelled"] += 1
        names = {x["label"] for x in found}
        top = "repeat" if "repeat" in names else "new" if "new" in names else "none"
        c[top] += 1
        # a labeller switch can move the rate on its own, so keep each labeller's share visible
        who = by_labeller[found[0].get("labeller", "unrecorded")]
        who["labelled"] += 1
        who["repeat"] += top == "repeat"
        fresh = lambda x: bool(args.new_since and x.get("labelled", "") >= args.new_since)
        for x in found:
            if x["label"] == "repeat":
                repeats.append({"time": e["time"][:16], "id": e["id"], "kind": kinds.get(x["kind"], x["kind"]),
                                "new": fresh(x), "text": clip(e["text"], 200)})
            elif x["label"] == "new" and fresh(x):
                new_kinds.append({"time": e["time"][:16], "id": e["id"], "kind": x["kind"],
                                  "describe": x.get("describe", "")})
    total = collections.Counter()
    for c in by_model.values():
        total.update(c)
    labelled = total["labelled"]
    return {"since": args.since, "work_messages": total["work_messages"], "labelled": labelled,
            "unlabelled": total["unlabelled"], "corrections": total["new"] + total["repeat"],
            "repeats": total["repeat"],
            "repeats_per_100_labelled": round(100 * total["repeat"] / labelled, 1) if labelled else None,
            "by_host_model": {k: dict(v) for k, v in sorted(by_model.items())},
            "by_labeller": {k: dict(v) for k, v in sorted(by_labeller.items())},
            "repeats_listed": repeats, "new_kinds": new_kinds}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=ROOT)
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("trace", help="write packets of unlabelled work messages")
    t.add_argument("--since", default=START)
    t.add_argument("--batch", type=int, default=25)
    t.add_argument("--top", type=int, default=5)
    v = sub.add_parser("verdict", help="label one message")
    v.add_argument("message")
    v.add_argument("label", choices=LABELS)
    v.add_argument("--kind")
    v.add_argument("--describe")
    v.add_argument("--of", help="for a repeat of an earlier correction: its message id")
    v.add_argument("--also", action="store_true", help="add a second correction label to a labelled message")
    v.add_argument("--note")
    v.add_argument("--labeller", required=True, help="the labelling agent's own model id")
    c = sub.add_parser("count", help="repeats per 100 work messages, by host and model")
    c.add_argument("--since", default=START)
    c.add_argument("--new-since")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        out = {"trace": trace, "verdict": verdict, "count": count}[args.command](args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
