#!/usr/bin/env python3
"""Discover subject memory and capture explicitly selected body references.

No prose predicate parser: selections record the agent's judgment. A repository
check reports syntactic reference reach, not authority or semantic completeness.
Only the standard library is needed.
"""
from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import html
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
HEADER_KEYS = {"id", "summary", "primary", "role"}
SKIP = {".git", ".eval", ".test-tmp", ".mnemorph-local", "__pycache__", ".venv"}
DIRECTIVES = {
    "root": re.compile(r"^Mnemorph root: (\S+)$"),
    "narrow": re.compile(r"^Mnemorph narrow route: requested module$"),
    "support": re.compile(r"^Mnemorph support: (\S+)$"),
}


@dataclass(frozen=True)
class Reference:
    text: str
    target: Path
    line: int


@dataclass(frozen=True)
class Module:
    path: Path
    module_id: str
    summary: str
    primary: bool
    body: str
    text: str
    references: tuple[Reference, ...] = ()
    role: str = "guidance"


@dataclass(frozen=True)
class Seed:
    target: str
    evidence: str
    kind: str = "prompt"


@dataclass(frozen=True)
class Selection:
    owner: str
    target: str
    evidence: str
    kind: str = "prompt"


@dataclass
class Graph:
    root: Path
    scope: str = "active"
    modules: dict[Path, Module] = field(default_factory=dict)
    by_id: dict[str, Module] = field(default_factory=dict)
    contents: dict[Path, bytes] = field(default_factory=dict)
    catalog: dict[str, list[Module]] | None = None
    composition_root: Path | None = None
    bootstrap_support: Path | None = None
    revision: str = "unavailable"
    metadata_reads: int = 0
    body_reads: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Assembly:
    modules: tuple[Module, ...] = ()
    support: tuple[Path, ...] = ()
    seeds: tuple[tuple[str, str, str], ...] = ()
    selections: tuple[tuple[str, str, str, str], ...] = ()
    errors: tuple[str, ...] = ()
    digest: str = ""
    support_bytes: int = 0


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def inside(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def _git_revision(root: Path) -> str:
    try:
        rows = subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}",
             "rev-parse", "--show-toplevel", "HEAD"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        if len(rows) == 2 and Path(rows[0]).resolve() == root:
            return rows[1]
    except (OSError, subprocess.CalledProcessError):
        pass
    return "unavailable"


def _content(graph: Graph, path: Path) -> bytes | None:
    """First read wins within this graph, including a file consumed in both roles."""
    if not inside(path, graph.root):
        graph.errors.append(f"{path}: target escapes repository")
        return None
    if path not in graph.contents:
        try:
            graph.contents[path] = path.read_bytes()
            graph.body_reads += 1
        except (OSError, ValueError) as error:
            graph.errors.append(f"{relative(path, graph.root)}: cannot read target: {error}")
            return None
    return graph.contents[path]


def _text(graph: Graph, path: Path) -> str | None:
    data = _content(graph, path)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeError as error:
        graph.errors.append(f"{relative(path, graph.root)}: not UTF-8: {error}")
        return None


@dataclass
class _MarkdownContainer:
    kind: str
    indent: int
    empty: bool = False


def _escaped(text: str, position: int) -> bool:
    start = position
    while start and text[start - 1] == "\\":
        start -= 1
    return (position - start) % 2 == 1


def _source_lines(text: str, *, keepends: bool = False) -> list[str]:
    """Split LF, CRLF and CR lines; other Unicode separators remain content."""
    rows = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    if rows and not rows[-1]:
        rows.pop()
    return rows if keepends else [row.rstrip("\r\n") for row in rows]


_HTML_TAG = (
    r"</[A-Za-z][A-Za-z0-9-]*[ \t\n]*>"
    r"|<[A-Za-z][A-Za-z0-9-]*"
    r"(?:[ \t\n]+[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:[ \t\n]*=[ \t\n]*(?:[^\x00-\x20\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*[ \t\n]*/?>"
)
_INLINE_HTML = re.compile(
    r"<!-->|<!--->|<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![A-Za-z][^>]*>|"
    + _HTML_TAG, re.S,
)
_AUTOLINK = re.compile(
    r"<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^<>\x00-\x20]*>"
    r"|<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*>"
)


