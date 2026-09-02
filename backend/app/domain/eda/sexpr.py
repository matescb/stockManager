"""A minimal s-expression reader/writer for KiCad library files.

KiCad stores symbols (`.kicad_sym`) and footprints (`.kicad_mod`) as
s-expressions. We need to do four things with them — name a library
entry, rename it, read/write a `(property "Key" "Value")`, and rewrite
`(model "PATH" …)` references — while leaving every node we don't
understand untouched. That is far less than a full format binding, so
this module is a tokenizer plus an emitter rather than a dependency:
`kiutils` (the only maintained candidate) lags the format by a release
or two and would fail on nodes it hasn't been taught, which is exactly
the behaviour we must not have when the input is a user's upload.

Representation
--------------
A node is a `list` whose members are atoms (`str`) or nested nodes.
`(symbol "R" (property "Reference" "R"))` parses to::

    ["symbol", Quoted("R"), ["property", Quoted("Reference"), Quoted("R")]]

`Quoted` is a `str` subclass, so callers can treat every atom as a
plain string; it only records that the token *was* quoted in the source
so `emit` can re-quote it. Without that distinction the bare token
`yes` and the string `"yes"` — which mean different things to KiCad —
would round-trip to the same text.

Every transformation here is non-mutating: `rename`, `set_property` and
`rewrite_model_paths` return a NEW node and share the untouched
sub-nodes with the input. Nothing mutates a node in place, so a caller
holding the parsed original keeps it intact.

Round-trip contract: `emit(parse(text))` is *semantically* stable, not
byte-stable. Whitespace and indentation are normalised; token content,
quoting and ordering are preserved exactly.
"""
from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "Node",
    "Quoted",
    "SexprError",
    "SYMBOL_LIB_ROOT",
    "SYMBOL_ROOT",
    "FOOTPRINT_ROOTS",
    "parse",
    "emit",
    "head",
    "entries",
    "entry_name",
    "rename",
    "get_property",
    "set_property",
    "model_paths",
    "rewrite_model_paths",
]


class SexprError(ValueError):
    """The input isn't a well-formed s-expression.

    Callers in the upload path turn this into a 422 — it always means
    "the bytes you sent are not a KiCad library file", never an
    internal fault.
    """


class Quoted(str):
    """An atom that was written as `"…"` in the source.

    A `str` subclass so every atom stays a plain string to callers;
    `emit` uses the type to decide whether to re-quote.
    """

    __slots__ = ()


# A node is a list of atoms and nested nodes. Python's type system can't
# express the recursion without a forward reference, and the runtime
# never inspects this alias, so the loose form is the honest one.
Node = list

SYMBOL_LIB_ROOT = "kicad_symbol_lib"
SYMBOL_ROOT = "symbol"
# `module` is the pre-6.0 spelling of `footprint`; files exported by
# older tools still use it and parse identically.
FOOTPRINT_ROOTS = ("footprint", "module")

_PROPERTY = "property"
_MODEL = "model"

# Nesting cap. Real KiCad files sit around 6 levels deep; 32 is still
# generous headroom while stopping a hand-crafted "((((…" upload from
# exhausting the C stack in `emit`'s recursion — and, combined with the
# post-emit size cap in storage.py, from using depth×width indentation
# as a 198x output-amplification lever (P2 security review HIGH-1).
_MAX_DEPTH = 32

_WHITESPACE = " \t\r\n"
# A bare atom runs until whitespace, a paren, or a quote.
_ATOM_END = frozenset(' \t\r\n()"')

_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


def parse(text: str) -> Node:
    """Parse `text` into a single top-level node.

    Iterative (explicit stack) rather than recursive: the input is an
    upload, and a recursive-descent reader would turn a deeply nested
    file into a `RecursionError` — a 500 — instead of a 422.

    Raises `SexprError` on unbalanced parens, an unterminated string, an
    atom outside any expression, excessive nesting, an empty document,
    or a second top-level expression.
    """
    stack: list[Node] = []
    root: Node | None = None
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch in _WHITESPACE:
            i += 1
            continue

        if ch == "(":
            node: Node = []
            if stack:
                stack[-1].append(node)
            elif root is not None:
                raise SexprError("unexpected second top-level expression")
            else:
                root = node
            stack.append(node)
            if len(stack) > _MAX_DEPTH:
                raise SexprError(f"nesting deeper than {_MAX_DEPTH} levels")
            i += 1
            continue

        if ch == ")":
            if not stack:
                raise SexprError("unbalanced ')'")
            stack.pop()
            i += 1
            continue

        if ch == '"':
            value, i = _read_quoted(text, i)
            if not stack:
                raise SexprError("string outside of an expression")
            stack[-1].append(value)
            continue

        j = i
        while j < n and text[j] not in _ATOM_END:
            j += 1
        if not stack:
            raise SexprError("atom outside of an expression")
        stack[-1].append(text[i:j])
        i = j

    if stack:
        raise SexprError("unbalanced '('")
    if root is None:
        raise SexprError("empty document")
    return root


