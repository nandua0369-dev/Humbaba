# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Humbaba parser: tokens -> AST."""

from .lexer import tokenize
from . import ast as A


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0

    # ---------- token helpers ----------

    @property
    def cur(self):
        return self.toks[self.i]

    def at(self, kind, value=None):
        t = self.cur
        return t.kind == kind and (value is None or t.value == value)

    def at_any(self, kind, values):
        return self.cur.kind == kind and self.cur.value in values

    def eat(self, kind, value=None):
        if not self.at(kind, value):
            want = value or kind
            raise ParseError(
                f"line {self.cur.line}: expected {want!r}, found {self.cur.value!r}"
            )
        t = self.cur
        self.i += 1
        return t

    def accept(self, kind, value=None):
        if self.at(kind, value):
            self.i += 1
            return True
        return False

    # ---------- program ----------

    def parse(self):
        types, prompts, fns = {}, {}, {}
        self.caps, self.imports = set(), []
        while not self.at("EOF"):
            if self.at("KW", "capability"):
                self.i += 1
                self.caps.add(self.dotted_name())
                continue
            if self.at("KW", "import"):
                line = self.cur.line
                self.i += 1
                path = self.eat("STR").value
                alias = self.eat("IDENT").value if self.accept("KW", "as") else ""
                self.imports.append(A.ImportDecl(path, alias, line))
                continue
            if self.at("KW", "type"):
                d = self.type_decl()
                types[d.name] = d
            elif self.at("KW", "prompt"):
                d = self.prompt_decl()
                prompts[d.name] = d
            elif self.at("KW", "fn"):
                d = self.fn_decl()
                fns[d.name] = d
            elif self.at("KW", "durable"):
                self.i += 1
                d = self.fn_decl(durable=True)
                fns[d.name] = d
            else:
                raise ParseError(
                    f"line {self.cur.line}: expected type/prompt/fn/capability/import, "
                    f"found {self.cur.value!r}"
                )
        return types, prompts, fns

    def type_decl(self):
        self.eat("KW", "type")
        name = self.eat("IDENT").value
        self.eat("OP", "{")
        fields = []
        while not self.at("OP", "}"):
            fname = self.eat("IDENT").value
            self.eat("OP", ":")
            fields.append(A.Field(fname, self.type_ref()))
            self.accept("OP", ",")
        self.eat("OP", "}")
        return A.TypeDecl(name, fields)

    def type_ref(self):
        """name | [name] | name? | [name]?"""
        is_list = False
        if self.accept("OP", "["):
            is_list = True
        t = self.cur
        if t.kind not in ("IDENT", "KW"):
            raise ParseError(f"line {t.line}: expected a type name")
        self.i += 1
        if is_list:
            self.eat("OP", "]")
        optional = self.accept("OP", "?")
        return A.TypeRef(t.value, is_list, optional)

    def prompt_decl(self):
        self.eat("KW", "prompt")
        name = self.eat("IDENT").value
        params = self.params()
        self.eat("OP", "{")
        system = user = ""
        while not self.at("OP", "}"):
            label = self.cur.value
            self.i += 1
            self.eat("OP", ":")
            text = self.eat("STR").value
            if label == "system":
                system = text
            elif label == "user":
                user = text
            else:
                raise ParseError(f"unknown prompt section {label!r}")
            self.accept("OP", ",")
        self.eat("OP", "}")
        return A.PromptDecl(name, params, system, user)

    def params(self):
        self.eat("OP", "(")
        out = []
        while not self.at("OP", ")"):
            pname = self.eat("IDENT").value
            self.eat("OP", ":")
            untrusted = self.accept("KW", "untrusted")
            out.append(A.Param(pname, self.type_ref(), untrusted))
            self.accept("OP", ",")
        self.eat("OP", ")")
        return out

    def fn_decl(self, durable=False):
        line = self.cur.line
        self.eat("KW", "fn")
        name = self.eat("IDENT").value
        params = self.params()
        ret = self.type_ref() if self.accept("OP", "->") else None

        uses, budget = set(), None
        while self.at_any("KW", {"uses", "budget"}):
            if self.accept("KW", "uses"):
                self.eat("OP", "{")
                while not self.at("OP", "}"):
                    uses.add(self.dotted_name())
                    self.accept("OP", ",")
                self.eat("OP", "}")
            else:
                self.eat("KW", "budget")
                self.eat("OP", "{")
                self.eat("KW", "max")
                self.eat("OP", ":")
                budget = float(self.eat("NUM").value)
                self.accept("OP", ",")
                self.eat("OP", "}")

        return A.FnDecl(name, params, uses, budget, self.block(), line,
                        ret=ret, durable=durable)

    def dotted_name(self):
        parts = [self.cur.value]
        self.i += 1
        while self.accept("OP", "."):
            parts.append(self.cur.value)
            self.i += 1
        return ".".join(parts)

    # ---------- statements ----------

    def block(self):
        self.eat("OP", "{")
        stmts = []
        while not self.at("OP", "}"):
            stmts.append(self.statement())
        self.eat("OP", "}")
        return A.Block(stmts)

    def statement(self):
        if self.at("KW", "let") or self.at("KW", "var"):
            mutable = self.cur.value == "var"
            line = self.cur.line
            self.i += 1
            name = self.eat("IDENT").value
            self.eat("OP", "=")
            return A.Let(name, self.expr(), line, mutable)
        if self.at("KW", "while"):
            line = self.cur.line
            self.i += 1
            cond = self.expr()
            return A.While(cond, self.block(), line)
        if self.at("KW", "break"):
            line = self.cur.line
            self.i += 1
            return A.Break(line)
        if self.at("KW", "continue"):
            line = self.cur.line
            self.i += 1
            return A.Continue(line)
        if self.at("KW", "step"):
            line = self.cur.line
            self.i += 1
            name = self.cur.value if self.cur.kind == "STR" else ""
            if name:
                self.i += 1
            return A.Step(name, self.block(), line)
        # assignment: IDENT = expr  (only valid for `var`, checked later)
        if (self.cur.kind == "IDENT" and self.toks[self.i + 1].kind == "OP"
                and self.toks[self.i + 1].value == "="):
            line = self.cur.line
            name = self.cur.value
            self.i += 2
            return A.Assign(name, self.expr(), line)
        if self.at("KW", "return"):
            self.i += 1
            return A.Return(self.expr())
        if self.at("KW", "if"):
            self.i += 1
            cond = self.expr()
            then = self.block()
            otherwise = self.block() if self.accept("KW", "else") else None
            return A.If(cond, then, otherwise)
        if self.at("KW", "policy"):
            return self.policy_stmt()
        return A.ExprStmt(self.expr())

    def policy_stmt(self):
        self.eat("KW", "policy")
        self.eat("OP", "{")
        retry, fallback = 0, None
        while not self.at("OP", "}"):
            key = self.cur.value
            self.i += 1
            self.eat("OP", ":")
            if key == "retry":
                retry = int(self.eat("NUM").value)
            elif key == "fallback":
                fallback = self.eat("STR").value
            else:
                raise ParseError(f"unknown policy key {key!r}")
            self.accept("OP", ",")
        self.eat("OP", "}")
        return A.Policy(retry, fallback, self.block())

    # ---------- expressions ----------

    def expr(self):
        return self.logic_or()

    def logic_or(self):
        left = self.logic_and()
        while self.at("KW", "or"):
            line = self.cur.line
            self.i += 1
            left = A.LogicOp("or", left, self.logic_and(), line)
        return left

    def logic_and(self):
        left = self.logic_not()
        while self.at("KW", "and"):
            line = self.cur.line
            self.i += 1
            left = A.LogicOp("and", left, self.logic_not(), line)
        return left

    def logic_not(self):
        if self.at("KW", "not") or self.at("OP", "!"):
            line = self.cur.line
            self.i += 1
            return A.UnaryOp("not", self.logic_not(), line)
        return self.comparison()

    def comparison(self):
        left = self.additive()
        while self.at_any("OP", {"==", "!=", "<", ">", "<=", ">="}):
            op = self.cur.value
            line = self.cur.line
            self.i += 1
            left = A.BinOp(op, left, self.additive(), line)
        return left

    def additive(self):
        left = self.multiplicative()
        while self.at_any("OP", {"+", "-"}):
            op = self.cur.value
            line = self.cur.line
            self.i += 1
            left = A.BinOp(op, left, self.multiplicative(), line)
        return left

    def multiplicative(self):
        left = self.unary()
        while self.at_any("OP", {"*", "/", "%"}):
            op = self.cur.value
            line = self.cur.line
            self.i += 1
            left = A.BinOp(op, left, self.unary(), line)
        return left

    def unary(self):
        if self.at("OP", "-"):
            line = self.cur.line
            self.i += 1
            return A.UnaryOp("-", self.unary(), line)
        return self.postfix()

    def _dead_multiplicative(self):
        left = self.postfix()
        while self.at_any("OP", {"*", "/", "%"}):
            op = self.cur.value
            line = self.cur.line
            self.i += 1
            left = A.BinOp(op, left, self.postfix(), line)
        return left

    def postfix(self):
        node = self.primary()
        while True:
            if self.at("OP", "."):
                line = self.cur.line
                self.i += 1
                name = self.cur.value
                self.i += 1
                node = A.Member(node, name, line)
            elif self.at("OP", "["):
                line = self.cur.line
                self.i += 1
                idx = self.expr()
                self.eat("OP", "]")
                node = A.Index(node, idx, line)
            elif self.at("OP", "("):
                node = A.Call(node, self.args(), self.cur.line)
            else:
                return node

    def args(self):
        self.eat("OP", "(")
        out = []
        while not self.at("OP", ")"):
            # named argument?  name: expr
            if self.cur.kind in ("IDENT", "KW") and self.toks[self.i + 1].value == ":":
                name = self.cur.value
                self.i += 2
                out.append((name, self.expr()))
            else:
                out.append((None, self.expr()))
            self.accept("OP", ",")
        self.eat("OP", ")")
        return out

    def primary(self):
        t = self.cur

        if t.kind == "NUM" or t.kind == "STR":
            self.i += 1
            return A.Literal(t.value)
        if t.kind == "KW" and t.value in ("true", "false"):
            self.i += 1
            return A.Literal(t.value == "true")

        if t.kind == "KW" and t.value == "step":
            self.i += 1
            name = ""
            if self.cur.kind == "STR":
                name = self.cur.value
                self.i += 1
            return A.Step(name, self.block(), t.line)

        if t.kind == "KW" and t.value == "try":
            self.i += 1
            return A.Try(self.expr(), t.line)

        if t.kind == "KW" and t.value == "gen":
            self.i += 1
            self.eat("OP", "<")
            type_name = self.type_ref().name        # gen<T>: always a record name
            self.eat("OP", ">")
            self.eat("KW", "from")
            prompt_name = self.eat("IDENT").value
            return A.Gen(type_name, prompt_name, self.args(), t.line)

        if t.kind == "KW" and t.value == "for":
            self.i += 1
            var = self.eat("IDENT").value
            self.eat("KW", "in")
            iterable = self.expr()
            return A.For(var, iterable, self.block(), t.line)

        if t.kind == "KW" and t.value == "parallel":
            self.i += 1
            self.eat("KW", "for")
            var = self.eat("IDENT").value
            self.eat("KW", "in")
            iterable = self.expr()
            body = self.block()
            limit = 4
            if self.accept("KW", "limit"):
                limit = int(self.eat("NUM").value)
            return A.ParallelFor(var, iterable, body, limit, t.line)

        if t.kind == "OP" and t.value == "[":
            self.i += 1
            items = []
            while not self.at("OP", "]"):
                items.append(self.expr())
                self.accept("OP", ",")
            self.eat("OP", "]")
            return A.ListLit(items)

        if t.kind == "OP" and t.value == "(":
            self.i += 1
            e = self.expr()
            self.eat("OP", ")")
            return e

        # record literal:  TypeName { field: expr, ... }
        if (t.kind == "IDENT" and t.value[:1].isupper()
                and self.toks[self.i + 1].kind == "OP"
                and self.toks[self.i + 1].value == "{"):
            self.i += 2
            fields = []
            while not self.at("OP", "}"):
                fname = self.cur.value
                self.i += 1
                self.eat("OP", ":")
                fields.append((fname, self.expr()))
                self.accept("OP", ",")
            self.eat("OP", "}")
            return A.RecordLit(t.value, fields, t.line)

        if t.kind in ("IDENT", "KW"):
            self.i += 1
            return A.Ident(t.value, t.line)

        raise ParseError(f"line {t.line}: unexpected {t.value!r}")