def _prose_blocks(text: str, *, definitions: dict[str, str] | None = None,
                  inline_ranges: list[tuple[int, int]] | None = None) -> str:
    """Mask non-prose blocks; leave inline precedence to the reference reader."""
    def mask(value: str) -> str:
        return "".join(char if char in "\r\n" else " " for char in value)

    rows = _source_lines(text, keepends=True)
    fence: tuple[str, int] | None = None
    html: str | None = None
    containers: list[_MarkdownContainer] = []
    paragraph = False
    result: list[str] = []
    inline: list[str] = []
    inline_paragraph = False
    offset = 0

    def append_result(value: str) -> None:
        nonlocal offset
        result.append(value)
        offset += len(value)

    def finish_inline() -> None:
        if inline:
            value = "".join(inline)
            end = 0
            if definitions is not None and inline_paragraph:
                while definition := _reference_definition(value, end):
                    label, destination, end = definition
                    definitions.setdefault(label, destination)
            if inline_ranges is not None:
                inline_ranges.append((offset + end, offset + len(value)))
            append_result(mask(value[:end]) + value[end:])
            inline.clear()

    def remove_columns(row: str, width: int) -> str:
        # Strip only container indentation; tabs inside a destination stay bytes.
        column = index = 0
        while column < width and index < len(row):
            column += 4 - column % 4 if row[index] == "\t" else 1
            index += 1
        return " " * (column - width) + row[index:]

    block_tags = (
        "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|"
        "details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|"
        "frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|"
        "optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|"
        "thead|title|tr|track|ul"
    )
    quote = re.compile(r"^ {0,3}> ?")
    marker = re.compile(r"^ {0,3}([-+*]|[0-9]{1,9}[.)])(?:( +)|$)")
    fence_marker = re.compile(r"^ {0,3}(\x60{3,}|~{3,})(.*)$")
    heading = re.compile(r"^ {0,3}#{1,6}(?: |$)")
    thematic = re.compile(r"^ {0,3}(?:(?:\* *){3,}|(?:- *){3,}|(?:_ *){3,})$")
    block_start = re.compile(r"^ {0,3}</?(" + block_tags + r")(?=[ \t>]|/>|$)", re.I)
    raw_start = re.compile(r"^ {0,3}<(?:script|pre|style|textarea)(?=[ \t>]|$)", re.I)
    special_start = re.compile(r"^ {0,3}(<!--|<\?|<!\[CDATA\[|<![A-Za-z])")
    complete_tag = re.compile(r"^ {0,3}(?:" + _HTML_TAG + r")[ \t]*$")
    closing_html = {
        "raw": re.compile(r"</(?:script|pre|style|textarea)>", re.I),
        "<!--": re.compile(r"-->"), "<?": re.compile(r"\?>"),
        "<![CDATA[": re.compile(r"\]\]>"), "declaration": re.compile(r">"),
    }

    def html_start(value: str) -> str | None:
        if raw_start.match(value):
            return "raw"
        if special := special_start.match(value):
            return special[1] if special[1] in closing_html else "declaration"
        return "tag" if block_start.match(value) else None

    def opening_fence(value: str) -> re.Match[str] | None:
        match = fence_marker.match(value)
        return match if match and (match[1][0] != chr(96) or chr(96) not in match[2]) else None

    def list_marker(value: str, *, interrupt: bool) -> _MarkdownContainer | None:
        match = marker.match(value)
        if not match or thematic.match(value):
            return None
        if interrupt and (not value[match.end():].strip() or
                          (match[1][0].isdigit() and int(match[1][:-1]) != 1)):
            return None
        padding = len(match[2] or "")
        empty = not value[match.end():].strip()
        # Five spaces start indented code after the marker's single space.
        width = match.start(1) + len(match[1]) + (padding if not empty and 1 <= padding <= 4 else 1)
        return _MarkdownContainer(match[1][-1], width, empty)

    for row in rows:
        content = row.expandtabs(4).rstrip("\r\n")
        matched = 0
        for container in containers:
            if container.kind == ">":
                prefix = quote.match(content)
                if not prefix:
                    break
                content = content[prefix.end():]
            elif not content.strip():
                if container.empty:
                    break
                content = ""
            elif content.startswith(" " * container.indent):
                content = content[container.indent:]
                container.empty = False
            else:
                break
            matched += 1
        if matched < len(containers):
            new_item = list_marker(content, interrupt=False)
            interrupts = (quote.match(content) or opening_fence(content) or heading.match(content)
                          or thematic.match(content) or html_start(content)
                          or new_item)
            # A new block belongs to the surviving parent, not the unmatched paragraph.
            # Only paragraphs may continue without their container prefixes.
            if fence or html or not paragraph or not content.strip() or interrupts:
                finish_inline()
                del containers[matched:]
                fence = html = None
                paragraph = False
        if not fence and not html:
            while True:
                prefix = quote.match(content)
                item = list_marker(content, interrupt=paragraph)
                if prefix:
                    finish_inline()
                    containers.append(_MarkdownContainer(">", 0))
                    content = content[prefix.end():]
                elif item:
                    finish_inline()
                    containers.append(item)
                    content = content[item.indent:]
                else:
                    break
                paragraph = False
        blank = not content.strip()
        match = fence_marker.match(content)
        block = html_start(content)
        standalone_tag = (not paragraph and complete_tag.fullmatch(content)
                          and not re.match(r"^ {0,3}<(?:script|pre|style|textarea)(?=[ \t/>]|$)",
                                           content, re.I))
        leaf_end = heading.match(content) or thematic.match(content)
        setext = paragraph and re.fullmatch(r" {0,3}(?:=+|-+) *", content)
        prose = not (fence or html or opening_fence(content) or block or standalone_tag or blank
                     or (content.startswith("    ") and not paragraph))
        if not prose or leaf_end or setext:
            finish_inline()
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            append_result(mask(row))
        elif html:
            if blank and html not in closing_html:
                html = None
            append_result(mask(row))
            if html in closing_html and closing_html[html].search(content):
                html = None
        elif opening_fence(content):
            fence = (match[1][0], len(match[1]))
            append_result(mask(row))
        elif block or standalone_tag:
            html = block or "tag"
            append_result(mask(row))
            if html in closing_html and closing_html[html].search(content):
                html = None
        elif blank:
            append_result(row)
        elif content.startswith("    ") and not paragraph:
            append_result(mask(row))
        else:
            if not inline:
                inline_paragraph = not (leaf_end or setext)
            if definitions is not None:
                width = len(row.expandtabs(4).rstrip("\r\n")) - len(content)
                inline.append(remove_columns(row, width))
            else:
                inline.append(row)
            paragraph = not (leaf_end or setext)
            if not paragraph:
                finish_inline()
            continue
        paragraph = False
    finish_inline()
    return "".join(result)


def _label(text: str, start: int) -> tuple[str, int] | None:
    """Read a reference label, bounded before scanning an invalid long suffix."""
    index = start + 1
    while index < len(text):
        if index - start - 1 > 999:
            return None
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "[":
            return None
        elif char == "]":
            return text[start + 1:index], index + 1
        index += 1
    return None


