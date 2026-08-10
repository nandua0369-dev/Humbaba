# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""HBX — compile a Humbaba program to the enforcement-carrying format.

The format is specified in docs/HBX.md. The short version: a stack machine
whose instruction set includes REQUIRE, CHARGE, FENCE, TAINT and GEN, so that
capability attenuation, taint propagation and budget limits are properties of
the artefact rather than of the front end that produced it.

Blocks that need their own frame — parallel bodies, durable steps, try
bodies — are lifted into synthetic functions so the instruction stream stays
flat. A host therefore needs no structured control stack, only a call stack.
"""

from . import ast as A

MAGIC = "HBX 2"


class CompileError(Exception):
    pass


# --------------------------------------------------------------- constants


class ConstPool:
    """Interned constants. Identity is (kind, value) so 1 and "1" differ."""

    def __init__(self):
        self._items = []
        self._index = {}

    def add(self, kind, value):
        key = (kind, value)
        if key not in self._index:
            self._index[key] = len(self._items)
            self._items.append((kind, value))
        return self._index[key]

    def number(self, v):
        return self.add("N", float(v))

    def string(self, v):
        return self.add("S", str(v))

    def boolean(self, v):
        return self.add("B", bool(v))

    def nil(self):
        return self.add("Z", None)

    def __len__(self):
        return len(self._items)

    def emit(self):
        out = []
        for kind, value in self._items:
            if kind == "N":
                out.append(f"N {value!r}")
            elif kind == "S":
                out.append("S " + _escape(value))
            elif kind == "B":
                out.append(f"B {1 if value else 0}")
            else:
                out.append("Z")
        return out


def _escape(s):
    return (s.replace("\\", "\\\\")
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t"))


def unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t",
                        "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ------------------------------------------------------------- function ctx


class FnCtx:
    """Accumulates instructions and local slots for one function."""

    def __init__(self, name, params, caps, budget, taint, durable):
        self.name = name
        self.params = params
        self.caps = caps
        self.budget = budget
        self.taint = taint
        self.durable = durable
        self.code = []
        self.slots = {}
        self.nslots = 0
        self.loops = []          # (continue_target, [break_patch_sites])
        for p in params:
            self.slot(p)

    def slot(self, name):
        if name not in self.slots:
            self.slots[name] = self.nslots
            self.nslots += 1
        return self.slots[name]

    def has(self, name):
        return name in self.slots

    def emit(self, op, *args):
        self.code.append((op,) + tuple(args))
        return len(self.code) - 1

    def here(self):
        return len(self.code)

    def patch(self, site, target):
        op = self.code[site]
        self.code[site] = (op[0], target) + op[2:]

    def max_stack(self):
        """Conservative peak depth, so a host can allocate the stack once."""
        depth = peak = 0
        for ins in self.code:
            depth += _STACK_EFFECT.get(ins[0], _effect_dynamic)(ins) \
                if ins[0] in _STACK_EFFECT or True else 0
            if depth < 0:
                depth = 0
            peak = max(peak, depth)
        return peak + 4          # headroom for enforcement temporaries


def _effect_dynamic(ins):
    return 0


# Stack effect per opcode. Callables take the instruction tuple.
_STACK_EFFECT = {
    "PUSHK": lambda i: 1, "LOAD": lambda i: 1, "STORE": lambda i: -1,
    "POP": lambda i: -1, "DUP": lambda i: 1,
    "ADD": lambda i: -1, "SUB": lambda i: -1, "MUL": lambda i: -1,
    "DIV": lambda i: -1, "MOD": lambda i: -1,
    "NEG": lambda i: 0, "NOT": lambda i: 0,
    "LT": lambda i: -1, "GT": lambda i: -1, "LE": lambda i: -1,
    "GE": lambda i: -1, "EQ": lambda i: -1, "NE": lambda i: -1,
    "JMP": lambda i: 0, "JZ": lambda i: -1, "JNZ": lambda i: -1,
    "LIST": lambda i: 1 - i[1], "INDEX": lambda i: -1, "LEN": lambda i: 0,
    "APPEND": lambda i: -1,
    "RECORD": lambda i: 1 - i[2], "FIELD": lambda i: 0,
    "CALL": lambda i: 1 - i[2], "RET": lambda i: -1, "RETNIL": lambda i: 0,
    "REQUIRE": lambda i: 0, "CHARGE": lambda i: -1,
    "RESERVE": lambda i: -1, "RELEASE": lambda i: -1,
    "FENCE": lambda i: 0, "TAINT": lambda i: 0, "UNTAINT": lambda i: 0,
    "GEN": lambda i: 1 - i[3],
    "PARALLEL": lambda i: -i[3], "STEP": lambda i: 1 - i[3],
    "TRY": lambda i: 1 - i[2],
}


# ---------------------------------------------------------------- compiler


class Compiler:
    def __init__(self, types, prompts, fns, capabilities=()):
        # parse() returns dicts keyed by name; tests may pass sequences.
        types = list(types.values()) if isinstance(types, dict) else list(types)
        prompts = list(prompts.values()) if isinstance(prompts, dict) else list(prompts)
        fns = list(fns.values()) if isinstance(fns, dict) else list(fns)

        self.K = ConstPool()
        self.types = {t.name: t for t in types}
        self.type_order = [t.name for t in types]
        self.type_index = {n: i for i, n in enumerate(self.type_order)}
        self.prompts = {p.name: p for p in prompts}
        self.prompt_order = [p.name for p in prompts]
        self.prompt_index = {n: i for i, n in enumerate(self.prompt_order)}
        self.fns = {f.name: f for f in fns}
        self.fn_order = []
        self.fn_index = {}
        self.caps = []
        self.cap_index = {}
        for c in capabilities:
            self.cap(c)
        for c in ("model",):
            self.cap(c)
        self.out = []            # compiled FnCtx list
        self._synth = 0
        # policy is lexically scoped in the interpreter: a `policy` block
        # affects the gen calls written inside it, not the functions those
        # calls invoke. So the compiler can resolve it statically and attach
        # retry/fallback to each GEN, and no host needs a policy stack.
        self._policy = [(0, None)]

    def cap(self, name):
        if name not in self.cap_index:
            self.cap_index[name] = len(self.caps)
            self.caps.append(name)
        return self.cap_index[name]

    # -- entry ------------------------------------------------------------

    def compile(self):
        # Intern prompt text first. serialise() emits the constant pool before
        # the prompt table, so anything interned during serialisation would be
        # written with an index past the end of the pool.
        self._prompt_consts = {}
        for name in self.prompt_order:
            pr = self.prompts[name]
            self._prompt_consts[name] = (
                self.K.string(getattr(pr, "system", "") or ""),
                self.K.string(getattr(pr, "user", "") or ""),
            )
        for name in self.fns:
            self._reserve(name)
        for name in list(self.fn_order):
            if name in self.fns:
                self._compile_fn(self.fns[name])
        return self

    def _reserve(self, name):
        if name not in self.fn_index:
            self.fn_index[name] = len(self.fn_order)
            self.fn_order.append(name)
        return self.fn_index[name]

    def _synth_name(self, kind):
        self._synth += 1
        return f"${kind}{self._synth}"

    # -- functions --------------------------------------------------------

    def _compile_fn(self, fn):
        caps = sorted(self.cap(c) for c in getattr(fn, "uses", ()) or ())
        budget = None
        if getattr(fn, "budget", None) is not None:
            budget = getattr(fn.budget, "max", None) \
                if not isinstance(fn.budget, (int, float)) else fn.budget
        taint = [i for i, p in enumerate(fn.params)
                 if getattr(p, "untrusted", False)]
        ctx = FnCtx(fn.name, [p.name for p in fn.params], caps, budget,
                    taint, 1 if getattr(fn, "durable", False) else 0)
        self._slot_for(ctx, fn.name)
        self._block(ctx, fn.body)
        if not ctx.code or ctx.code[-1][0] not in ("RET", "RETNIL"):
            ctx.emit("RETNIL")
        self.out.append((self.fn_index[fn.name], ctx))
        return ctx

    def _slot_for(self, ctx, _name):
        return ctx

    def _free_vars(self, node, bound, found):
        """Collect names a lifted body reads but does not itself bind.

        Lifted bodies become separate functions, so anything they reference
        from the enclosing scope has to be passed in explicitly. Without this,
        `step "x" { use(doc) }` compiles to a function that has never heard
        of `doc`.
        """
        if node is None:
            return
        if isinstance(node, list):
            for n in node:
                self._free_vars(n, bound, found)
            return
        if isinstance(node, tuple):
            self._free_vars(node[1] if len(node) > 1 else None, bound, found)
            return
        if isinstance(node, A.Block):
            self._free_vars(node.stmts, bound, found)
            return
        if isinstance(node, A.Ident):
            if node.name not in bound and node.name not in found:
                found.append(node.name)
            return
        if isinstance(node, A.Let):
            self._free_vars(node.expr, bound, found)
            bound.add(node.name)
            return
        if isinstance(node, A.Assign):
            self._free_vars(node.expr, bound, found)
            if node.name not in bound and node.name not in found:
                found.append(node.name)
            return
        if isinstance(node, (A.For, A.ParallelFor)):
            self._free_vars(node.iterable, bound, found)
            inner = set(bound) | {node.var}
            self._free_vars(node.body, inner, found)
            return
        if isinstance(node, A.Member):
            self._free_vars(node.base, bound, found)
            return
        if isinstance(node, A.Call):
            # The callee of db.write(...) is a capability name, not a variable.
            if not isinstance(node.callee, (A.Ident, A.Member)):
                self._free_vars(node.callee, bound, found)
            elif isinstance(node.callee, A.Ident) and \
                    node.callee.name in bound:
                found.append(node.callee.name) if False else None
            self._free_vars(node.args, bound, found)
            return
        import dataclasses
        if dataclasses.is_dataclass(node):
            for f in dataclasses.fields(node):
                if f.name in ("line", "name", "op", "type_name",
                              "prompt_name", "var"):
                    continue
                self._free_vars(getattr(node, f.name), bound, found)

    def _lift(self, kind, body, params=(), caps=None, budget=None,
              value=False, enclosing=None):
        """Compile a block into a synthetic function; return its index.

        `value=True` marks a comprehension body, whose final expression is the
        result rather than a discarded statement. Without this a `for` used as
        an expression yields a list of nils.
        """
        name = self._synth_name(kind)
        idx = self._reserve(name)
        stmts = list(body.stmts if isinstance(body, A.Block) else body)
        if value and stmts and isinstance(stmts[-1], A.ExprStmt):
            stmts[-1] = A.Return(stmts[-1].expr)

        # Anything the body reads from the enclosing scope becomes a
        # parameter, appended after the body's own parameters.
        found = []
        self._free_vars(stmts, set(params) | {"print", "len"}, found)
        captures = [n for n in found if n in (enclosing.slots if enclosing else {})]
        ctx = FnCtx(name, list(params) + captures, caps or [], budget, [], 0)

        self._block(ctx, stmts)
        if not ctx.code or ctx.code[-1][0] not in ("RET", "RETNIL"):
            ctx.emit("RETNIL")
        self.out.append((idx, ctx))
        return idx, captures

    # -- statements -------------------------------------------------------

    def _block(self, ctx, block):
        stmts = block.stmts if isinstance(block, A.Block) else block
        for st in stmts:
            self._stmt(ctx, st)

    def _stmt(self, ctx, s):
        if isinstance(s, A.Let):
            self._expr(ctx, s.expr)
            ctx.emit("STORE", ctx.slot(s.name))

        elif isinstance(s, A.Assign):
            if not ctx.has(s.name):
                raise CompileError(f"assignment to undeclared {s.name!r}")
            self._expr(ctx, s.expr)
            ctx.emit("STORE", ctx.slots[s.name])

        elif isinstance(s, A.Return):
            if s.expr is None:
                ctx.emit("RETNIL")
            else:
                self._expr(ctx, s.expr)
                ctx.emit("RET")

        elif isinstance(s, A.If):
            self._expr(ctx, s.cond)
            jz = ctx.emit("JZ", -1)
            self._block(ctx, s.then)
            if getattr(s, "otherwise", None):
                jmp = ctx.emit("JMP", -1)
                ctx.patch(jz, ctx.here())
                self._block(ctx, s.otherwise)
                ctx.patch(jmp, ctx.here())
            else:
                ctx.patch(jz, ctx.here())

        elif isinstance(s, A.While):
            top = ctx.here()
            self._expr(ctx, s.cond)
            jz = ctx.emit("JZ", -1)
            ctx.loops.append((top, []))
            self._block(ctx, s.body)
            ctx.emit("JMP", top)
            ctx.patch(jz, ctx.here())
            _, breaks = ctx.loops.pop()
            for b in breaks:
                ctx.patch(b, ctx.here())

        elif isinstance(s, A.Break):
            if not ctx.loops:
                raise CompileError("break outside a loop")
            ctx.loops[-1][1].append(ctx.emit("JMP", -1))

        elif isinstance(s, A.Continue):
            if not ctx.loops:
                raise CompileError("continue outside a loop")
            ctx.emit("JMP", ctx.loops[-1][0])

        elif isinstance(s, A.Step):
            body, caps_v = self._lift("step", s.body, caps=ctx.caps,
                                      value=True, enclosing=ctx)
            for cv in caps_v:
                ctx.emit("LOAD", ctx.slots[cv])
            ctx.emit("STEP", self.K.string(s.name), body, len(caps_v))
            ctx.emit("POP")

        elif isinstance(s, A.ExprStmt):
            self._expr(ctx, s.expr)
            ctx.emit("POP")

        elif isinstance(s, A.Policy):
            retry = int(getattr(s, "retry", 0) or 0)
            fallback = getattr(s, "fallback", None)
            self._policy.append((retry, fallback))
            try:
                self._block(ctx, s.body)
            finally:
                self._policy.pop()

        else:
            raise CompileError(f"{type(s).__name__} not handled")

    @staticmethod
    def _dotted(node):
        """Reconstruct a dotted name: db.dump() parses as Member(Ident(db), dump)."""
        if isinstance(node, str):
            return node
        if isinstance(node, A.Ident):
            return node.name
        if isinstance(node, A.Member):
            base = Compiler._dotted(node.base)
            return f"{base}.{node.name}" if base else node.name
        return getattr(node, "name", None)

    @staticmethod
    def _argexprs(args):
        """Call and Gen args are (name, expr) pairs; name is None if positional."""
        out = []
        for a in (args.items() if isinstance(args, dict) else args):
            out.append(a[1] if isinstance(a, tuple) else a)
        return out

    # -- expressions ------------------------------------------------------

    def _expr(self, ctx, e):
        if isinstance(e, A.Literal):
            v = e.value
            if isinstance(v, bool):
                ctx.emit("PUSHK", self.K.boolean(v))
            elif isinstance(v, (int, float)):
                ctx.emit("PUSHK", self.K.number(v))
            elif v is None:
                ctx.emit("PUSHK", self.K.nil())
            else:
                ctx.emit("PUSHK", self.K.string(v))

        elif isinstance(e, A.Ident):
            if not ctx.has(e.name):
                raise CompileError(f"undefined name {e.name!r}")
            ctx.emit("LOAD", ctx.slots[e.name])

        elif isinstance(e, A.ListLit):
            for item in e.items:
                self._expr(ctx, item)
            ctx.emit("LIST", len(e.items))

        elif isinstance(e, A.RecordLit):
            tname = getattr(e, "type_name", None) or getattr(e, "name", None)
            ti = self.type_index.get(tname, -1)
            fields = list(e.fields.items()) if isinstance(e.fields, dict) \
                else list(e.fields)
            for f in fields:
                self._expr(ctx, f[1] if isinstance(f, tuple) else f)
            ctx.emit("RECORD", ti, len(fields))

        elif isinstance(e, A.Member):
            self._expr(ctx, e.base)
            ctx.emit("FIELD", self.K.string(e.name))

        elif isinstance(e, A.Index):
            self._expr(ctx, e.base)
            self._expr(ctx, e.index)
            ctx.emit("INDEX")

        elif isinstance(e, A.BinOp):
            self._expr(ctx, e.left)
            self._expr(ctx, e.right)
            ctx.emit({"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV",
                      "%": "MOD", "<": "LT", ">": "GT", "<=": "LE",
                      ">=": "GE", "==": "EQ", "!=": "NE"}[e.op])

        elif isinstance(e, A.UnaryOp):
            self._expr(ctx, e.operand)
            ctx.emit("NEG" if e.op == "-" else "NOT")

        elif isinstance(e, A.LogicOp):
            # Short-circuit compiled to jumps, so the semantics are in the
            # stream rather than in a host's reading of an opcode.
            self._expr(ctx, e.left)
            ctx.emit("DUP")
            jump = ctx.emit("JZ" if e.op == "and" else "JNZ", -1)
            ctx.emit("POP")
            self._expr(ctx, e.right)
            ctx.patch(jump, ctx.here())

        elif isinstance(e, A.Call):
            fname = self._dotted(e.callee)
            eargs = self._argexprs(e.args)
            if fname in ("print", "len"):
                for a in eargs:
                    self._expr(ctx, a)
                ctx.emit("PRINT" if fname == "print" else "LEN")
                if fname == "print":
                    ctx.emit("PUSHK", self.K.nil())
                return
            if fname not in self.fn_index and fname in self.fns:
                self._reserve(fname)
            if fname not in self.fn_index:
                # A capability call, e.g. db.write(...)
                y = self.cap(fname)
                for a in eargs:
                    self._expr(ctx, a)
                ctx.emit("REQUIRE", y, 0)
                ctx.emit("PUSHK", self.K.nil())
                return
            for a in eargs:
                self._expr(ctx, a)
            ctx.emit("CALL", self.fn_index[fname], len(eargs))

        elif isinstance(e, A.Gen):
            ti = self.type_index.get(e.type_name, -1)
            pname = e.prompt_name
            pi = self.prompt_index.get(pname, -1)
            args = self._argexprs(e.args)
            for a in args:
                self._expr(ctx, a)
            retry, fallback = self._policy[-1]
            fb = self.K.string(fallback) if fallback else -1
            ctx.emit("REQUIRE", self.cap("model"), 1)
            ctx.emit("GEN", ti, pi, len(args), self.K.string(
                getattr(e, "model", "large") or "large"), retry, fb)

        elif isinstance(e, A.For):
            body, caps_v = self._lift("for", e.body, params=[e.var],
                                      caps=ctx.caps, value=True, enclosing=ctx)
            self._expr(ctx, e.iterable)
            for cv in caps_v:
                ctx.emit("LOAD", ctx.slots[cv])
            ctx.emit("PARALLEL", body, 1, len(caps_v))

        elif isinstance(e, A.ParallelFor):
            body, caps_v = self._lift("par", e.body, params=[e.var],
                                      caps=ctx.caps, value=True, enclosing=ctx)
            self._expr(ctx, e.iterable)
            for cv in caps_v:
                ctx.emit("LOAD", ctx.slots[cv])
            ctx.emit("PARALLEL", body, int(getattr(e, "limit", 0) or 0),
                     len(caps_v))

        elif isinstance(e, A.Step):
            body, caps_v = self._lift("step", e.body, caps=ctx.caps,
                                      value=True, enclosing=ctx)
            for cv in caps_v:
                ctx.emit("LOAD", ctx.slots[cv])
            ctx.emit("STEP", self.K.string(e.name), body, len(caps_v))

        elif isinstance(e, A.Try):
            body, caps_v = self._lift("try", [A.Return(e.expr)],
                                      caps=ctx.caps, enclosing=ctx)
            for cv in caps_v:
                ctx.emit("LOAD", ctx.slots[cv])
            ctx.emit("TRY", body, len(caps_v))

        else:
            raise CompileError(f"{type(e).__name__} not handled")

    # -- emit -------------------------------------------------------------

    def serialise(self):
        lines = [MAGIC]

        consts = self.K.emit()
        lines.append(f"K {len(consts)}")
        lines.extend(consts)

        lines.append(f"Y {len(self.caps)}")
        lines.extend(self.caps)

        lines.append(f"T {len(self.type_order)}")
        for name in self.type_order:
            t = self.types[name]
            fields = ",".join(
                f"{f.name}:{f.type.name}{'?' if getattr(f.type, 'optional', False) else ''}"
                for f in t.fields)
            lines.append(f"{name} {fields}")

        lines.append(f"P {len(self.prompt_order)}")
        for name in self.prompt_order:
            p = self.prompts[name]
            sysk, usrk = self._prompt_consts[name]
            params = ",".join(x.name for x in getattr(p, "params", []))
            lines.append(f"{name} {sysk} {usrk} {params or '-'}")

        by_index = dict(self.out)
        lines.append(f"F {len(self.fn_order)}")
        for i, name in enumerate(self.fn_order):
            ctx = by_index.get(i)
            if ctx is None:
                lines.append(f"{name} 0 0 4 - - - 0")
                lines.append("RETNIL")
                lines.append("ENDF")
                continue
            caps = ",".join(str(c) for c in ctx.caps) or "-"
            budget = repr(ctx.budget) if ctx.budget is not None else "-"
            taint = ",".join(str(t) for t in ctx.taint) or "-"
            lines.append(f"{name} {len(ctx.params)} {ctx.nslots} "
                         f"{ctx.max_stack()} {caps} {budget} {taint} "
                         f"{ctx.durable}")
            for ins in ctx.code:
                lines.append(" ".join(str(x) for x in ins))
            lines.append("ENDF")

        return "\n".join(lines) + "\n"


def compile_program(types, prompts, fns, capabilities=()):
    return Compiler(types, prompts, fns, capabilities).compile()


def to_hbx(types, prompts, fns, capabilities=()):
    return compile_program(types, prompts, fns, capabilities).serialise()
