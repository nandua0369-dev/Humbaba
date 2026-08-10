# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Humbaba static checker.

One pass over the AST before anything runs. Catches at compile time what v0.2
only caught at runtime — after money had been spent.

Solves, from docs/LIMITATIONS.md:
  §3.3  no static type checking
  §3.4  assignment vs concurrency safety (mutable capture across `parallel`)
  §4.4  taint propagation beyond the prompt boundary
  §2.2  durable side-effect confinement
  §3.2  user-defined capabilities are validated against declarations
"""

from . import ast as A

BUILTIN_TYPES = {"string", "number", "bool"}
BUILTIN_FNS = {"print": None, "len": "number"}
BUILTIN_CAPS = {"model", "web.search", "db.dump"}


class CheckError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(errors))


class Binding:
    __slots__ = ("type", "mutable", "tainted", "depth")

    def __init__(self, type_, mutable=False, tainted=False, depth=0):
        self.type = type_
        self.mutable = mutable
        self.tainted = tainted
        self.depth = depth


class Scope:
    def __init__(self, parent=None, fn_depth=0):
        self.names = {}
        self.parent = parent
        self.fn_depth = fn_depth

    def declare(self, name, binding):
        self.names[name] = binding

    def lookup(self, name):
        s = self
        while s:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None


def tname(t):
    """TypeRef -> comparable string. Tolerates plain strings for old ASTs."""
    if t is None:
        return None
    if isinstance(t, str):
        return t
    return str(t)


class Checker:
    def __init__(self, types, prompts, fns, caps=None):
        self.types = types
        self.prompts = prompts
        self.fns = fns
        self.declared_caps = BUILTIN_CAPS | set(caps or ())
        self.errors = []

    def err(self, line, msg):
        self.errors.append(f"line {line}: {msg}")

    # ---------------------------------------------------------------- entry

    def check(self):
        for td in self.types.values():
            self.check_type(td)
        for pd in self.prompts.values():
            self.check_prompt(pd)
        for fd in self.fns.values():
            self.check_fn(fd)
        if self.errors:
            raise CheckError(self.errors)
        return True

    def check_type(self, td):
        seen = set()
        for f in td.fields:
            if f.name in seen:
                self.err(0, f"type {td.name}: duplicate field {f.name!r}")
            seen.add(f.name)
            base = f.type.name if not isinstance(f.type, str) else f.type
            if base not in BUILTIN_TYPES and base not in self.types:
                self.err(0, f"type {td.name}: field {f.name!r} has unknown type {base!r}")
            elif base == td.name:
                self.err(0, f"type {td.name}: field {f.name!r} is directly recursive")

    def check_prompt(self, pd):
        names = {p.name for p in pd.params}
        for section, text in (("system", pd.system), ("user", pd.user)):
            i = 0
            while i < len(text):
                if text[i] == "{":
                    j = text.find("}", i)
                    if j > 0:
                        ref = text[i + 1:j]
                        if ref and ref not in names:
                            self.err(0, f"prompt {pd.name}: {section} references "
                                        f"{{{ref}}} but has no such parameter")
                        i = j
                i += 1

    # ---------------------------------------------------------------- fns

    def check_fn(self, fd):
        scope = Scope(fn_depth=0)
        for p in fd.params:
            base = p.type.name if not isinstance(p.type, str) else p.type
            if base not in BUILTIN_TYPES and base not in self.types:
                self.err(fd.line, f"fn {fd.name}: parameter {p.name!r} has "
                                  f"unknown type {base!r}")
            scope.declare(p.name, Binding(tname(p.type), False, p.untrusted))

        for cap in fd.uses:
            if cap not in self.declared_caps:
                self.err(fd.line, f"fn {fd.name}: undeclared capability {cap!r}. "
                                  f"Add `capability {cap}` at the top level.")

        ctx = {
            "fn": fd,
            "loop_depth": 0,
            "par_depth": 0,
            "in_step": False,
        }
        self.block(fd.body, scope, ctx)

    def block(self, b, scope, ctx):
        inner = Scope(scope, scope.fn_depth)
        for st in b.stmts:
            self.stmt(st, inner, ctx)

    def stmt(self, n, scope, ctx):
        cls = n.__class__

        if cls is A.Let:
            t, tainted = self.expr(n.expr, scope, ctx)
            if scope.lookup(n.name) and n.name in scope.names:
                self.err(n.line, f"{n.name!r} is already bound in this scope")
            scope.declare(n.name, Binding(t, n.mutable, tainted, scope.fn_depth))
            return

        if cls is A.Assign:
            b = scope.lookup(n.name)
            if b is None:
                self.err(n.line, f"cannot assign to undefined name {n.name!r}")
                return
            if not b.mutable:
                self.err(n.line,
                         f"cannot assign to {n.name!r}: it was bound with `let`. "
                         f"Use `var {n.name} = ...` to make it mutable.")
                return
            # §3.4: mutable capture across a parallel boundary is the exact
            # race the language previously prevented by having no assignment.
            if ctx["par_depth"] > 0 and b.depth < scope.fn_depth:
                self.err(n.line,
                         f"cannot assign to {n.name!r} inside `parallel for`: it "
                         f"is declared outside the block, so iterations would "
                         f"race. Collect results from the block instead.")
            t, tainted = self.expr(n.expr, scope, ctx)
            if b.type and t and b.type != t:
                self.err(n.line, f"cannot assign {t} to {n.name!r} of type {b.type}")
            b.tainted = b.tainted or tainted
            return

        if cls is A.ExprStmt:
            self.expr(n.expr, scope, ctx)
            return

        if cls is A.Return:
            t, _ = self.expr(n.expr, scope, ctx)
            want = tname(ctx["fn"].ret)
            if want and t and want != t:
                self.err(getattr(n, "line", ctx["fn"].line),
                         f"fn {ctx['fn'].name} declares -> {want} but returns {t}")
            return

        if cls is A.If:
            self.expr(n.cond, scope, ctx)
            self.block(n.then, scope, ctx)
            if n.otherwise:
                self.block(n.otherwise, scope, ctx)
            return

        if cls is A.While:
            self.expr(n.cond, scope, ctx)
            ctx["loop_depth"] += 1
            self.block(n.body, scope, ctx)
            ctx["loop_depth"] -= 1
            return

        if cls in (A.Break, A.Continue):
            if ctx["loop_depth"] == 0:
                word = "break" if cls is A.Break else "continue"
                self.err(n.line, f"`{word}` outside a loop")
            return

        if cls is A.Policy:
            self.block(n.body, scope, ctx)
            return

        if cls is A.Step:
            if not ctx["fn"].durable:
                self.err(n.line, "`step` is only valid inside a `durable fn`")
            was, ctx["in_step"] = ctx["in_step"], True
            self.block(n.body, scope, ctx)
            ctx["in_step"] = was
            return

        self.err(getattr(n, "line", 0), f"unhandled statement {cls.__name__}")

    # ---------------------------------------------------------------- exprs
    # Every expr returns (type_name_or_None, tainted).

    def expr(self, n, scope, ctx):
        cls = n.__class__

        if cls is A.Literal:
            v = n.value
            if isinstance(v, bool):
                return "bool", False
            if isinstance(v, (int, float)):
                return "number", False
            return "string", False

        if cls is A.ListLit:
            tainted = False
            elem = None
            for x in n.items:
                t, tt = self.expr(x, scope, ctx)
                tainted = tainted or tt
                elem = elem or t
            return (f"[{elem}]" if elem else None), tainted

        if cls is A.Ident:
            b = scope.lookup(n.name)
            if b is None:
                if n.name in self.fns or n.name in BUILTIN_FNS:
                    return None, False
                self.err(n.line, f"undefined name {n.name!r}")
                return None, False
            return b.type, b.tainted

        if cls is A.UnaryOp:
            t, tainted = self.expr(n.operand, scope, ctx)
            if n.op == "-" and t and t != "number":
                self.err(n.line, f"unary minus needs a number, got {t}")
                return "number", tainted
            return ("number" if n.op == "-" else "bool"), tainted

        if cls is A.LogicOp:
            _, a = self.expr(n.left, scope, ctx)
            _, b = self.expr(n.right, scope, ctx)
            return "bool", a or b

        if cls is A.BinOp:
            return self.binop(n, scope, ctx)

        if cls is A.Member:
            return self.member(n, scope, ctx)

        if cls is A.Index:
            bt, tainted = self.expr(n.base, scope, ctx)
            it, _ = self.expr(n.index, scope, ctx)
            if it and it != "number":
                self.err(n.line, f"list index must be a number, got {it}")
            if bt and not bt.startswith("["):
                self.err(n.line, f"cannot index {bt}")
                return None, tainted
            return (bt[1:-1] if bt else None), tainted

        if cls is A.Call:
            return self.call(n, scope, ctx)

        if cls is A.Gen:
            return self.gen(n, scope, ctx)

        if cls is A.Try:
            t, tainted = self.expr(n.expr, scope, ctx)
            return t, tainted

        if cls is A.Step:
            if not ctx["fn"].durable:
                self.err(n.line, "`step` is only valid inside a `durable fn`")
            was, ctx["in_step"] = ctx["in_step"], True
            inner = Scope(scope, scope.fn_depth)
            last = (None, False)
            for st in n.body.stmts:
                if st.__class__ is A.ExprStmt:
                    last = self.expr(st.expr, inner, ctx)
                else:
                    self.stmt(st, inner, ctx)
            ctx["in_step"] = was
            return last

        if cls is A.RecordLit:
            return self.record_lit(n, scope, ctx)

        if cls in (A.For, A.ParallelFor):
            return self.loop_expr(n, scope, ctx)

        self.err(getattr(n, "line", 0), f"unhandled expression {cls.__name__}")
        return None, False

    def binop(self, n, scope, ctx):
        lt, la = self.expr(n.left, scope, ctx)
        rt, ra = self.expr(n.right, scope, ctx)
        tainted = la or ra
        if n.op in ("==", "!=", "<", ">", "<=", ">="):
            if lt and rt and lt != rt:
                self.err(n.line, f"cannot compare {lt} with {rt}")
            return "bool", tainted
        # + is overloaded: string+string -> string, number+number -> number
        if n.op == "+":
            if lt == "string" or rt == "string":
                # Allow string + string or string + unknown
                if lt and lt != "string" or rt and rt != "string":
                    self.err(n.line, f"cannot concatenate string with {lt or rt}")
                return "string", tainted
            if lt and lt != "number" or rt and rt != "number":
                got = lt if (lt and lt != "number") else rt
                self.err(n.line, f"operator '+' needs numbers, got {got}")
            return "number", tainted
        if lt and lt != "number" or rt and rt != "number":
            got = lt if (lt and lt != "number") else rt
            self.err(n.line, f"operator {n.op!r} needs numbers, got {got}")
        return "number", tainted

    def member(self, n, scope, ctx):
        base = n.base
        if (base.__class__ is A.Ident and scope.lookup(base.name) is None
                and base.name in {"web", "db", "model"}):
            full = f"{base.name}.{n.name}"
            if full not in self.declared_caps:
                self.err(n.line, f"unknown capability {full!r}")
            return None, False
        bt, tainted = self.expr(base, scope, ctx)
        if bt is None:
            return None, tainted
        if bt.startswith("["):
            self.err(n.line, f"cannot read .{n.name} on a list")
            return None, tainted
        clean = bt.rstrip("?")
        td = self.types.get(clean)
        if td is None:
            self.err(n.line, f"cannot read .{n.name} on {bt}")
            return None, tainted
        for f in td.fields:
            if f.name == n.name:
                return tname(f.type), tainted
        self.err(n.line, f"type {clean} has no field {n.name!r}")
        return None, tainted

    def call(self, n, scope, ctx):
        callee = n.callee
        if callee.__class__ is A.Ident and scope.lookup(callee.name) is None:
            name = callee.name
            if name in BUILTIN_FNS:
                for _, e in n.args:
                    self.expr(e, scope, ctx)
                return BUILTIN_FNS[name], False
            fn = self.fns.get(name)
            if fn:
                return self.user_call(fn, n, scope, ctx)

        # capability call
        t, _ = self.expr(callee, scope, ctx)
        tainted = False
        for _, e in n.args:
            _, a = self.expr(e, scope, ctx)
            tainted = tainted or a
        if callee.__class__ is A.Member:
            full = f"{callee.base.name}.{callee.name}" \
                if callee.base.__class__ is A.Ident else None
            if full:
                # §4.4: tainted data must not reach a capability call
                if tainted:
                    self.err(n.line,
                             f"tainted value passed to {full!r}. It derives from "
                             f"`untrusted` input; launder it explicitly first.")
                if full not in ctx["fn"].uses:
                    self.err(n.line,
                             f"fn {ctx['fn'].name} calls {full!r} but does not "
                             f"declare it in `uses`")
                if ctx["fn"].durable and not ctx["in_step"]:
                    self.err(n.line,
                             f"side effect {full!r} outside a `step` in durable fn "
                             f"{ctx['fn'].name}: replay would repeat it")
        return None, tainted

    def user_call(self, fn, n, scope, ctx):
        if len(n.args) != len(fn.params):
            self.err(n.line, f"fn {fn.name} takes {len(fn.params)} argument(s), "
                             f"{len(n.args)} given")
        tainted = False
        for i, (_, e) in enumerate(n.args):
            t, a = self.expr(e, scope, ctx)
            tainted = tainted or a
            if i < len(fn.params):
                want = tname(fn.params[i].type)
                if want and t and want != t:
                    self.err(n.line, f"fn {fn.name}: argument {i + 1} expects "
                                     f"{want}, got {t}")
        missing = set(fn.uses) - set(ctx["fn"].uses)
        if missing:
            self.err(n.line, f"fn {ctx['fn'].name} cannot call {fn.name}: it "
                             f"requires {sorted(missing)} which "
                             f"{ctx['fn'].name} does not hold")
        if ctx["fn"].durable and fn.uses and not ctx["in_step"]:
            self.err(n.line, f"call to {fn.name} (which has side effects) outside "
                             f"a `step` in durable fn {ctx['fn'].name}")
        return tname(fn.ret), tainted

    def gen(self, n, scope, ctx):
        if "model" not in ctx["fn"].uses:
            self.err(n.line, f"fn {ctx['fn'].name} uses gen<> but does not "
                             f"declare `model` in `uses`")
        if ctx["fn"].durable and not ctx["in_step"]:
            self.err(n.line, f"gen<> outside a `step` in durable fn "
                             f"{ctx['fn'].name}: it is non-deterministic, so its "
                             f"result must be journaled")
        if n.type_name not in self.types:
            self.err(n.line, f"unknown type {n.type_name!r}")
        pr = self.prompts.get(n.prompt_name)
        if pr is None:
            self.err(n.line, f"unknown prompt {n.prompt_name!r}")
            return n.type_name, False
        supplied = set()
        for i, (name, e) in enumerate(n.args):
            key = name or (pr.params[i].name if i < len(pr.params) else None)
            supplied.add(key)
            t, _ = self.expr(e, scope, ctx)
            for p in pr.params:
                if p.name == key:
                    want = p.type.name if not isinstance(p.type, str) else p.type
                    if want and t and want != t.rstrip("?"):
                        self.err(n.line, f"prompt {pr.name}: parameter {key!r} "
                                         f"expects {want}, got {t}")
        for p in pr.params:
            if p.name not in supplied:
                self.err(n.line, f"prompt {pr.name}: missing argument {p.name!r}")
        # A model's output derived from untrusted input stays untrusted.
        return n.type_name, any(p.untrusted for p in pr.params)

    def record_lit(self, n, scope, ctx):
        td = self.types.get(n.type_name)
        if td is None:
            self.err(n.line, f"unknown type {n.type_name!r}")
            return None, False
        given = {}
        tainted = False
        for fname, e in n.fields:
            t, a = self.expr(e, scope, ctx)
            tainted = tainted or a
            given[fname] = t
        for f in td.fields:
            ft = tname(f.type)
            if f.name not in given:
                if not (hasattr(f.type, "optional") and f.type.optional):
                    self.err(n.line, f"{n.type_name}: missing field {f.name!r}")
            elif given[f.name] and ft and given[f.name] != ft.rstrip("?"):
                self.err(n.line, f"{n.type_name}: field {f.name!r} expects "
                                 f"{ft}, got {given[f.name]}")
        for k in given:
            if not any(f.name == k for f in td.fields):
                self.err(n.line, f"{n.type_name} has no field {k!r}")
        return n.type_name, tainted

    def loop_expr(self, n, scope, ctx):
        it, tainted = self.expr(n.iterable, scope, ctx)
        elem = it[1:-1] if it and it.startswith("[") else None
        inner = Scope(scope, scope.fn_depth + 1)
        inner.declare(n.var, Binding(elem, False, tainted, inner.fn_depth))
        if n.__class__ is A.ParallelFor:
            ctx["par_depth"] += 1
            self.block(n.body, inner, ctx)
            ctx["par_depth"] -= 1
        else:
            self.block(n.body, inner, ctx)
        return None, tainted


def check(types, prompts, fns, caps=None):
    return Checker(types, prompts, fns, caps).check()