def _read_quoted(text: str, i: int) -> tuple[Quoted, int]:
    """Read the string starting at `text[i] == '"'`. Returns the decoded
    value and the index just past the closing quote."""
    i += 1
    n = len(text)
    out: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                raise SexprError("unterminated escape sequence")
            nxt = text[i + 1]
            if nxt in _UNESCAPE:
                out.append(_UNESCAPE[nxt])
            else:
                # An unrecognised escape keeps BOTH characters. Emit has
                # no inverse for a bare kept character, so dropping the
                # backslash here would corrupt stored content — vendor
                # footprints carry Windows model paths ("C:\Users\…")
                # full of non-canonical escapes.
                out.append("\\" + nxt)
            i += 2
            continue
        if ch == '"':
            return Quoted("".join(out)), i + 1
        out.append(ch)
        i += 1
    raise SexprError("unterminated string")


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------


def emit(node: Node) -> str:
    """Render a node back to KiCad-flavoured s-expression text.

    Indentation is regenerated (two spaces per level) — the output is
    semantically identical to the input, not byte-identical.
    """
    return _render(node, 0)


def _render(node, indent: int) -> str:
    pad = "  " * indent
    if not isinstance(node, list):
        return pad + _emit_atom(node)
    if not any(isinstance(child, list) for child in node):
        return pad + "(" + " ".join(_emit_atom(child) for child in node) + ")"

    # Leading atoms (the head token and its scalar arguments) stay on the
    # opening line; everything from the first nested node onwards gets a
    # line of its own. That is the shape KiCad itself writes.
    split = 0
    while split < len(node) and not isinstance(node[split], list):
        split += 1
    head_atoms = " ".join(_emit_atom(child) for child in node[:split])

    opening = pad + "(" + head_atoms if head_atoms else pad + "("
    lines = [opening]
    for child in node[split:]:
        lines.append(_render(child, indent + 1))
    lines.append(pad + ")")
    return "\n".join(lines)


def _emit_atom(atom) -> str:
    text = str(atom)
    if isinstance(atom, Quoted) or _needs_quoting(text):
        return '"' + "".join(_ESCAPE.get(c, c) for c in text) + '"'
    return text


def _needs_quoting(text: str) -> bool:
    """An unquoted atom that contains a delimiter would not read back as
    one token, so quote it even though the source didn't."""
    return not text or any(c in _ATOM_END for c in text)


# ---------------------------------------------------------------------
# Library-entry helpers
# ---------------------------------------------------------------------


def head(node) -> str | None:
    """The node's head token (`"symbol"`, `"property"`, …), or None when
    the node is empty or starts with a nested node."""
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        return None
    return str(node[0])


def entry_name(node: Node) -> str:
    """The name of a `(symbol "NAME" …)` / `(footprint "NAME" …)` node —
    its first string argument."""
    if len(node) < 2 or not isinstance(node[1], str):
        raise SexprError(f"({head(node)} …) has no name argument")
    return str(node[1])


def entries(libtext: str) -> list[tuple[str, Node]]:
    """The symbol entries in `libtext`, as `(name, node)` pairs.

    Accepts either a whole `(kicad_symbol_lib …)` file — in which case
    every direct `(symbol …)` child is an entry — or a single bare
    `(symbol …)`. Unit sub-symbols are nested one level deeper and are
    therefore never mistaken for entries.
    """
    root = parse(libtext)
    root_token = head(root)
    if root_token == SYMBOL_LIB_ROOT:
        return [
            (entry_name(child), child)
            for child in root[1:]
            if isinstance(child, list) and head(child) == SYMBOL_ROOT
        ]
    if root_token == SYMBOL_ROOT:
        return [(entry_name(root), root)]
    raise SexprError(
        f"expected a ({SYMBOL_LIB_ROOT} …) or ({SYMBOL_ROOT} …) document, "
        f"got ({root_token} …)"
    )


