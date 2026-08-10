# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""humbaba run|build|check FILE"""

import argparse
import os
import sys
import time

from .parser import parse_file, ParseError
from .lexer import LexError
from .check import check as static_check, CheckError
from .model import MockModel
from .runtime import Interpreter, HumbabaError
from .compile import FastProgram


def main(argv=None):
    ap = argparse.ArgumentParser(prog="humbaba", description="Humbaba v0.3")
    ap.add_argument("command", choices=["run", "build", "check"])
    ap.add_argument("file")
    ap.add_argument("--entry", default="main")
    ap.add_argument("-o", "--out", default=None,
                    help="output path for `humbaba build` (default: FILE.hbx)")
    ap.add_argument("--cassette", default=None,
                    help="record/replay model responses to this file")
    ap.add_argument("--provider", default="mock",
                    choices=["mock", "anthropic", "openai"],
                    help="mock (default) or a real API")
    ap.add_argument("--chaos", type=float, default=0.0,
                    help="probability a model call fails transiently")
    ap.add_argument("--overloaded", default="",
                    help="comma-separated models that return malformed output")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--journal", default=None,
                    help="directory for durable-execution journals")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any existing journal and start over")
    ap.add_argument("--scheduler", choices=["threads", "asyncio"],
                    default="threads",
                    help="asyncio lifts the concurrency ceiling ~50x")
    ap.add_argument("--no-check", action="store_true",
                    help="skip static checking (not recommended)")
    ap.add_argument("--quiet", action="store_true", help="hide runtime trace")
    ap.add_argument("--backend", choices=["fast", "tree"], default="fast")
    args = ap.parse_args(argv)

    # ---- front end
    try:
        types, prompts, fns, caps = parse_file(args.file)
    except (ParseError, LexError) as e:
        print(f"parse error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"cannot read: {e}", file=sys.stderr)
        return 2

    # ---- static checking
    if not args.no_check:
        try:
            static_check(types, prompts, fns, caps)
        except CheckError as e:
            print(f"\n{len(e.errors)} error(s):\n", file=sys.stderr)
            for err in e.errors:
                print(f"  {err}", file=sys.stderr)
            print(file=sys.stderr)
            return 2

    if args.command == "check":
        print(f"  ok — {len(types)} type(s), {len(prompts)} prompt(s), "
              f"{len(fns)} function(s), {len(caps)} capability declaration(s)")
        return 0

    if args.command == "build":
        # HBX carries the enforcement primitives, so the whole language
        # compiles. The v1 register IR covered only the compute subset and
        # could build one of the thirteen shipped examples.
        from . import hbx as hbx_mod
        try:
            prog = hbx_mod.compile_program(types, prompts, fns, caps)
            text = prog.serialise()
        except Exception as e:
            print(f"cannot compile: {e}", file=sys.stderr)
            return 1
        out = args.out or (args.file.rsplit(".", 1)[0] + ".hbx")
        with open(out, "w") as f:
            f.write(text)
        print(f"  wrote {out}  ({len(prog.fn_order)} function(s), "
              f"{len(prog.K)} constant(s), {len(prog.caps)} capability/ies)")
        return 0

    # ---- provider
    if args.provider == "mock":
        model = MockModel(cassette=args.cassette, chaos=args.chaos,
                          seed=args.seed,
                          overloaded=[m for m in args.overloaded.split(",") if m])
    else:
        from .providers import get
        try:
            model = get(args.provider)
        except RuntimeError as e:
            print(f"provider error: {e}", file=sys.stderr)
            return 2

    journal_dir = args.journal
    if args.fresh and journal_dir:
        import shutil
        shutil.rmtree(journal_dir, ignore_errors=True)

    if args.backend == "fast":
        interp = FastProgram(types, prompts, fns, model, trace=not args.quiet,
                             journal_dir=journal_dir, scheduler=args.scheduler)
    else:
        interp = Interpreter(types, prompts, fns, model, trace=not args.quiet)

    start = time.time()
    try:
        _, budget = interp.run(args.entry)
    except HumbabaError as e:
        print(f"\n  runtime error: {e}", file=sys.stderr)
        model.save()
        return 1
    elapsed = time.time() - start
    model.save()

    if not args.quiet:
        cap = f"{budget.limit:.2f}" if budget.limit is not None else "none"
        print(
            f"\n  {interp.gen_calls} gen call(s) · "
            f"spent £{budget.spent:.4f} of {cap} · "
            f"{model.hits} replayed, {model.misses} live · "
            f"{interp.denials} blocked · {elapsed:.2f}s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