def parse(src):
    p = Parser(src)
    types, prompts, fns = p.parse()
    return types, prompts, fns


def parse_file(path, _seen=None):
    """Parse a file and everything it imports.

    Solves LIMITATIONS §2.3. Imports are resolved relative to the importing
    file. Cycles are detected; a duplicate declaration across modules is an
    error rather than a silent overwrite.
    """
    import os
    _seen = _seen if _seen is not None else set()
    path = os.path.abspath(path)
    if path in _seen:
        return {}, {}, {}, set()
    _seen.add(path)

    with open(path) as f:
        src = f.read()
    p = Parser(src)
    types, prompts, fns = p.parse()
    caps = set(p.caps)

    base = os.path.dirname(path)
    for imp in p.imports:
        child = imp.path if os.path.isabs(imp.path) else os.path.join(base, imp.path)
        if not os.path.exists(child) and not child.endswith(".hb"):
            child += ".hb"
        ct, cp, cf, cc = parse_file(child, _seen)
        for name, d in ct.items():
            if name in types:
                raise ParseError(f"{path}: type {name!r} already defined")
            types[name] = d
        for name, d in cp.items():
            if name in prompts:
                raise ParseError(f"{path}: prompt {name!r} already defined")
            prompts[name] = d
        for name, d in cf.items():
            if name in fns:
                raise ParseError(f"{path}: fn {name!r} already defined")
            fns[name] = d
        caps |= cc

    return types, prompts, fns, caps