def _with_name(node: Node, name: str) -> Node:
    return [node[0], Quoted(name), *node[2:]]


def rename(node: Node, new_name: str) -> Node:
    """Return a copy of `node` renamed to `new_name`.

    A symbol's graphical units are nested `(symbol "NAME_<unit>_<style>" …)`
    children whose names are derived from the parent's. KiCad matches them
    by that prefix, so renaming the parent without renaming the children
    leaves a symbol that draws as blank. Children that don't carry the
    prefix (there shouldn't be any) are left alone rather than guessed at.

    `(extends "PARENT")` is deliberately untouched — it names a *different*
    entry, not this one.
    """
    old_name = entry_name(node)
    renamed: Node = [node[0], Quoted(new_name)]
    for child in node[2:]:
        if (
            isinstance(child, list)
            and head(child) == SYMBOL_ROOT
            and len(child) > 1
            and isinstance(child[1], str)
            and str(child[1]).startswith(old_name + "_")
        ):
            suffix = str(child[1])[len(old_name):]
            renamed.append(_with_name(child, new_name + suffix))
        else:
            renamed.append(child)
    return renamed


# ---------------------------------------------------------------------
# Properties — `(property "Key" "Value" …)`
# ---------------------------------------------------------------------


def _is_property(child, key: str) -> bool:
    return (
        isinstance(child, list)
        and head(child) == _PROPERTY
        and len(child) >= 3
        and isinstance(child[1], str)
        and str(child[1]) == key
    )


def get_property(node: Node, key: str) -> str | None:
    """The value of the first `(property "key" "value" …)` child, or None."""
    for child in node:
        if _is_property(child, key) and isinstance(child[2], str):
            return str(child[2])
    return None


def set_property(node: Node, key: str, value: str) -> Node:
    """Return a copy of `node` with `key`'s property set to `value`.

    An existing property keeps its position and its trailing nodes (the
    `(at …)` placement and `(effects …)` styling), so rewriting a value
    doesn't move the field on the schematic. A new property is appended
    after the last existing one — KiCad reads them positionally for the
    mandatory four (Reference, Value, Footprint, Datasheet), so a new
    field must land after them, never before.
    """
    out: Node = []
    replaced = False
    last_property_index = -1
    for child in node:
        if _is_property(child, key) and not replaced:
            out.append([child[0], child[1], Quoted(value), *child[3:]])
            replaced = True
        else:
            out.append(child)
        if isinstance(child, list) and head(child) == _PROPERTY:
            last_property_index = len(out) - 1
    if not replaced:
        insert_at = last_property_index + 1 if last_property_index >= 0 else len(out)
        out.insert(insert_at, [_PROPERTY, Quoted(key), Quoted(value)])
    return out


# ---------------------------------------------------------------------
# 3D models — `(model "PATH" …)`
# ---------------------------------------------------------------------


def _is_model(child) -> bool:
    return (
        isinstance(child, list)
        and head(child) == _MODEL
        and len(child) >= 2
        and isinstance(child[1], str)
    )


def model_paths(node: Node) -> list[str]:
    """The 3D-model paths referenced by a footprint node.

    Direct children only. In `.kicad_mod` a `(model …)` is always a child
    of the footprint; recursing would risk matching an unrelated node
    that happens to share the token in some future format revision.
    """
    return [str(child[1]) for child in node if _is_model(child)]


def rewrite_model_paths(node: Node, fn: Callable[[str], str | None]) -> Node:
    """Return a copy of `node` with every model path passed through `fn`.

    Everything else in the `(model …)` node — `(offset …)`, `(scale …)`,
    `(rotate …)` — is carried over untouched, so re-pointing a model at
    our own storage never disturbs how it's placed on the board.

    `fn` returning None DROPS that `(model …)` node. The zip importer
    needs it: a vendor footprint references a 3D file by the vendor's own
    path, and one that wasn't in the archive would otherwise leave KiCad
    reporting a missing model on every board that places the footprint.
    """
    out: Node = []
    for child in node:
        if not _is_model(child):
            out.append(child)
            continue
        replacement = fn(str(child[1]))
        if replacement is None:
            continue
        out.append([child[0], Quoted(replacement), *child[2:]])
    return out