def _destination_parentheses(text: str, start: int, end: int) -> dict[int, int]:
    """Match bare-destination parentheses once, without crossing controls."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    index = start
    while index < end:
        char = text[index]
        if (char == "\\" and index + 1 < end
                and text[index + 1] in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"):
            index += 2
            continue
        if ord(char) <= 32 or ord(char) == 127:
            stack.clear()
        elif char == "(":
            stack.append(index)
        elif char == ")" and stack:
            pairs[stack.pop()] = index
        index += 1
    return pairs


def _destination(text: str, start: int,
                 parentheses: Callable[[], dict[int, int]] | None = None) -> tuple[str, int] | None:
    index = start
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    if index >= len(text):
        return None
    if text[index] == "<":
        begin = index + 1
        index = begin
        while index < len(text):
            char = text[index]
            if char in "\r\n<":
                return None
            if char == ">":
                return text[begin:index], index + 1
            if char == "\\" and index + 1 < len(text) and text[index + 1] in "<>\\":
                index += 2
            else:
                index += 1
        return None
    begin = index
    depth = 0
    while index < len(text):
        char = text[index]
        if (char == "\\" and index + 1 < len(text)
                and text[index + 1] in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"):
            index += 2
            continue
        if ord(char) <= 32 or ord(char) == 127:
            break
        if char == "(":
            if parentheses is not None:
                closing = parentheses().get(index)
                if closing is None:
                    return None
                index = closing + 1
                continue
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        index += 1
    if index == begin or depth:
        return None
    return text[begin:index], index


def _link_space(text: str, start: int) -> int | None:
    """Link whitespace may cross one line ending, never a blank line."""
    index = start
    while index < len(text) and text[index] in " \t\n":
        index += 1
    return index if text.count("\n", start, index) <= 1 else None


def _link_title(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] not in "\"'(":
        return None
    opener = text[start]
    closer = ")" if opener == "(" else opener
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "\\\"'()":
            index += 2
            continue
        if char == closer:
            return index + 1
        if opener == "(" and char == "(":
            return None
        if char == "\n" and _link_space(text, index) is None:
            return None
        index += 1
    return None


def _inline_link(text: str, start: int,
                 parentheses: Callable[[], dict[int, int]] | None = None) -> tuple[str, int] | None:
    begin = _link_space(text, start)
    if begin is None or begin == len(text):
        return None
    if text[begin] == ")":
        return "", begin + 1
    value = _destination(text, begin, parentheses)
    if value is None:
        return None
    destination, end = value
    tail = _link_space(text, end)
    if tail is None:
        return None
    if tail > end and tail < len(text) and text[tail] in "\"'(":
        title_end = _link_title(text, tail)
        if title_end is None:
            return None
        tail = _link_space(text, title_end)
    if tail is not None and tail < len(text) and text[tail] == ")":
        return destination, tail + 1
    return None


def _reference_label(text: str) -> str | None:
    if not 1 <= len(text) <= 999 or not text.strip():
        return None
    if any(char in "[]" and not _escaped(text, i) for i, char in enumerate(text)):
        return None
    return " ".join(text.casefold().split())


def _reference_definition(text: str, start: int) -> tuple[str, str, int] | None:
    """Read a complete definition at the beginning of a paragraph remainder."""
    match = re.match(r"[ \t]*\[", text[start:])
    if not match:
        return None
    value = _label(text, start + match.end() - 1)
    if value is None:
        return None
    label, end = value
    normalized = _reference_label(label)
    if normalized is None or text[end:end + 1] != ":":
        return None
    begin = _link_space(text, end + 1)
    if begin is None:
        return None
    value = _destination(text, begin)
    if value is None:
        return None
    destination, end = value

    def line_end(position: int) -> int | None:
        while position < len(text) and text[position] in " \t":
            position += 1
        if position == len(text):
            return position
        return position + 1 if text[position] == "\n" else None

    without_title = line_end(end)
    title_start = _link_space(text, end)
    if title_start is not None and title_start > end:
        title_end = _link_title(text, title_start)
        if title_end is not None:
            complete = line_end(title_end)
            if complete is not None:
                return normalized, destination, complete
    # A malformed title on the destination line invalidates the definition;
    # a malformed following line can instead begin the paragraph's prose.
    return (normalized, destination, without_title) if without_title is not None else None


def local_target(owner: Path, destination: str) -> Path | None:
    def decode(match):
        if match[1] is not None:
            return match[1]
        entity = match[0]
        if entity.startswith("&#"):
            # HTML's numeric replacements apply, but its removal of valid
            # control/noncharacter code points is not Markdown decoding.
            number = int(entity[3:-1], 16) if entity[2] in "xX" else int(entity[2:-1])
            return html.unescape(entity) or chr(number)
        return html.entities.html5.get(entity[1:], entity)

    # One pass preserves an escaped '&' and does not reinterpret a decoded
    # backslash or a second entity nested in an entity's replacement text.
    text = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_\x60{|}~])"
                  r"|&(?:\#[0-9]{1,7}|\#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});",
                  decode, destination)
    try:
        # urlsplit otherwise strips literal leading spaces and embedded line
        # controls, including characters legitimately decoded from entities.
        parts = urlsplit(quote(text, safe="/:#?[]@!$&'()*+,;=%"))
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        return None
    path = unquote(parts.path)
    if "\0" in path:
        return None
    return (owner.parent / path).resolve() if path else owner


def body_references(path: Path, body: str) -> tuple[Reference, ...]:
    """Local inline links and full/collapsed/shortcut reference links.

    This is a small Markdown link reader, not a renderer. Raw HTML, autolinks,
    images and generated references are not prompt dependency declarations.
    """
    definitions: dict[str, str] = {}
    inline_ranges: list[tuple[int, int]] = []
    clean = _prose_blocks(body.replace("\r\n", "\n").replace("\r", "\n"),
                          definitions=definitions,
                          inline_ranges=inline_ranges)
    refs: list[tuple[int, Reference]] = []
    # A completed inner link disables enclosing link openers. Image descriptions
    # may contain links syntactically, but declare no prompt dependencies.
    pattern = re.compile(r"!?\[|\]|`+|<")
    html_opener = re.compile(r"<!--|<\?|<!\[CDATA\[|<![A-Za-z]")
    for block_start, block_end in inline_ranges:
        # Resolve code delimiters in linear time, but consume them only when the
        # inline scan reaches them. A parsed destination/title or HTML tag owns
        # its punctuation; an earlier code opener can instead consume that text.
        code_ends: dict[int, int] = {}
        following: dict[int, int] = {}
        ticks = list(re.finditer(r"`+", clean[block_start:block_end]))
        for tick in reversed(ticks):
            start = block_start + tick.start()
            width = len(tick[0]) - int(_escaped(clean, start))
            if width and width in following:
                code_ends[start] = following[width] + width
            following[len(tick[0])] = start
        brackets: list[tuple[int, bool, bool]] = []
        failed_html: set[str] = set()
        parentheses: dict[int, int] | None = None

        def destination_parentheses() -> dict[int, int]:
            nonlocal parentheses
            if parentheses is None:
                parentheses = _destination_parentheses(clean, block_start, block_end)
            return parentheses

        index = block_start
        while match := pattern.search(clean, index, block_end):
            token = match[0]
            if token[0] == "`":
                index = code_ends.get(match.start(), match.end())
                continue
            if _escaped(clean, match.start()):
                # Escaping '!' leaves a link; escaping '[' leaves plain text.
                index = match.start() + 1 if token == "![" else match.end()
                continue
            if token == "<":
                opening = html_opener.match(clean, match.start(), block_end)
                kind = opening[0] if opening else None
                if kind and len(kind) == 3 and kind.startswith("<!"):
                    kind = "declaration"
                # If this opener had no closing delimiter before the block's
                # end, later openers of the same kind cannot have one either.
                # Keep scanning their literal contents for real Markdown links.
                opaque = None if kind in failed_html else (
                    _AUTOLINK.match(clean, match.start(), block_end)
                    or _INLINE_HTML.match(clean, match.start(), block_end))
                if kind and opaque is None:
                    failed_html.add(kind)
                index = opaque.end() if opaque else match.end()
                continue
            if token != "]":
                brackets.append((match.end() - 1, token == "![", True))
                index = match.end()
                continue
            index = match.end()
            if not brackets:
                continue
            opener, is_image, active = brackets.pop()
            if not active:
                continue
            label = clean[opener + 1:match.start()]
            end = match.end()
            destination = None
            if end < len(clean) and clean[end] == "(":
                value = _inline_link(clean, end + 1, destination_parentheses)
                if value and value[1] <= block_end:
                    destination, end = value
            if destination is None:
                value = _label(clean, end) if clean[end:end + 1] == "[" else None
                if (value and value[1] <= block_end
                        and (not value[0] or _reference_label(value[0]) is not None)):
                    label = value[0] or label
                    end = value[1]
                destination = definitions.get(_reference_label(label))
            if destination is None:
                continue
            if is_image:
                refs = [(position, ref) for position, ref in refs if position < opener]
            else:
                brackets = [(position, image, enabled and image)
                            for position, image, enabled in brackets]
                target = local_target(path, destination)
                if target is not None:
                    refs.append((opener, Reference(destination, target,
                                                  clean.count("\n", 0, opener) + 1)))
            index = end
    return tuple(ref for _, ref in refs)


def _read_module(graph: Graph, path: Path, *, metadata_only: bool = False,
                 optional: bool = False) -> Module | None:
    path = path.resolve()
    if not inside(path, graph.root / "src") or path.suffix != ".md":
        graph.errors.append(f"{relative(path, graph.root)}: prompt must be a src Markdown module")
        return None
    if not metadata_only and path in graph.modules:
        return graph.modules[path]
    if metadata_only:
        try:
            header = []
            with path.open(encoding="utf-8") as source:
                for row in source:
                    header.append(row)
                    if len(header) == 1 and row.strip() != "---":
                        break
                    if len(header) > 1 and row.strip() == "---":
                        break
            text = "".join(header)
            graph.metadata_reads += 1
        except (OSError, UnicodeError) as error:
            graph.errors.append(f"{relative(path, graph.root)}: cannot read header: {error}")
            return None
    else:
        text = _text(graph, path)
        if text is None:
            return None
    rel = relative(path, graph.root)
    rows = _source_lines(text)
    # Catalog/check scans permit ordinary Markdown support beside indexed memory.
    # Explicit prompt selection remains strict; malformed opt-in headers still fail.
    if optional and (not rows or rows[0] != "---"):
        return None
    if not rows or rows[0] != "---" or "---" not in rows[1:]:
        graph.errors.append(f"{rel}: missing frontmatter delimiters")
        return None
    end = rows.index("---", 1)
    before = len(graph.errors)
    fields: dict[str, str] = {}
    for number, row in enumerate(rows[1:end], 2):
        if not row.strip():
            continue
        key, separator, value = row.partition(":")
        if not separator or key not in HEADER_KEYS or key in fields or not value.strip():
            graph.errors.append(f"{rel}:{number}: invalid or duplicate metadata field")
            continue
        fields[key] = value.strip()
    if not ID_RE.fullmatch(fields.get("id", "")):
        graph.errors.append(f"{rel}: id must be a portable nonempty identity")
    if not fields.get("summary"):
        graph.errors.append(f"{rel}: summary is required")
    if "primary" in fields and fields["primary"] != "true":
        graph.errors.append(f"{rel}: primary must be exact 'true'; omit for direct-only")
    role = fields.get("role", "guidance")
    if role not in {"guidance", "reference"}:
        graph.errors.append(f"{rel}: role must be guidance or reference")
    if role == "reference" and "primary" in fields:
        graph.errors.append(f"{rel}: reference cannot be primary")
    if len(graph.errors) != before:
        return None
    body = "\n".join(rows[end + 1:])
    module = Module(path, fields["id"], fields["summary"],
                    "primary" in fields,
                    body, text, () if metadata_only else body_references(path, body),
                    role=role)
    if not metadata_only:
        other = graph.by_id.get(module.module_id)
        if other and other.path != path:
            graph.errors.append(f"{rel}: duplicate id {module.module_id}")
            return None
        graph.modules[path] = module
        graph.by_id[module.module_id] = module
    return module


def _paths(graph: Graph):
    return sorted((path.resolve() for path in (graph.root / "src").rglob("*.md")
                   if not any(part in SKIP for part in path.relative_to(graph.root).parts)),
                  key=lambda path: relative(path, graph.root))


def discover(root: Path) -> Graph:
    graph = Graph(root.resolve(), scope="catalog")
    graph.catalog = {}
    for path in _paths(graph):
        module = _read_module(graph, path, metadata_only=True, optional=True)
        if module:
            graph.catalog.setdefault(module.module_id, []).append(module)
    for identity, modules in graph.catalog.items():
        if len(modules) > 1:
            graph.errors.append(f"duplicate id {identity}")
    return graph


def _directives(graph: Graph) -> None:
    text = _text(graph, graph.root / "AGENTS.md")
    if text is None:
        return
    for key, regex in DIRECTIVES.items():
        matches = [match for row in _source_lines(text) if (match := regex.fullmatch(row))]
        if len(matches) != 1:
            graph.errors.append(f"AGENTS.md: expected exactly one {key} directive")
            continue
        if key == "narrow":
            continue
        path = (graph.root / matches[0][1]).resolve()
        if not inside(path, graph.root):
            graph.errors.append(f"AGENTS.md: {key} target escapes repository")
        elif key == "root":
            graph.composition_root = path
        else:
            graph.bootstrap_support = path


def load_task_graph(root: Path) -> Graph:
    graph = Graph(root.resolve())
    graph.revision = _git_revision(graph.root)
    _directives(graph)
    if graph.composition_root:
        _active_module(graph, graph.composition_root)
    return graph


def _active_module(graph: Graph, path: Path) -> Module | None:
    """A file's declared role bounds its use, independently of its location."""
    module = _read_module(graph, path)
    if module and module.role == "reference":
        graph.errors.append(
            f"{relative(path, graph.root)}: reference is data-only; use context/support")
        return None
    return module


