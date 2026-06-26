"""A tiny, dependency-free YAML loader for the homed config subset.

Why this exists
---------------
homed aims to run on the standard library alone. The config format is YAML
because that is what homelab operators expect, but pulling in PyYAML just to
read a small, well-structured file is more dependency than the job needs.

This module implements the *subset* of YAML that the homed schema uses:

* a top-level mapping;
* nested mappings, indented with spaces;
* block sequences (``- item``);
* inline flow sequences of scalars (``[a, b, c]``) and empty maps (``{}``);
* scalars: quoted/bare strings, integers, floats, booleans, null;
* ``#`` comments (whole-line and trailing, outside quotes);
* blank lines.

It deliberately does **not** support anchors, aliases, multi-document streams,
block scalars (``|`` / ``>``), complex flow mappings, or tags. If a config uses
those, install PyYAML and homed will prefer it automatically (see
:func:`homed.config.load_raw`).

Keeping this small and readable is the point: the parser should be auditable in
one sitting.
"""

from __future__ import annotations

from typing import Any, List, Tuple


class YamlError(ValueError):
    """Raised when the input is outside the supported YAML subset."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~", "none"}


def load(text: str) -> Any:
    """Parse *text* and return the corresponding Python object.

    Returns ``{}`` for empty/comment-only input.
    """
    raw_lines = text.splitlines()
    logical = _strip_and_index(raw_lines)
    if not logical:
        return {}
    value, idx = _parse_block(logical, 0, _indent(logical[0][1]))
    if idx != len(logical):
        # Trailing content at a shallower indent than where we started.
        raise YamlError("unexpected dedent / trailing content", logical[idx][0])
    return value


# --- internals -------------------------------------------------------------

# A "logical line" is a tuple of (1-based source line number, content) for every
# line that carries data (comments and blank lines removed).
_Line = Tuple[int, str]


def _strip_and_index(raw_lines: List[str]) -> List[_Line]:
    out: List[_Line] = []
    for i, line in enumerate(raw_lines, start=1):
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise YamlError("tabs are not allowed for indentation", i)
        stripped = _strip_comment(line)
        if stripped.strip() == "":
            continue
        out.append((i, stripped.rstrip()))
    return out


def _strip_comment(line: str) -> str:
    """Remove a trailing/leading ``#`` comment that is outside quotes."""
    in_single = in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            # A '#' only starts a comment at start-of-line or after whitespace.
            if idx == 0 or line[idx - 1] in " \t":
                return line[:idx]
    return line


def _indent(content: str) -> int:
    return len(content) - len(content.lstrip(" "))


def _parse_block(lines: List[_Line], start: int, indent: int) -> Tuple[Any, int]:
    """Parse a block (mapping or sequence) at the given indent level."""
    first = lines[start][1]
    if first.lstrip(" ").startswith("- "):
        return _parse_sequence(lines, start, indent)
    if first.lstrip(" ") == "-":
        return _parse_sequence(lines, start, indent)
    return _parse_mapping(lines, start, indent)


def _parse_mapping(lines: List[_Line], start: int, indent: int) -> Tuple[Any, int]:
    result: dict = {}
    idx = start
    while idx < len(lines):
        lineno, content = lines[idx]
        cur = _indent(content)
        if cur < indent:
            break
        if cur > indent:
            raise YamlError("unexpected indentation", lineno)
        body = content[indent:]
        key, sep, rest = _split_key(body, lineno)
        if not sep:
            raise YamlError(f"expected 'key: value' mapping, got {body!r}", lineno)
        rest = rest.strip()
        if rest == "":
            # Nested block belongs to this key (or it is an explicit null).
            if idx + 1 < len(lines) and _indent(lines[idx + 1][1]) > indent:
                child_indent = _indent(lines[idx + 1][1])
                value, idx = _parse_block(lines, idx + 1, child_indent)
                result[key] = value
                continue
            result[key] = None
            idx += 1
            continue
        result[key] = _parse_scalar_or_flow(rest, lineno)
        idx += 1
    return result, idx


def _parse_sequence(lines: List[_Line], start: int, indent: int) -> Tuple[Any, int]:
    result: list = []
    idx = start
    while idx < len(lines):
        lineno, content = lines[idx]
        cur = _indent(content)
        if cur < indent:
            break
        if cur > indent:
            raise YamlError("unexpected indentation in sequence", lineno)
        body = content[indent:]
        if body == "-":
            # Item is a nested block on following deeper lines.
            if idx + 1 < len(lines) and _indent(lines[idx + 1][1]) > indent:
                child_indent = _indent(lines[idx + 1][1])
                value, idx = _parse_block(lines, idx + 1, child_indent)
                result.append(value)
                continue
            result.append(None)
            idx += 1
            continue
        if not body.startswith("- "):
            break
        item = body[2:].strip()
        # Inline "key: value" after a dash starts a mapping whose first key sits
        # on the dash line. We re-parse it as a mapping at the dash content
        # column so additional keys (deeper-indented) attach correctly.
        key, sep, _rest = _split_key(item, lineno)
        if sep and not _looks_like_flow(item):
            item_indent = indent + 2
            # Rewrite the current line so the first key aligns at item_indent.
            patched = lines[:idx] + [(lineno, " " * item_indent + item)] + lines[idx + 1 :]
            value, idx = _parse_mapping(patched, idx, item_indent)
            result.append(value)
            continue
        result.append(_parse_scalar_or_flow(item, lineno))
        idx += 1
    return result, idx


def _split_key(body: str, lineno: int) -> Tuple[str, bool, str]:
    """Split ``key: value`` honoring quotes. Returns (key, found_sep, rest)."""
    in_single = in_double = False
    for i, ch in enumerate(body):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 >= len(body) or body[i + 1] == " ":
                return _scalar_str(body[:i].strip(), lineno), True, body[i + 1 :]
    return body, False, ""


def _looks_like_flow(text: str) -> bool:
    return text.startswith("[") or text.startswith("{")


def _parse_scalar_or_flow(text: str, lineno: int) -> Any:
    text = text.strip()
    if text.startswith("["):
        return _parse_flow_sequence(text, lineno)
    if text.startswith("{"):
        if text.replace(" ", "") == "{}":
            return {}
        raise YamlError("non-empty flow mappings are not supported", lineno)
    return _parse_scalar(text, lineno)


def _parse_flow_sequence(text: str, lineno: int) -> List[Any]:
    if not text.endswith("]"):
        raise YamlError("unterminated flow sequence", lineno)
    inner = text[1:-1].strip()
    if inner == "":
        return []
    items: List[Any] = []
    for part in _split_flow(inner, lineno):
        items.append(_parse_scalar(part.strip(), lineno))
    return items


def _split_flow(inner: str, lineno: int) -> List[str]:
    parts: List[str] = []
    buf = []
    in_single = in_double = False
    for ch in inner:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == "," and not in_single and not in_double:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _parse_scalar(text: str, lineno: int) -> Any:
    if text == "":
        return None
    if (text[0] == '"' and text[-1] == '"' and len(text) >= 2) or (
        text[0] == "'" and text[-1] == "'" and len(text) >= 2
    ):
        return _scalar_str(text, lineno)
    low = text.lower()
    if low in _NULL:
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    # int
    try:
        if text == str(int(text)):
            return int(text)
    except ValueError:
        pass
    # float
    try:
        f = float(text)
        if low not in ("nan", "inf", "-inf"):  # keep these as strings; uncommon
            return f
    except ValueError:
        pass
    return text


def _scalar_str(text: str, lineno: int) -> str:
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unescape_double(text[1:-1])
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _unescape_double(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            mapping = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)
