# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Humbaba lexer: source text -> token stream."""

import re
from dataclasses import dataclass

KEYWORDS = {
    "type", "prompt", "fn", "let", "return", "if", "else",
    "uses", "budget", "gen", "from", "parallel", "for", "in", "limit",
    "policy", "untrusted", "true", "false", "system", "user",
    "retry", "fallback", "max",
    # v0.3
    "var", "while", "break", "continue", "and", "or", "not",
    "try", "capability", "durable", "step", "import", "as", "optional",
}

TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>//[^\n]*)
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op>->|==|!=|<=|>=|[{}()\[\]<>:,.=+\-*/%?!])
    """,
    re.VERBOSE,
)

ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


@dataclass
class Token:
    kind: str          # KW | IDENT | NUM | STR | OP | EOF
    value: object
    line: int
    col: int

    def __repr__(self):
        return f"{self.kind}({self.value!r})@{self.line}"


class LexError(Exception):
    pass


def unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append(ESCAPES.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def tokenize(src: str):
    tokens = []
    pos, line, line_start = 0, 1, 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise LexError(f"line {line}: unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        col = pos - line_start + 1

        if kind == "ws":
            nl = text.count("\n")
            if nl:
                line += nl
                line_start = pos + text.rfind("\n") + 1
        elif kind == "comment":
            pass
        elif kind == "num":
            tokens.append(Token("NUM", float(text) if "." in text else int(text), line, col))
        elif kind == "str":
            tokens.append(Token("STR", unescape(text[1:-1]), line, col))
        elif kind == "ident":
            tokens.append(Token("KW" if text in KEYWORDS else "IDENT", text, line, col))
        else:
            tokens.append(Token("OP", text, line, col))
        pos = m.end()

    tokens.append(Token("EOF", None, line, 0))
    return tokens