def load_graph(root: Path) -> Graph:
    graph = load_task_graph(root)
    graph.scope = "repository"
    for path in _paths(graph):
        _read_module(graph, path, optional=True)
    if graph.bootstrap_support:
        _content(graph, graph.bootstrap_support)
    # Whole-repository validation intentionally includes dormant body/document links.
    for path in sorted(graph.root.rglob("*.md")):
        if any(part in SKIP for part in path.relative_to(graph.root).parts):
            continue
        text = _text(graph, path.resolve())
        if text is None:
            continue
        body = graph.modules[path.resolve()].body if path.resolve() in graph.modules else text
        for ref in body_references(path.resolve(), body):
            if not inside(ref.target, graph.root):
                graph.errors.append(f"{relative(path, graph.root)}:{ref.line}: link escapes repository")
            elif not ref.target.exists():
                graph.errors.append(f"{relative(path, graph.root)}:{ref.line}: missing link {ref.text}")
    graph.errors = sorted(set(graph.errors))
    return graph


def reference_reach(graph: Graph) -> dict[Path, tuple[Path, ...]]:
    """One shortest witness per reachable file, never all paths through diamonds."""
    if graph.composition_root is None:
        return {}
    witnesses = {graph.composition_root: (graph.composition_root,)}
    pending = deque([graph.composition_root])
    while pending:
        owner = pending.popleft()
        module = graph.modules.get(owner)
        if module is None:
            continue
        for ref in module.references:
            if inside(ref.target, graph.root) and ref.target not in witnesses:
                witnesses[ref.target] = witnesses[owner] + (ref.target,)
                pending.append(ref.target)
    return witnesses


