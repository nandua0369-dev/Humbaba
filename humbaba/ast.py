# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Humbaba AST nodes."""

from dataclasses import dataclass, field
from typing import Any, Optional

# ---------- declarations ----------


@dataclass
class TypeRef:
    """A type expression: a name, optionally a list, optionally optional."""
    name: str
    is_list: bool = False
    optional: bool = False

    def __str__(self):
        s = f"[{self.name}]" if self.is_list else self.name
        return s + "?" if self.optional else s


@dataclass
class Field:
    name: str
    type: object          # TypeRef


@dataclass
class TypeDecl:
    name: str
    fields: list


@dataclass
class Param:
    name: str
    type: object          # TypeRef
    untrusted: bool = False


@dataclass
class CapabilityDecl:
    """User-defined capability. Makes the capability set open."""
    name: str
    line: int = 0


@dataclass
class ImportDecl:
    path: str
    alias: str = ""
    line: int = 0


@dataclass
class PromptDecl:
    name: str
    params: list
    system: str
    user: str


@dataclass
class FnDecl:
    name: str
    params: list
    uses: set
    budget: Optional[float]
    body: "Block"
    line: int = 0
    ret: object = None            # TypeRef or None
    durable: bool = False


# ---------- statements ----------


@dataclass
class Block:
    stmts: list


@dataclass
class Let:
    """Immutable binding. Cannot be reassigned."""
    name: str
    expr: Any
    line: int = 0
    mutable: bool = False         # `var` sets this


@dataclass
class Assign:
    """Rebinding an existing `var`. Rejected for `let` and across
    parallel boundaries — see §3.4 of LIMITATIONS."""
    name: str
    expr: Any
    line: int = 0


@dataclass
class While:
    cond: Any
    body: "Block"
    line: int = 0


@dataclass
class Break:
    line: int = 0


@dataclass
class Continue:
    line: int = 0


@dataclass
class Step:
    """Journaled unit of work inside a durable fn."""
    name: str
    body: "Block"
    line: int = 0


@dataclass
class Return:
    expr: Any


@dataclass
class If:
    cond: Any
    then: Block
    otherwise: Optional[Block]


@dataclass
class Policy:
    retry: int
    fallback: Optional[str]
    body: Block


@dataclass
class ExprStmt:
    expr: Any


# ---------- expressions ----------


@dataclass
class Literal:
    value: Any


@dataclass
class ListLit:
    items: list


@dataclass
class Ident:
    name: str
    line: int = 0


@dataclass
class Member:
    base: Any
    name: str
    line: int = 0


@dataclass
class Index:
    base: Any
    index: Any
    line: int = 0


@dataclass
class Call:
    callee: Any
    args: list           # list of (name|None, expr)
    line: int = 0


@dataclass
class Gen:
    type_name: str
    prompt_name: str
    args: list
    line: int = 0


@dataclass
class For:
    var: str
    iterable: Any
    body: "Block"
    line: int = 0


@dataclass
class ParallelFor:
    var: str
    iterable: Any
    body: Block
    limit: int = 4
    line: int = 0


@dataclass
class BinOp:
    op: str
    left: Any
    right: Any
    line: int = 0


@dataclass
class LogicOp:
    """`and` / `or`. Separate from BinOp because they short-circuit."""
    op: str
    left: Any
    right: Any
    line: int = 0


@dataclass
class UnaryOp:
    op: str               # "not" | "-"
    operand: Any
    line: int = 0


@dataclass
class Try:
    """Converts a failure into a value instead of terminating."""
    expr: Any
    line: int = 0


@dataclass
class RecordLit:
    """Nested record construction, needed once records can nest."""
    type_name: str
    fields: list          # [(name, expr)]
    line: int = 0
