#!/usr/bin/env python3
"""Append the messages the user typed to Codex and Claude Code to src/record/.

`append` reads the hosts' native transcripts, keeps only human-typed messages,
redacts credentials and appends entries that are not yet recorded. `check`
validates the record. Neither command prints message text. `model_at` names the
model that answered a message; tools/repeats.py uses it to count repeated
corrections by model.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
CONTEXT_HEAD, CONTEXT_TAIL = 300, 700
REPLAY_WINDOW_S = 3600

# --- Credential redaction -------------------------------------------------

_REDACTIONS = [
    ("private_key", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----.*?"
        r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S)),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("api_key", re.compile(r"(?<![A-Za-z0-9])sk-(?:[A-Za-z0-9]+-){0,3}[A-Za-z0-9_\-]{16,}")),
    ("api_key", re.compile(r"(?<![A-Za-z0-9])(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("api_key", re.compile(r"(?<![A-Za-z0-9])cr\\?_[A-Za-z0-9]{24,}")),
    ("api_key", re.compile(
        r"(?<![A-Za-z0-9])(?:hf_[A-Za-z0-9]{30,}|gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}"
        r"|pplx-[A-Za-z0-9]{40,}|r8_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36,}|glpat-[A-Za-z0-9_\-]{20,})")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("slack_token", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer_token", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/\-]{20,}=*")),
]
_KEYWORD = (r"(?:api[\s_-]?key|apikey|access[\s_-]?(?:key|token)|auth[\s_-]?token|secret(?:[\s_-]?key)?"
            r"|client[\s_-]?secret|token|password|passwd|passcode|pwd|credential|密钥|密码|口令|令牌)")
# "API key is X", "token: X", "password=X", "密码是X": redact X when it looks like a credential.
_CONTEXTUAL = re.compile(
    rf"(?i)({_KEYWORD}s?[\"']?(?:\s+(?:is|was|are|here|=|:)|\s*[:=：]|\s*是)[\s:=：\"'`]*)"
    r"((?:[A-Za-z0-9_\-+/=.~!@#$%^&*]|\\[_\-*]){4,})")
_URL_PARAM = re.compile(r"(?i)([?&](?:api_?key|key|token|access_token|auth|secret|password|sig|signature)=)([^&\s#]{8,})")
_GENERIC = re.compile(r"(?<![A-Za-z0-9_\-\\/.%=+:@])((?:[A-Za-z0-9_\-]|\\[_\-]){32,})(?![A-Za-z0-9_\-\\])")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _entropy(text: str) -> float:
    counts = collections.Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


_PASSWORD_WORD = re.compile(r"(?i)pass|pwd|密码|口令")


def _credential_like(token: str, *, contextual: bool, password: bool = False) -> bool:
    plain = token.replace("\\", "")
    if contextual:
        if plain.startswith(("/", "~", "http")) or "://" in plain:
            return False
        has_digit = any(c.isdigit() for c in plain)
        has_alpha = any(c.isalpha() for c in plain)
        if password and (has_digit or not plain.isalpha()):
            return len(plain) >= 4  # a stated password or passcode, even numeric
        return (has_digit and has_alpha and len(plain) >= 8) or len(plain) >= 24
    if _UUID.search(plain) or _DATE.search(plain):
        return False
    return (any(c.isdigit() for c in plain) and any(c.isupper() for c in plain)
            and any(c.islower() for c in plain) and _entropy(plain) >= 3.5)


def redact(text: str, found: collections.Counter | None = None) -> str:
    """Replace credentials with typed placeholders such as [REDACTED:api_key]."""
    if not text:
        return text
    found = found if found is not None else collections.Counter()

    def sub(kind, match_group=0):
        def replace(match):
            found[kind] += 1
            if match_group == 0:
                return f"[REDACTED:{kind}]"
            return match.group(1) + f"[REDACTED:{kind}]"
        return replace

    for kind, pattern in _REDACTIONS:
        text = pattern.sub(sub(kind), text)

    def contextual(match):
        token = match.group(2)
        trail = ""
        while token and token[-1] in ".!":  # sentence punctuation after the value
            trail = token[-1] + trail
            token = token[:-1]
        password = bool(_PASSWORD_WORD.search(match.group(1)))
        if not _credential_like(token, contextual=True, password=password):
            return match.group(0)
        found["credential"] += 1
        return match.group(1) + "[REDACTED:credential]" + trail

    text = _CONTEXTUAL.sub(contextual, text)
    text = _URL_PARAM.sub(sub("credential", 1), text)

    def generic(match):
        if not _credential_like(match.group(1), contextual=False):
            return match.group(0)
        found["secret"] += 1
        return "[REDACTED:secret]"

    return _GENERIC.sub(generic, text)


# --- Stance: the user's own words versus accepting the agent's -------------

_QUOTED = re.compile(r"\"[^\"\n]{3,}\"|“[^”]{3,}”|「[^」]{3,}」|^\s*>.*$", re.M)
_CLAUSE = re.compile(r"[^\n.!?。！？;；,，]+[.!?。！？;；,，]*")
_LIST_MARK = re.compile(r"^\s*(?:[-*•]|\(?\d{1,2}[.)]|[a-z][.)])\s+", re.I)
_ACCEPT = {
    "ok", "okay", "k", "kk", "yes", "yeah", "yep", "yup", "sure", "go", "go ahead", "go on", "ok go", "sure go",
    "do it", "do that", "do these", "do both", "do all", "do all of them", "please do", "yes please", "sure do these",
    "sounds good", "sounds great", "sounds better", "that sounds better", "good", "great", "nice", "fine", "perfect",
    "cool", "agreed", "agree", "i agree", "approved", "lgtm", "alright", "all right", "continue", "please continue",
    "proceed", "keep going", "then keep going", "keep working", "go for it", "that works", "works for me", "makes sense",
    "up to you", "your call", "you decide", "either is ok", "either is fine", "either works", "whatever you think",
    "as you recommend", "as you recommended", "as you suggest", "as you suggested", "as recommended", "as suggested",
    "i trust you", "yes it is", "correct", "right", "exactly", "that's right", "thats right", "ok then", "ok cool",
    "好", "好的", "行", "可以", "同意", "没问题", "继续", "按你说的", "听你的", "允许", "都行", "随你",
}
_DEFERRAL = re.compile(r"(?i)(?:as you (?:recommend|suggest)|your recommend|your suggest|as recommended|as suggested"
                       r"|up to you|your call|either (?:is|one is) (?:ok|fine)|whatever you think|按你说的|听你的|随你)")
_OPTION = re.compile(r"(?i)^(?:option\s+)?[a-e]$")
_REJECT = {"no", "nope", "nah", "no thanks", "not now", "don't", "dont", "stop", "cancel", "不", "不要", "不用", "算了", "别"}
_HEDGE = re.compile(r"(?i)^(?:i'?m (?:kind of |kinda )?wondering (?:if|whether)|i wonder (?:if|whether)"
                    r"|should we|maybe we should|what if|do you think|would it be)")
_REQUEST_ASK = re.compile(r"(?i)^(?:can|could|would|will) you\b|^please\b|^帮我|^请")
_CJK = re.compile(r"[㐀-鿿]")


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def stance(text: str, agent_text: str = "") -> str:
    """Mechanical, conservative reading of whose position a message states.

    acceptance: only accepts, defers to or picks from the agent's proposal
    (including repeating one of the agent's options); question: only questions;
    own: substantive statements or requests with no acceptance; mixed:
    acceptance plus own statements; unknown: too little to judge.
    """
    agent = _normal(agent_text)
    whole = _normal(_QUOTED.sub(" ", text or ""))
    if agent and len(whole.split()) >= 2 and f" {whole} " in f" {agent} ":
        return "acceptance"
    body = _QUOTED.sub(" ", text or "")
    kinds, words = [], 0
    for raw in _CLAUSE.findall(body):
        clause = _LIST_MARK.sub("", raw.strip())
        core = clause.rstrip(".!?。！？;；,， ").strip().lower().replace("’", "'")
        if not core or re.fullmatch(r"[\d\W_]+", core):
            continue
        plain = _normal(core)
        if agent and len(plain.split()) >= 4 and f" {plain} " in f" {agent} ":
            continue  # unquoted repetition of the agent's words
        question = (raw.rstrip().endswith(("?", "？")) or bool(_HEDGE.match(core))) and not _REQUEST_ASK.match(core)
        if core in _ACCEPT or _OPTION.match(core) or _DEFERRAL.search(core):
            kinds.append("A")
        elif core in _REJECT:
            kinds.append("R")
        elif question:
            kinds.append("Q")
        else:
            kinds.append("S")
            cjk = len(_CJK.findall(core))
            words += len(re.findall(r"[A-Za-z0-9']+", core)) + cjk // 2
    if not kinds:
        return "unknown"
    has = set(kinds)
    if "A" in has:
        return "mixed" if has & {"S", "R"} else "acceptance"
    if "R" in has:
        return "own"
    if "S" not in has:
        return "question"
    return "own" if words >= 3 else "unknown"


# --- Shared helpers --------------------------------------------------------

def display_path(path: Path) -> str:
    try:
        return "~/" + str(path.resolve().relative_to(HOME.resolve()))
    except ValueError:
        return str(path)


def excerpt(text: str) -> tuple[str, bool]:
    if len(text) <= CONTEXT_HEAD + CONTEXT_TAIL:
        return text, False
    return text[:CONTEXT_HEAD] + "\n[…]\n" + text[-CONTEXT_TAIL:], True


def make_context(kind: str, text: str, *, session=None, message=None, source=None, line=None,
                 redactions: collections.Counter) -> dict | None:
    if not text or not text.strip():
        return None
    body, cut = excerpt(text)
    context = {"by": "agent", "type": kind}
    if session:
        context["session"] = session
    if message:
        context["message"] = message
    if source:
        context["source"] = source
    if line:
        context["line"] = line
    context["chars"] = len(text)
    context["truncated"] = cut
    context["excerpt"] = redact(body, redactions)
    return context


def parse_time(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def temp_cwd(cwd) -> bool:
    return isinstance(cwd, str) and (cwd + "/").startswith(TEMP_PREFIXES)


def text_key(text: str, attachments) -> str:
    normal = re.sub(r"\s+", " ", text or "").strip()
    names = sorted(json.dumps(a, sort_keys=True) for a in attachments or [])
    return hashlib.sha256((normal + "\x00" + "\x00".join(names)).encode()).hexdigest()


class Run:
    def __init__(self):
        self.entries: list[dict] = []
        self.excluded: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.stripped: collections.Counter = collections.Counter()
        self.redactions: collections.Counter = collections.Counter()
        self.files: collections.Counter = collections.Counter()
        self.unreadable: collections.Counter = collections.Counter()

    def exclude(self, host: str, reason: str, count: int = 1):
        self.excluded[host][reason] += count

    def add(self, *, host, session, message, time, source, line, kind, text, attachments=None, context=None,
            agent_text=""):
        found = collections.Counter()
        clean = redact(text, found)
        attachments = [{k: (redact(v, found) if isinstance(v, str) else v) for k, v in a.items() if v is not None}
                       for a in attachments or []]
        judged = "unknown" if kind == "command" else stance(clean, agent_text)
        entry = {"id": f"{host}:{message}", "time": time, "host": host, "session": session, "message": message,
                 "source": source, "line": line, "kind": kind, "stance": judged, "text": clean}
        if attachments:
            entry["attachments"] = attachments
        if found:
            entry["redacted"] = sorted(found)
        if context:
            entry["context"] = context
        self.redactions.update(found)
        self.entries.append(entry)
        return entry


# --- Claude Code -----------------------------------------------------------

_CLAUDE_STRIP = re.compile(
    r"<(system-reminder|ide_opened_file|ide_selection|ide_diagnostics|local-command-caveat)\b[^>]*>.*?</\1>", re.S)
_APP_RESUME = "I hit my usage limit while you were working, but it has reset now. Please continue from where you left off."
_APP_MARKERS = ("[Request interrupted by user", "[Request cancelled by user")
_CLAUDE_EXCLUDE_PREFIX = (
    ("<scheduled-task", "scheduled_task"), ("<task-notification", "task_notification"),
    ("<agent-message", "agent_message"), ("<local-command-stdout", "command_output"),
    ("<local-command-stderr", "command_output"), ("<bash-stdout", "command_output"),
    ("<bash-stderr", "command_output"), ("<heartbeat", "heartbeat"),
)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_BASH_INPUT = re.compile(r"^\s*<bash-input>(.*?)</bash-input>\s*$", re.S)
_IMAGE_SOURCE = re.compile(r"^\[Image: source: (.+)\]$")
_REJECTION = re.compile(r"^The user doesn't want to proceed with this tool use\..*?the user said:\s*\n?(.*)$", re.S)


def _claude_blocks(content):
    """Return (text blocks, attachments) of a user message content."""
    if isinstance(content, str):
        return [content], []
    texts, attachments = [], []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(block.get("text") or "")
        elif block.get("type") in ("image", "document"):
            source = block.get("source") or {}
            item = {"type": block["type"]}
            if source.get("media_type"):
                item["media_type"] = source["media_type"]
            attachments.append(item)
    return texts, attachments


def _assistant_text(record) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n\n".join(b.get("text") or "" for b in content or [] if isinstance(b, dict) and b.get("type") == "text").strip()


def claude_session(path: Path, run: Run):
    host = "claude"
    source = display_path(path)
    records = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, 1):
            try:
                records.append((number, json.loads(raw)))
            except json.JSONDecodeError:
                run.unreadable[host] += 1
    # Judge the session by how it started: agents may later change directory.
    entry_point = next((r["entrypoint"] for _, r in records if isinstance(r.get("entrypoint"), str)), None)
    start_cwd = next((r["cwd"] for _, r in records if isinstance(r.get("cwd"), str)), None)
    if entry_point and entry_point.startswith("sdk"):
        run.exclude(host, "sdk_or_print_session")
        return
    if temp_cwd(start_cwd):
        run.exclude(host, "temporary_directory_session")
        return
    run.files[host] += 1
    last = None          # preceding assistant message: dict(id, line, uuid, text)
    last_error = False   # preceding assistant record was an API error
    tool_uses = {}       # tool_use id -> (line, uuid, name, input)
    by_prompt = {}       # promptId -> entry, for image companions
    for number, record in records:
        kind = record.get("type")
        if record.get("isSidechain"):
            if kind in ("user", "attachment"):
                run.exclude(host, "sidechain")
            continue
        session = record.get("sessionId") or path.stem
        if kind == "assistant":
            message = record.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_uses[block.get("id")] = (number, record.get("uuid"), block.get("name"), block.get("input"))
            if record.get("isApiErrorMessage"):
                last_error = True
                continue
            text = _assistant_text(record)
            if text:
                if last and last["id"] == message.get("id"):
                    last["text"] += "\n\n" + text
                else:
                    last = {"id": message.get("id"), "line": number, "uuid": record.get("uuid"),
                            "session": session, "text": text}
                last_error = False
            continue
        time = parse_time(record.get("timestamp"))

        def context_from_last():
            if not last:
                return None
            return make_context("preceding_message", last["text"], session=last["session"],
                                message=last["uuid"], source=source, line=last["line"],
                                redactions=run.redactions)

        if kind == "attachment":
            attachment = record.get("attachment") or {}
            if attachment.get("type") != "queued_command":
                continue
            origin = (attachment.get("origin") or {}).get("kind")
            if origin != "human":
                run.exclude(host, {"peer": "peer_message", None: "task_notification"}.get(origin, f"origin_{origin}"))
                continue
            texts, attachments = _claude_blocks(attachment.get("prompt"))
            text = "\n\n".join(t for t in (_CLAUDE_STRIP.sub("", t).strip("\n") for t in texts) if t.strip())
            if not time or not (text or attachments):
                run.exclude(host, "empty_or_untimed")
                continue
            run.add(host=host, session=session, message=record.get("uuid"), time=time, source=source, line=number,
                    kind="queued", text=text, attachments=attachments, context=context_from_last(),
                    agent_text=last["text"] if last else "")
            continue
        if kind != "user":
            continue
        content = (record.get("message") or {}).get("content")
        if isinstance(content, list) and content and all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            handled = False
            for block in content:
                use = tool_uses.get(block.get("tool_use_id"))
                result = record.get("toolUseResult")
                if not time:
                    break
                if use and use[2] == "AskUserQuestion" and isinstance(result, dict) and isinstance(result.get("answers"), dict):
                    answers = result["answers"]
                    questions = [str(q) for q in answers]
                    text = "\n\n".join(str(a) for a in answers.values())
                    context = make_context("question", "\n\n".join(questions), session=session, message=use[1],
                                           source=source, line=use[0], redactions=run.redactions)
                    run.add(host=host, session=session, message=record.get("uuid"), time=time, source=source,
                            line=number, kind="question_reply", text=text, context=context,
                            agent_text="\n\n".join(questions))
                    handled = True
                    break
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text") or "" for b in body if isinstance(b, dict))
                match = _REJECTION.match(body or "") if isinstance(body, str) else None
                if match and match.group(1).strip():
                    run.add(host=host, session=session, message=record.get("uuid"), time=time, source=source,
                            line=number, kind="rejection_feedback", text=match.group(1).strip("\n"),
                            context=context_from_last(), agent_text=last["text"] if last else "")
                    handled = True
                    break
            if not handled:
                run.exclude(host, "tool_result")
            continue
        if record.get("isMeta"):
            texts, _ = _claude_blocks(content)
            image = _IMAGE_SOURCE.match("".join(texts).strip())
            if image and record.get("promptId") in by_prompt:
                entry = by_prompt[record["promptId"]]
                pending = [a for a in entry.get("attachments", []) if a["type"] == "image" and "path" not in a]
                if pending:
                    pending[0]["path"] = redact(image.group(1), run.redactions)
                    continue
            origin = (record.get("origin") or {}).get("kind")
            run.exclude(host, "peer_message" if origin == "peer" else "meta")
            continue
        if record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly"):
            run.exclude(host, "compact_summary")
            continue
        origin = (record.get("origin") or {}).get("kind")
        if origin is not None and origin != "human":
            run.exclude(host, {"task-notification": "task_notification", "peer": "peer_message"}.get(
                origin, f"origin_{origin}"))
            continue
        texts, attachments = _claude_blocks(content)
        kept = []
        for text in texts:
            cleaned = _CLAUDE_STRIP.sub("", text)
            if cleaned != text:
                run.stripped["claude_injected_context"] += 1
            if cleaned.strip():
                kept.append(cleaned.strip("\n") if cleaned != text else cleaned)
        text = "\n\n".join(kept)
        head = text.lstrip()
        reason = next((why for prefix, why in _CLAUDE_EXCLUDE_PREFIX if head.startswith(prefix)), None)
        if reason:
            run.exclude(host, reason)
            continue
        if head.startswith(_APP_MARKERS) or text.strip() == _APP_RESUME or (text.strip() == "Try again" and last_error):
            run.exclude(host, "app_generated")
            continue
        entry_kind = "message"
        command = _COMMAND.search(text)
        shell = _BASH_INPUT.match(text)
        if command and head.startswith(("<command-", "<command-message")):
            args = _COMMAND_ARGS.search(text)
            text = (command.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
            entry_kind = "command"
        elif shell:
            text, entry_kind = shell.group(1), "shell"
        if not text.strip() and not attachments:
            run.exclude(host, "injected_context")
            continue
        if not time:
            run.exclude(host, "empty_or_untimed")
            continue
        entry = run.add(host=host, session=session, message=record.get("uuid"), time=time, source=source,
                        line=number, kind=entry_kind, text=text, attachments=attachments, context=context_from_last(),
                        agent_text=last["text"] if last else "")
        if record.get("promptId"):
            by_prompt[record["promptId"]] = entry


def claude_sources(projects: Path, run: Run):
    if not projects.is_dir():
        return
    for path in sorted(projects.glob("*/*.jsonl")):
        claude_session(path, run)
    run.exclude("claude", "subagent_transcript_files", sum(1 for _ in projects.glob("*/*/subagents/**/*.jsonl")))


# --- Codex ----------------------------------------------------------------

_BROWSER = re.compile(r"<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>\s*", re.S)
_QUESTION_REPLY = re.compile(r"<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>", re.S)
_REQUEST = re.compile(r"^## My request(?: for Codex)?:[ \t]*\n?", re.M)
_ANNOTATIONS = re.compile(r"<response-annotations>\s*(.*?)\s*</response-annotations>", re.S)
_FILE_LINE = re.compile(r"^## (.+?): (/.+)$", re.M)
_DIFF_COMMENT = re.compile(r"^## User Comment \d+\n(.*?)^Comment:[ \t]*\n(.*?)(?=^## |\Z)", re.S | re.M)
_CAPTION = re.compile(r"^The next image shows .* at the time of Comment \d+\.", re.S)
_CODEX_WRAPPERS = ("<environment_context", "<subagent_notification", "<skill", "<recommended_plugins",
                   "<codex_internal_context", "<user_instructions", "<turn_aborted", "<agent-message",
                   "<user_shell_command", "# AGENTS.md instructions")
_FILENAME = re.compile(r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-")


def codex_user_message(item, run: Run) -> tuple[str | None, str, list, tuple | None, str]:
    """Return (exclusion reason or None, kind, attachments, (context type, agent text) or None, user text)."""
    texts, attachments = [], []
    for part in item.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(part.get("text") or "")
        elif part.get("type") == "local_image":
            attachments.append({"type": "image", "path": part.get("path")})
        elif part.get("type") == "image":
            attachments.append({"type": "image"})
        elif part.get("type") == "skill":
            attachments.append({"type": "skill", "name": part.get("name"), "path": part.get("path")})
    first, rest = (texts[0], texts[1:]) if texts else ("", [])
    extra = []
    for part in rest:
        if _CAPTION.match(part.strip()):
            run.stripped["codex_image_caption"] += 1
        elif part.strip():
            extra.append(part)
    if _BROWSER.search(first):
        first = _BROWSER.sub("", first)
        run.stripped["codex_in_app_browser_context"] += 1
    head = first.lstrip()
    if head.startswith("<heartbeat"):
        return "heartbeat", "", [], None, ""
    if head.startswith(_CODEX_WRAPPERS):
        return "injected_context", "", [], None, ""
    reply = _QUESTION_REPLY.search(first)
    if reply:
        try:
            items = json.loads(reply.group(1))
        except json.JSONDecodeError:
            items = None
        if isinstance(items, list):
            answers, questions = [], []
            for element in items:
                if not isinstance(element, dict):
                    continue
                answer = element.get("answer")
                answers.append(answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False))
                if element.get("question"):
                    questions.append(str(element["question"]))
            residue = _QUESTION_REPLY.sub("", first).strip()
            text = "\n\n".join([a for a in answers if a] + ([residue] if residue else []) + extra)
            return None, "question_reply", attachments, ("question", "\n\n".join(questions)), text
    request = list(_REQUEST.finditer(first))
    if not request:
        return None, "message", attachments, None, ("\n\n".join([first] + extra) if extra else first)
    header, user = first[:request[-1].start()], first[request[-1].end():]
    kind, selections, comments = "message", [], []
    if "# Files mentioned by the user:" in header:
        kind = "attachment"
        for name, file_path in _FILE_LINE.findall(header):
            attachments.append({"type": "file", "name": name.strip(), "path": file_path.strip()})
    if "## Referenced ChatGPT conversation:" in header:
        kind = "attachment"
        reference = re.search(r"\{.*\}", header.split("## Referenced ChatGPT conversation:", 1)[1], re.S)
        try:
            data = json.loads(reference.group(0)) if reference else {}
        except json.JSONDecodeError:
            data = {}
        attachments.append({"type": "chatgpt_conversation", "id": data.get("conversationId"),
                            "title": data.get("title")})
    annotations = _ANNOTATIONS.search(header)
    if annotations:
        kind = "annotation"
        try:
            for element in json.loads(annotations.group(1)):
                if isinstance(element, dict):
                    if element.get("text"):
                        selections.append(str(element["text"]))
                    if element.get("comment"):
                        comments.append(str(element["comment"]))
        except json.JSONDecodeError:
            selections.append(annotations.group(1))
    if "# Diff comments:" in header:
        kind = "diff_comment"
        for meta, comment in _DIFF_COMMENT.findall(header):
            selections.append(meta.strip())
            if comment.strip():
                comments.append(comment.strip("\n"))
            name = re.search(r"^File: (.+)$", meta, re.M)
            if name:
                attachments.append({"type": "file", "name": name.group(1).strip()})
    text = "\n\n".join([c for c in comments] + ([user.strip("\n")] if user.strip() else []) + extra)
    context = ("selection", "\n---\n".join(selections)) if selections else None
    return None, kind, attachments, context, text


def _codex_state(state_db: Path | None) -> set[str]:
    """Thread ids that Codex marks archived; empty when the database is unavailable."""
    if not state_db or not state_db.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        try:
            return {row[0] for row in connection.execute("select id from threads where archived != 0")}
        finally:
            connection.close()
    except sqlite3.Error:
        return set()


def _codex_session_reason(meta: dict, archived: set[str]) -> str | None:
    source = meta.get("source")
    if meta.get("id") in archived:
        return "archived_thread"
    if isinstance(source, dict):
        sub = source.get("subagent")
        if isinstance(sub, dict) and sub.get("other") == "guardian" or meta.get("thread_source") == "guardian_review":
            return "guardian_review_threads"
        return "subagent_threads"
    if source == "exec":
        return "exec_threads"
    if meta.get("thread_source") == "automation":
        return "automation_threads"
    if meta.get("thread_source") not in (None, "user") or source not in ("vscode", "cli"):
        return "other_source_threads"
    if temp_cwd(meta.get("cwd")):
        return "temporary_directory_threads"
    return None


def codex_sources(sessions: Path, state_db: Path | None, run: Run):
    host = "codex"
    if not sessions.is_dir():
        return
    archived = _codex_state(state_db)
    threads: dict[str, dict] = {}
    for path in sorted(sessions.rglob("rollout-*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                meta = json.loads(handle.readline())
        except (json.JSONDecodeError, OSError):
            run.unreadable[host] += 1
            continue
        if meta.get("type") != "session_meta":
            run.unreadable[host] += 1
            continue
        payload = meta.get("payload") or {}
        stamp = _FILENAME.search(path.name)
        thread = threads.setdefault(payload.get("id"), {"meta": payload, "segments": []})
        thread["segments"].append((stamp.group(1) if stamp else "", path.name, path))
    agent_messages: dict[str, list] = {}
    family_keys: dict[str, list] = {}
    for thread_id, thread in sorted(threads.items(), key=lambda item: (item[1]["meta"].get("timestamp") or "", item[0])):
        meta = thread["meta"]
        reason = _codex_session_reason(meta, archived)
        if reason:
            run.exclude(host, reason)
            continue
        run.files[host] += len(thread["segments"])
        agents = agent_messages.setdefault(thread_id, [])
        keys = family_keys.setdefault(thread_id, [])
        ancestors, parent = [], meta.get("forked_from_id")
        while parent and parent not in ancestors:
            ancestors.append(parent)
            parent = threads.get(parent, {}).get("meta", {}).get("forked_from_id")
        fork_point = meta.get("forked_from_ordinal_exclusive")
        goals_seen = set()
        segments = sorted(thread["segments"])
        for index, (_, _, path) in enumerate(segments):
            source = display_path(path)
            first_user_in_segment = True
            last_call = ""
            with path.open(encoding="utf-8", errors="replace") as handle:
                handle.readline()
                for number, raw in enumerate(handle, 2):
                    if not any(marker in raw for marker in ("UserMessage", "AgentMessage", "thread_goal_updated",
                                                            "function_call", "custom_tool_call")):
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        run.unreadable[host] += 1
                        continue
                    payload = record.get("payload") or {}
                    if not isinstance(payload, dict):
                        continue
                    if record.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                        last_call = str(payload.get("name") or "")
                        continue
                    time = parse_time(record.get("timestamp"))
                    if record.get("type") == "event_msg" and payload.get("type") == "thread_goal_updated":
                        goal = payload.get("goal") or {}
                        objective = goal.get("objective")
                        if not isinstance(objective, str) or not objective.strip():
                            continue
                        key = (goal.get("createdAt"), objective)
                        if key in goals_seen:
                            continue
                        goals_seen.add(key)
                        if "goal" in last_call.lower():
                            run.exclude(host, "agent_set_goal")
                            continue
                        if not time:
                            run.exclude(host, "empty_or_untimed")
                            continue
                        digest = hashlib.sha256(objective.encode()).hexdigest()[:12]
                        run.add(host=host, session=thread_id, message=f"goal:{goal.get('createdAt')}:{digest}",
                                time=time, source=source, line=number, kind="goal", text=objective)
                        continue
                    if record.get("type") != "event_msg" or payload.get("type") != "item_completed":
                        continue
                    item = payload.get("item") or {}
                    if item.get("type") == "AgentMessage":
                        text = "\n\n".join(p.get("text") or "" for p in item.get("content") or []
                                           if isinstance(p, dict)).strip()
                        if text:
                            agents.append((record.get("ordinal") or 0, item.get("id"), source, number, text))
                        last_call = ""
                        continue
                    if item.get("type") != "UserMessage":
                        continue
                    last_call = ""
                    reason, kind, attachments, context_piece, text = codex_user_message(item, run)
                    if reason:
                        run.exclude(host, reason)
                        continue
                    if not time or not (text.strip() or attachments):
                        run.exclude(host, "empty_or_untimed")
                        continue
                    key = text_key(text, attachments)
                    now = epoch(time)
                    replay = first_user_in_segment and (index > 0 or ancestors) and any(
                        k == key and 0 <= now - t <= REPLAY_WINDOW_S
                        for owner in [thread_id, *ancestors] for t, k in family_keys.get(owner, []))
                    first_user_in_segment = False
                    keys.append((now, key))
                    if replay:
                        run.exclude(host, "fork_or_resume_replay")
                        continue
                    agent_text = ""
                    if context_piece:
                        agent_text = context_piece[1] if context_piece[0] == "question" else ""
                        context = make_context(context_piece[0], context_piece[1], session=thread_id,
                                               message=item.get("id"), source=source, line=number,
                                               redactions=run.redactions)
                    else:
                        previous = agents[-1] if agents else None
                        if previous is None and ancestors:
                            limit = fork_point if isinstance(fork_point, int) else float("inf")
                            candidates = [a for a in agent_messages.get(ancestors[0], []) if a[0] < limit]
                            previous = candidates[-1] if candidates else None
                            owner = ancestors[0]
                        else:
                            owner = thread_id
                        agent_text = previous[4] if previous else ""
                        context = make_context("preceding_message", previous[4], session=owner, message=previous[1],
                                               source=previous[2], line=previous[3],
                                               redactions=run.redactions) if previous else None
                    run.add(host=host, session=thread_id, message=item.get("id"), time=time, source=source,
                            line=number, kind=kind, text=text, attachments=attachments, context=context,
                            agent_text=agent_text)


# --- Record files ----------------------------------------------------------

def load_record(directory: Path) -> tuple[set, set, int]:
    ids, copies, count = set(), set(), 0
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path.name}:{number}: invalid JSON ({error.msg})") from None
                ids.add(entry.get("id"))
                copies.add((entry.get("host"), entry.get("time"), text_key(entry.get("text"), entry.get("attachments"))))
                count += 1
    return ids, copies, count


def append(args) -> dict:
    directory = args.root / "src" / "record"
    ids, copies, existing = load_record(directory)
    run = Run()
    if not args.no_claude:
        claude_sources(args.claude_projects, run)
    if not args.no_codex:
        codex_sources(args.codex_sessions, None if args.codex_state == Path("none") else args.codex_state, run)
    run.entries.sort(key=lambda e: (e["time"], e["host"], e["source"], e["line"]))
    added, by_month = [], collections.defaultdict(list)
    skipped = collections.Counter()
    for entry in run.entries:
        copy = (entry["host"], entry["time"], text_key(entry["text"], entry.get("attachments")))
        if entry["id"] in ids:
            skipped["already_recorded"] += 1
            continue
        if copy in copies:
            skipped["copy_of_recorded_message"] += 1
            continue
        ids.add(entry["id"])
        copies.add(copy)
        added.append(entry)
        by_month[entry["time"][:7]].append(entry)
    if not args.dry_run and added:
        directory.mkdir(parents=True, exist_ok=True)
        for month, entries in sorted(by_month.items()):
            target = directory / f"{month}.jsonl"
            with target.open("a", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    count = lambda field: dict(sorted(collections.Counter(e[field] for e in added).items()))
    return {
        "dry_run": args.dry_run,
        "previously_recorded": existing,
        "candidates": len(run.entries),
        "added": len(added),
        "added_by_host": count("host"),
        "added_by_kind": count("kind"),
        "added_by_stance": count("stance"),
        "added_by_month": {m: len(v) for m, v in sorted(by_month.items())},
        "skipped": dict(skipped),
        "excluded": {host: dict(sorted(c.items())) for host, c in sorted(run.excluded.items())},
        "stripped": dict(sorted(run.stripped.items())),
        "redactions": dict(sorted(run.redactions.items())),
        "session_files_read": dict(run.files),
        "unreadable_lines": dict(run.unreadable),
    }


REQUIRED = ("id", "time", "host", "session", "message", "source", "line", "kind", "stance", "text")


def check(args) -> dict:
    directory = args.root / "src" / "record"
    problems, seen, count, residual = [], set(), 0, collections.Counter()
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                where = f"{path.name}:{number}"
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    problems.append(f"{where}: invalid JSON")
                    continue
                count += 1
                missing = [field for field in REQUIRED if field not in entry]
                if missing:
                    problems.append(f"{where}: missing {','.join(missing)}")
                if entry.get("id") in seen:
                    problems.append(f"{where}: duplicate id")
                seen.add(entry.get("id"))
                if isinstance(entry.get("time"), str) and entry["time"][:7] != path.stem:
                    problems.append(f"{where}: time outside file month")
                strings = [entry.get("text") or ""]
                strings += [v for a in entry.get("attachments") or [] for v in a.values() if isinstance(v, str)]
                strings.append(((entry.get("context") or {}).get("excerpt")) or "")
                for value in strings:
                    found = collections.Counter()
                    redact(value, found)
                    residual.update(found)
    if residual:
        problems.append(f"credential-like text remains: {dict(residual)}")
    return {"entries": count, "files": len(list(directory.glob('*.jsonl'))), "problems": problems[:50],
            "problem_count": len(problems)}


# --- Answering model, used by tools/repeats.py ------------------------------


def _source_path(source: str) -> Path:
    return HOME / source[2:] if source.startswith("~/") else Path(source)


def _models(path: Path) -> list[tuple[int, str]]:
    """(line, model) for each Claude assistant reply or Codex turn context."""
    found = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, raw in enumerate(handle, 1):
                if '"model"' not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if row.get("type") == "assistant":
                    model = (row.get("message") or {}).get("model")
                elif row.get("type") == "turn_context":
                    model = (row.get("payload") or {}).get("model")
                else:
                    continue
                if isinstance(model, str) and model and not model.startswith("<"):
                    found.append((number, model))
    except OSError:
        pass
    return found


def model_at(source: str, line: int, cache: dict, *, codex: bool) -> str:
    """The model that answered this message. Codex writes a turn's context before its user message;
    Claude Code names the model on each reply after it."""
    if source not in cache:
        cache[source] = _models(_source_path(source))
    models = cache[source]
    after = [model for number, model in models if number > line][:1]
    before = [model for number, model in models if number < line][-1:]
    found = before + after if codex else after + before
    return found[0] if found else "unknown"


def parser() -> argparse.ArgumentParser:
    # Each host's own setting for its directory, else its default under the home directory.
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
    codex_dir = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=ROOT, help="Mnemorph repository root")
    commands = result.add_subparsers(dest="command", required=True)
    add = commands.add_parser("append", help="append new human-typed messages; prints counts only")
    add.add_argument("--claude-projects", type=Path, default=claude_dir / "projects",
                     help="Claude Code transcripts (default: projects/ in $CLAUDE_CONFIG_DIR, else ~/.claude)")
    add.add_argument("--codex-sessions", type=Path, default=codex_dir / "sessions",
                     help="Codex transcripts (default: sessions/ in $CODEX_HOME, else ~/.codex)")
    add.add_argument("--codex-state", type=Path, default=codex_dir / "state_5.sqlite",
                     help="Codex state database for archive flags; 'none' to skip")
    add.add_argument("--no-claude", action="store_true")
    add.add_argument("--no-codex", action="store_true")
    add.add_argument("--dry-run", action="store_true", help="report what would be added without writing")
    commands.add_parser("check", help="validate record files and scan them for unredacted credentials")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    args.root = args.root.resolve()
    try:
        output = {"append": append, "check": check}[args.command](args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if args.command == "check" and output["problem_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