def _seed_path(graph: Graph, target: str) -> Path | None:
    if "/" in target or "\\" in target or target.endswith(".md"):
        path = (graph.root / target).resolve()
        if not inside(path, graph.root):
            graph.errors.append(f"{target}: seed escapes repository")
            return None
        return path
    if target in graph.by_id:
        return graph.by_id[target].path
    if graph.catalog is None:
        catalog = discover(graph.root)
        graph.catalog = catalog.catalog
        graph.metadata_reads += catalog.metadata_reads
    matches = (graph.catalog or {}).get(target, [])
    if len(matches) != 1:
        graph.errors.append(f"{target}: seed id is unknown or ambiguous; use an exact path")
        return None
    return matches[0].path


def assemble(graph: Graph, seeds: list[Seed] | tuple[Seed, ...] = (),
             selections: list[Selection] | tuple[Selection, ...] = ()) -> Assembly:
    if graph.scope != "active":
        return Assembly(errors=("assemble requires a fresh task graph, not a catalog or repository check",))
    active: set[Path] = set()
    support: set[Path] = set()
    seed_rows: list[tuple[str, str, str]] = []
    trace: list[tuple[str, str, str, str]] = []
    if (graph.composition_root and graph.composition_root in graph.modules
            and graph.modules[graph.composition_root].role == "guidance"):
        active.add(graph.composition_root)
        seed_rows.append(("prompt", relative(graph.composition_root, graph.root), "universal task entry"))
    if graph.bootstrap_support:
        support.add(graph.bootstrap_support)
    for seed in seeds:
        if not seed.evidence.strip() or seed.kind not in {"prompt", "support"}:
            graph.errors.append("seed requires prompt/support mode and a nonempty purpose")
            continue
        path = _seed_path(graph, seed.target)
        if path is None:
            continue
        by_identity = bool(ID_RE.fullmatch(seed.target)) and not seed.target.endswith(".md")
        if seed.kind == "prompt" or by_identity:
            module = (_active_module if seed.kind == "prompt" else _read_module)(graph, path)
            if not module:
                continue
            if by_identity and module.module_id != seed.target:
                graph.errors.append(f"{seed.target}: identity changed after discovery")
                continue
        if seed.kind == "prompt":
            active.add(path)
        else:
            support.add(path)
        seed_rows.append((seed.kind, relative(path, graph.root), seed.evidence))
    pending = sorted(set(selections), key=lambda row: (row.owner, row.target, row.kind, row.evidence))
    while pending:
        deferred = []
        progress = False
        for choice in pending:
            if not choice.evidence.strip() or choice.kind not in {"prompt", "support"}:
                graph.errors.append("selection requires prompt/support mode and nonempty task evidence")
                continue
            owner = graph.by_id.get(choice.owner)
            if owner is None or owner.path not in active:
                deferred.append(choice)
                continue
            progress = True
            target = local_target(owner.path, choice.target)
            if target is None or not inside(target, graph.root):
                graph.errors.append(f"{choice.owner}: selection must stay inside repository: {choice.target}")
                continue
            if target not in {ref.target for ref in owner.references}:
                graph.errors.append(f"{choice.owner}: no body reference to {choice.target}")
                continue
            if choice.kind == "prompt":
                module = _active_module(graph, target)
                if not module:
                    continue
                active.add(target)
            else:
                support.add(target)
            trace.append((choice.owner, choice.kind, relative(target, graph.root), choice.evidence))
        if not progress:
            graph.errors.extend(f"{choice.owner}: selected source is not an active prompt" for choice in deferred)
            break
        pending = deferred
    for path in sorted(support):
        _content(graph, path)
    if graph.errors:
        return Assembly(errors=tuple(sorted(set(graph.errors))))
    modules = tuple(sorted((graph.modules[path] for path in active),
                           key=lambda module: (module.path != graph.composition_root, module.module_id)))
    supports = tuple(sorted(support, key=lambda path: relative(path, graph.root)))
    identities = [("bootstrap", graph.root / "AGENTS.md")]
    identities += [("prompt", module.path) for module in modules]
    identities += [("support", path) for path in supports]
    digest = hashlib.sha256()
    for kind, path in identities:
        digest.update(f"{kind}\0{relative(path, graph.root)}\0".encode())
        digest.update(hashlib.sha256(graph.contents[path]).digest())
    return Assembly(modules, supports, tuple(seed_rows), tuple(sorted(set(trace))), (),
                    digest.hexdigest(), sum(len(graph.contents[path]) for path in supports))


def failure_lines(errors: list[str] | tuple[str, ...]) -> list[str]:
    return [*(f"ERROR\t{error}" for error in sorted(set(errors))), "FAIL"]


# Scripts written without spaces between words (Han, kana, Hangul).
_CJK = (r"\u2e80-\u2fdf\u3040-\u30ff\u3100-\u31bf\u3400-\u4dbf\u4e00-\u9fff"
        r"\ua960-\ua97f\uac00-\ud7af\uf900-\ufaff\uff66-\uff9f")
# Whitespace and CJK or fullwidth punctuation separate query words.
_SEPARATORS = r"\s\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65"
_QUERY_WORD = re.compile(rf"([{_CJK}]+)|[^{_SEPARATORS}{_CJK}]+")
# Quotes, brackets and clause punctuation around a word are not part of it.
_WRAPPING = "\"'()[]{}<>,;:!?\u00ab\u00bb\u2018\u2019\u201c\u201d"
_LETTER = rf"[^\W\d_{_CJK}]"
# A common English suffix, longest first, with the shortest stem it may leave.
_SUFFIXES = (("ations", 5), ("ation", 5), ("ments", 5), ("ment", 5), ("ions", 5), ("ion", 5),
             ("ings", 5), ("ing", 5), ("ies", 3), ("ied", 3), ("ed", 5), ("es", 3), ("s", 4))
# A stem matches only when one of these endings, or none, completes the word,
# so 'experiment' (experi-) misses 'experience' and 'queries' finds 'query'.
_ENDINGS = "(?:e|es|s|d|ed|ing|ings|ion|ions|ation|ations|ate|ates|ated|ating|ment|ments)?"
_Y_ENDINGS = "(?:y|ie|ies|ied)"


@dataclass(frozen=True)
class Term:
    text: str
    word: str  # the query word; a CJK run yields several character pairs
    group: int
    key: str  # a substring every match contains, checked before the pattern
    literal: re.Pattern[str]  # the term as typed, where a word starts
    match: re.Pattern[str]  # the literal term, or its stem completed by an inflection
    whole: re.Pattern[str]  # the term as typed, as a whole word

    def found(self, text: str) -> bool:
        return self.key in text and bool(self.match.search(text))


def _stem(word: str) -> tuple[str, str] | None:
    """The stem left by one common English suffix, and the endings that complete it."""
    if not (word.isascii() and word.isalpha()):
        return None
    for suffix, shortest in _SUFFIXES:
        stem = word[:-len(suffix)]
        if not word.endswith(suffix) or len(stem) < shortest:
            continue
        if suffix == "es" and not stem.endswith(("s", "x", "z", "ch", "sh")):
            continue  # 'files' is file+s; 'classes' is class+es
        if suffix == "s" and word.endswith(("ss", "us", "is")):
            continue  # 'press', 'status' and 'basis' are not plurals
        return stem, (_Y_ENDINGS if suffix in ("ies", "ied") else _ENDINGS)
    return None


def _word_class(char: str) -> str:
    """Letters and digits form separate words, so '79' starts a word in 'item79'."""
    if re.fullmatch(_LETTER, char):
        return _LETTER
    return r"\d" if char.isdigit() else ""


def _term(text: str, word: str, group: int) -> Term:
    before, after = _word_class(text[0]), _word_class(text[-1])
    start = f"(?<!{before})" if before else ""
    literal = start + re.escape(text) + (f"(?!{after})" if before and after and len(text) <= 2 else "")
    stemmed = _stem(text)
    match = literal
    if stemmed:
        stem, endings = stemmed
        match = f"{start}(?:{re.escape(text)}|{re.escape(stem)}{endings}(?!{_LETTER}))"
    return Term(text, word, group, stemmed[0] if stemmed else text, re.compile(literal),
                re.compile(match), re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)"))


def query_terms(query: str) -> tuple[Term, ...]:
    """Casefolded terms from whitespace-separated words, split where CJK text begins or ends.

    A term matches where a word starts, so 'gh' misses 'might' and 'vision'
    misses 'revision'; a term of one or two characters must be a whole word.
    A CJK run longer than two characters becomes its overlapping pairs; CJK
    and punctuation-led terms match anywhere. Wrapping quotes, brackets and
    clause punctuation are dropped, and words without letters or digits skipped.
    """
    terms: dict[str, Term] = {}
    for group, match in enumerate(_QUERY_WORD.finditer(query.casefold())):
        word = match[0] if match[1] else match[0].strip(_WRAPPING)
        if not re.search(r"\w", word):
            continue
        pairs = match[1] and len(word) > 2
        for text in ([word[i:i + 2] for i in range(len(word) - 1)] if pairs else [word]):
            terms.setdefault(text, _term(text, word, group))
    return tuple(terms.values())


@dataclass(frozen=True)
class _Coverage:
    """Items containing every term, otherwise those containing the most query words."""
    indices: tuple[int, ...]
    full: bool
    present: int  # query words each partial result contains
    rarity: tuple[float, ...]  # per item, summed log inverse frequency of matched terms
    absent: tuple[str, ...]  # query words none of whose terms occur anywhere

    def note(self, terms: tuple[Term, ...], unit: str) -> str | None:
        words = list(dict.fromkeys(term.word for term in terms))
        nowhere = f"; found nowhere: {' '.join(self.absent)}" if self.absent else ""
        if self.indices and not self.full:
            share = f"{self.present} of {len(words)} query words" if len(words) > 1 else f"most of {words[0]}"
            return f"NOTE\tNo {unit} contains every term; these contain {share}{nowhere}"
        if not self.indices and self.absent and len(words) > 1:
            return f"NOTE\tNo {unit} contains every term{nowhere}"
        return None


def _coverage(terms: tuple[Term, ...], texts: list[str]) -> _Coverage:
    """Require every term, or else most query words: at least two, and more than half.

    A CJK word is present when more than half of its character pairs are.
    Partial results rank by the rarity of their matched terms.
    """
    hits = [[term.found(text) for term in terms] for text in texts]
    groups: dict[int, list[int]] = {}
    for index, term in enumerate(terms):
        groups.setdefault(term.group, []).append(index)
    frequency = [sum(row[index] for row in hits) for index in range(len(terms))]
    absent = tuple(terms[members[0]].word for members in groups.values()
                   if not any(frequency[index] for index in members))
    indices = [item for item, row in enumerate(hits) if all(row)]
    full, present = bool(indices), len(groups)
    if not full:
        counts = [sum(2 * sum(row[index] for index in members) > len(members) for members in groups.values())
                  for row in hits]
        present = max(counts, default=0)
        if not present or 2 * present <= len(groups) or (present < 2 and len(groups) > 1):
            return _Coverage((), False, present, (), absent)
        indices = [item for item, count in enumerate(counts) if count == present]
    weights = [math.log(len(texts) / count) if count else 0.0 for count in frequency]
    rarity = tuple(sum(weight for weight, hit in zip(weights, hits[item]) if hit) for item in indices)
    return _Coverage(tuple(indices), full, present, rarity, absent)


def _body_text(graph: Graph, path: Path) -> str:
    """A document's text after its metadata header, which is not content."""
    rows = _source_lines(_text(graph, path) or "")
    if rows and rows[0] == "---" and "---" in rows[1:]:
        rows = rows[rows.index("---", 1) + 1:]
    return "\n".join(rows)


def list_lines(graph: Graph, *, query: str = "", limit: int | None = 20,
               primary_only: bool = False) -> list[str]:
    """Match identities and summaries; read document text only when none match."""
    terms = query_terms(query)
    pool = sorted((module for group in (graph.catalog or {}).values() for module in group
                   if not primary_only or module.primary),
                  key=lambda module: (module.module_id, relative(module.path, graph.root)))
    headers = [f"{module.module_id} {module.summary}".casefold() for module in pool]
    found = _coverage(terms, headers)
    # Whole words, then words as typed, precede stem matches; ids break ties.
    order = sorted((-sum(bool(term.whole.search(headers[index])) for term in terms),
                    -sum(bool(term.literal.search(headers[index])) for term in terms), index)
                   for index in found.indices)
    notes: list[str] = []
    # Summaries rarely name particulars such as projects; read text only when
    # no header has every term, so ordinary listing stays header-only.
    if terms and not found.full:
        reads = graph.body_reads
        texts = [header + "\n" + _body_text(graph, module.path).casefold()
                 for header, module in zip(headers, pool)]
        found = _coverage(terms, texts)
        order = sorted((-rarity, -sum(term.found(headers[index]) for term in terms),
                        -sum(bool(term.literal.search(texts[index])) for term in terms), index)
                       for index, rarity in zip(found.indices, found.rarity))
        notes = [f"FILE_READS\t{graph.body_reads - reads}"]
        if found.indices and found.full:
            notes.append("NOTE\tNo identity or summary contains every term; these documents do in their text"
                         " (search shows where)")
        elif note := found.note(terms, "document"):
            notes.append(note)
    candidates = [pool[key[-1]] for key in order]
    selected = candidates if limit is None else candidates[:limit]
    diagnostics = [f"WARNING\tDiscovery incomplete: {error}" for error in sorted(set(graph.errors))[:3]]
    if len(graph.errors) > 3:
        diagnostics.append("WARNING\tMore diagnostics available with check")
    return [
        *diagnostics, f"MATCHES\t{len(candidates)}\tSHOWN\t{len(selected)}",
        f"METADATA_READS\t{graph.metadata_reads}", *notes,
        *(f"{module.module_id}\t{relative(module.path, graph.root)}\t{module.summary}\t"
          f"{'reference' if module.role == 'reference' else 'primary' if module.primary else 'direct-only'}"
          for module in selected),
    ]


def _sections(module: Module):
    """Yield contiguous sections and heading context, without treating code as headings."""
    rows = _source_lines(module.text)
    start = rows.index("---", 1) + 1
    prose = _source_lines(_prose_blocks(module.text))
    headings: list[tuple[int, str]] = []
    heading_marker = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]|$)")
    for number in range(start, len(rows)):
        if not heading_marker.match(prose[number]):
            continue
        match = heading_marker.match(rows[number])
        # Masking detects block boundaries; raw text preserves inline code and
        # punctuation in searchable ancestry. Closing hashes need whitespace.
        title = re.sub(r"(?:^|[ \t])#+[ \t]*$", "", rows[number][match.end():]).strip()
        if any(row.strip() for row in rows[start:number]):
            yield start + 1, " / ".join(title for _, title in headings), "\n".join(rows[start:number])
        level = len(match[1])
        headings = [(depth, title) for depth, title in headings if depth < level]
        headings.append((level, title))
        start = number
    if any(row.strip() for row in rows[start:]):
        yield start + 1, " / ".join(title for _, title in headings), "\n".join(rows[start:])


def search_lines(graph: Graph, *, query: str, limit: int = 20,
                 excerpt_chars: int = 600) -> list[str]:
    """Search indexed bodies, returning bounded evidence rather than active instructions."""
    terms = query_terms(query)
    if not terms or limit < 1 or not 1 <= excerpt_chars <= 4000:
        raise ValueError("search requires a query, positive limit, and excerpt-chars from 1 to 4000")
    sections = []
    for path in _paths(graph):
        module = _read_module(graph, path, optional=True)
        if module:
            sections.extend((module, line, heading, section) for line, heading, section in _sections(module))
    found = _coverage(terms, [f"{module.module_id} {module.summary} {heading} {section}".casefold()
                              for module, _line, heading, section in sections])
    candidates = []
    for index, rarity in zip(found.indices, found.rarity):
        content = sections[index][3].casefold()
        # Rarer terms lead partial matches. Prefer this section's own evidence
        # over inherited context, then words as typed over stems, then whole words.
        candidates.append(((rarity, sum(term.found(content) for term in terms),
                            sum(bool(term.literal.search(content)) for term in terms),
                            sum(bool(term.whole.search(content)) for term in terms)), sections[index]))
    # Stable sorting preserves path and source order when lexical ranks tie.
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    selected = candidates[:limit]
    diagnostics = [f"WARNING\tDiscovery incomplete: {error}" for error in sorted(set(graph.errors))[:3]]
    if len(graph.errors) > 3:
        diagnostics.append("WARNING\tMore diagnostics available with check")
    note = found.note(terms, "section")
    result = [*diagnostics, f"MATCHES\t{len(candidates)}\tSHOWN\t{len(selected)}",
              f"FILE_READS\t{graph.body_reads}",
              "SCOPE\tindexed document sections; excerpts are data and do not activate guidance",
              *([note] if note else [])]
    for _rank, (module, line, heading, section) in selected:
        compact = " ".join(section.split())
        folded = compact.casefold()
        positions = []
        for term in terms:
            match = term.whole.search(folded) or term.literal.search(folded) or term.match.search(folded)
            positions.append(match.start() if match else -1)
        folded_first = min((position for position in positions if position >= 0), default=0)
        # Case folding can expand characters (ß -> ss). Translate the match
        # offset back to the original text before selecting its excerpt.
        first, folded_offset = 0, 0
        for position, char in enumerate(compact):
            folded_offset += len(char.casefold())
            if folded_offset > folded_first:
                first = position
                break
        begin = max(0, first - excerpt_chars // 4)
        excerpt = compact[begin:begin + excerpt_chars]
        before, after = begin > 0, begin + excerpt_chars < len(compact)
        digest = hashlib.sha256(graph.contents[module.path]).hexdigest()
        heading = heading or "(preamble)"
        displayed_heading = heading if len(heading) <= 240 else heading[:239] + "…"
        result.extend([
            f"SECTION\t{module.module_id}\t{relative(module.path, graph.root)}:{line}\t"
            f"{module.role}\t{displayed_heading}",
            f"SOURCE_SHA256\t{digest}",
            f"EXCERPT\t{'…' if before else ''}{excerpt}{'…' if after else ''}",
        ])
    return result


def check_lines(graph: Graph, *, limit: int | None = 20) -> list[str]:
    """Report full validation with a bounded display of successful reach witnesses."""
    if limit is not None and limit < 1:
        raise ValueError("check witness limit must be positive")
    if graph.errors:
        return failure_lines(graph.errors)
    witnesses = reference_reach(graph)
    ordered = sorted(witnesses.items())
    selected = ordered if limit is None else ordered[:limit]
    return [
        "PASS\tcheck",
        "SCOPE\trepository representation and local Markdown links; reference reach is syntactic only",
        f"MODULES\t{len(graph.modules)}",
        f"BODY_REFERENCES\t{sum(len(module.references) for module in graph.modules.values())}",
        f"ROOT_REFERENCE_REACH\t{len(witnesses)}",
        f"REACH_SHOWN\t{len(selected)}",
        *(f"REACH\t{relative(path, graph.root)}\tvia\t"
          + " -> ".join(relative(item, graph.root) for item in witness)
          for path, witness in selected),
        *(["NOTE\tMore reference witnesses omitted; use check --all to display every witness."]
          if len(selected) < len(ordered) else []),
    ]


def size_check_lines(root: Path) -> list[str]:
    """Apply the repository's hard Markdown budgets after structural checking."""
    if not (root / "memory-limits.json").exists() and not (root / "tools/memory.py").exists():
        return []  # Synthetic module fixtures need no budget policy.
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("memory.py")),
                             "--root", str(root), "sizes", "--limit", "20"],
                            capture_output=True, text=True)
    if result.returncode not in (0, 1):
        return [f"ERROR\tsize check: {result.stderr.strip() or 'invalid result'}", "FAIL"]
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ["ERROR\tsize check returned invalid JSON", "FAIL"]
    if result.returncode == 0:
        return [f"SIZE_CHECK\tPASS\t{report['text_files']} governed files"]
    rows = [f"ERROR\tsize check: {report['over_limit_files']} governed files exceed limits"]
    rows.extend(f"SIZE_OVER\t{item['path']}\t{item['chars']}/{item['limit']}"
                for item in report["records"])
    if report["truncated"]:
        rows.append("NOTE\tMore size overages omitted; use memory.py sizes --limit 100")
    return [*rows, "FAIL"]


def assembly_lines(graph: Graph, assembly: Assembly) -> list[str]:
    if assembly.errors:
        return failure_lines(assembly.errors)
    bootstrap_chars = len(graph.contents[graph.root / "AGENTS.md"].decode("utf-8"))
    module_chars = sum(len(module.text) for module in assembly.modules)
    return [
        "PASS\tassemble",
        "SCOPE\tselected references and captured content; conditions, completeness and dormant links not checked",
        f"REVISION_HINT\t{graph.revision}",
        f"ACTIVE_DIGEST\t{assembly.digest}",
        *(f"SEED\t{kind}\t{path}\t{purpose}" for kind, path, purpose in assembly.seeds),
        f"ACTIVE_MODULES\t{len(assembly.modules)}",
        f"BOOTSTRAP_CHARS\t{bootstrap_chars}",
        f"ACTIVE_MODULE_CHARS\t{module_chars}",
        f"ACTIVE_PROMPT_CHARS\t{bootstrap_chars + module_chars}",
        f"ACTIVE_SUPPORT_BYTES\t{assembly.support_bytes}",
        f"METADATA_READS\t{graph.metadata_reads}",
        f"FILE_READS\t{graph.body_reads}",
        *(f"MODULE\t{module.module_id}\t{relative(module.path, graph.root)}" for module in assembly.modules),
        *(f"USE\t{owner}\t{kind}\t{path}\t{evidence}" for owner, kind, path, evidence in assembly.selections),
        *(f"SUPPORT\t{relative(path, graph.root)}" for path in assembly.support),
    ]


def _parse_seed(raw: str, kind: str) -> Seed:
    target, separator, evidence = raw.partition("|")
    if not target.strip() or not separator or not evidence.strip():
        raise ValueError("seed/context must be target|purpose")
    return Seed(target.strip(), evidence.strip(), kind)


def _parse_selection(raw: str, kind: str) -> Selection:
    source_target, separator, evidence = raw.partition("|")
    owner, colon, target = source_target.partition(":")
    if not separator or not colon or not ID_RE.fullmatch(owner) or not target.strip() or not evidence.strip():
        raise ValueError("prompt/support must be owner.id:relative-link|task evidence")
    return Selection(owner, target.strip(), evidence.strip(), kind)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: python tools/modules.py assemble --seed 'core.taste|judgment for this task'. "
               "Quote each selection so the shell does not interpret the | character.",
    )
    parser.add_argument("command", choices=("list", "search", "check", "assemble"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seed", action="append", default=[], help="'target|purpose', both required; target is a module id or path; activate as prompt")
    parser.add_argument("--context", action="append", default=[], help="'target|purpose', both required; target is a module id or path; read as data")
    parser.add_argument("--prompt", action="append", default=[], help="owner.id:relative-link|task evidence")
    parser.add_argument("--support", action="append", default=[], help="owner.id:relative-link|task evidence")
    parser.add_argument("--query", default="",
                        help="case-insensitive words matched where words start, with light stemming; list "
                             "reads document text only when no identity and summary contain every term")
    parser.add_argument("--limit", type=int, default=20,
                        help="maximum list/search results or check witnesses (default: 20)")
    parser.add_argument("--excerpt-chars", type=int, default=600, help="search: excerpt characters, 1–4000")
    parser.add_argument("--all", action="store_true", help="list/check: display every result")
    parser.add_argument("--primary-only", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.command == "list":
        rows = list_lines(discover(args.root), query=args.query,
                          limit=None if args.all else args.limit, primary_only=args.primary_only)
    elif args.command == "search":
        if args.all or args.primary_only:
            parser.error("search is bounded and includes every role; use --limit, without --all or --primary-only")
        try:
            rows = search_lines(Graph(args.root.resolve(), scope="search"), query=args.query,
                                limit=args.limit, excerpt_chars=args.excerpt_chars)
        except ValueError as error:
            parser.error(str(error))
    elif args.command == "check":
        root = args.root.resolve()
        rows = check_lines(load_graph(root), limit=None if args.all else args.limit)
        budget = size_check_lines(root)
        if budget:
            structure_failed = rows[-1] == "FAIL"
            if structure_failed:
                rows.pop()
            rows.extend(budget)
            if structure_failed and rows[-1] != "FAIL":
                rows.append("FAIL")
    else:
        try:
            seeds = [_parse_seed(raw, kind) for kind, raws in
                     (("prompt", args.seed), ("support", args.context)) for raw in raws]
            choices = [_parse_selection(raw, kind) for kind, raws in
                       (("prompt", args.prompt), ("support", args.support)) for raw in raws]
        except ValueError as error:
            parser.error(str(error))
        graph = load_task_graph(args.root)
        rows = assembly_lines(graph, assemble(graph, seeds, choices))
    print("\n".join(rows))
    return 1 if rows[-1] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
