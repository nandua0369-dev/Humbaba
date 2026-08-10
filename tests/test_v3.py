"""Tests for the limitations solved in v0.3.

Each class maps to a section of docs/LIMITATIONS.md. Run alongside
tests/test_humbaba.py, which covers v0.1-v0.2 guarantees.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humbaba.parser import Parser, parse, parse_file
from humbaba.check import check as static_check, CheckError
from humbaba.model import MockModel, RefusalError
from humbaba.compile import FastProgram
from humbaba.runtime import BudgetExceeded, HumbabaError

BASE = """
type Invoice { vendor: string  total: number }
prompt extract(document: untrusted string) {
  system: "Extract fields."
  user:   "Document: {document}"
}
"""


def check_src(src):
    """Parse and statically check. Returns errors, empty list if clean."""
    p = Parser(src)
    types, prompts, fns = p.parse()
    try:
        static_check(types, prompts, fns, p.caps)
        return []
    except CheckError as e:
        return e.errors


def run(src, **kw):
    p = Parser(src)
    types, prompts, fns = p.parse()
    static_check(types, prompts, fns, p.caps)
    model = MockModel(**kw)
    prog = FastProgram(types, prompts, fns, model, trace=False)
    value, budget = prog.run()
    return prog, budget, model


class CheckerCase(unittest.TestCase):
    def assertCaught(self, src, fragment):
        errs = check_src(src)
        self.assertTrue(errs, "expected an error, got none")
        self.assertTrue(any(fragment in e for e in errs),
                        f"expected {fragment!r} among {errs}")

    def assertClean(self, src):
        self.assertEqual(check_src(src), [])


# ---------------------------------------------------------------- §1


class TestBlockingGaps(CheckerCase):
    """LIMITATIONS §1 — the four gaps that made real programs impossible."""

    def test_assignment_and_while(self):
        run("""fn main() {
          var t = 0
          var i = 0
          while i < 5 { i = i + 1  t = t + i }
          print(t)
        }""")

    def test_break_and_continue(self):
        run("""fn main() {
          var i = 0
          while i < 100 {
            i = i + 1
            if i == 2 { continue }
            if i > 4 { break }
          }
          print(i)
        }""")

    def test_boolean_operators(self):
        run("""fn main() {
          print(true and false, true or false, not true)
        }""")

    def test_unary_minus(self):
        run("fn main() { let x = 5  print(-x) }")

    def test_nested_record_types_accepted(self):
        self.assertClean("""
        type Line  { sku: string  qty: number }
        type Order { id: string  line: Line }
        fn main() { print(1) }""")

    def test_optional_field_may_be_omitted(self):
        self.assertClean("""
        type Order { id: string  note: string? }
        fn main() { let o = Order { id: "a" }  print(o.id) }""")

    def test_missing_required_field_rejected(self):
        self.assertCaught("""
        type Order { id: string  total: number }
        fn main() { let o = Order { id: "a" } }""", "missing field")

    def test_list_indexing(self):
        run('fn main() { let xs = [10, 20, 30]  print(xs[1]) }')

    def test_index_out_of_range_is_a_clean_error(self):
        with self.assertRaises(HumbabaError):
            run('fn main() { let xs = [1]  print(xs[9]) }')


# ---------------------------------------------------------------- §2


class TestModules(unittest.TestCase):
    """LIMITATIONS §2.3 — imports."""

    def test_import_merges_declarations(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "lib.hb"), "w") as f:
            f.write("type Shared { v: number }\n")
        main = os.path.join(d, "main.hb")
        with open(main, "w") as f:
            f.write('import "lib.hb"\nfn main() { print(1) }\n')
        types, prompts, fns, caps = parse_file(main)
        self.assertIn("Shared", types)
        self.assertIn("main", fns)

    def test_import_cycle_terminates(self):
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, "a.hb"), os.path.join(d, "b.hb")
        with open(a, "w") as f:
            f.write('import "b.hb"\nfn main() { print(1) }\n')
        with open(b, "w") as f:
            f.write('import "a.hb"\ntype T { v: number }\n')
        types, _, fns, _ = parse_file(a)
        self.assertIn("T", types)


class TestJournal(unittest.TestCase):
    """LIMITATIONS §2.2 — a crash must not lose completed work."""

    SRC = BASE + """
    durable fn pipeline() uses { model } {
      let a = step "one"   { gen<Invoice> from extract(document: "INVOICE from A. 100") }
      let b = step "two"   { gen<Invoice> from extract(document: "INVOICE from B. 200") }
      let c = step "three" { gen<Invoice> from extract(document: "INVOICE from C. 300") }
      return c
    }"""

    def build(self, jd, fail_after=None):
        types, prompts, fns = parse(self.SRC)
        m = MockModel()
        if fail_after is not None:
            orig, state = m.generate, {"n": 0}

            def crashing(*a, **k):
                state["n"] += 1
                if state["n"] > fail_after:
                    raise RuntimeError("process died")
                return orig(*a, **k)
            m.generate = crashing
        return FastProgram(types, prompts, fns, m, trace=False,
                           journal_dir=jd), m

    def test_resumes_without_repeating_work(self):
        jd = os.path.join(tempfile.mkdtemp(), "j")
        shutil.rmtree(jd, ignore_errors=True)

        prog, _ = self.build(jd, fail_after=2)
        with self.assertRaises(RuntimeError):
            prog.run("pipeline")

        prog2, m2 = self.build(jd)
        _, budget = prog2.run("pipeline")
        self.assertEqual(m2.misses, 1, "only the unfinished step should run")
        self.assertGreater(budget.spent, 0)

    def test_completed_run_starts_fresh_next_time(self):
        jd = os.path.join(tempfile.mkdtemp(), "j2")
        prog, m1 = self.build(jd)
        prog.run("pipeline")
        self.assertEqual(m1.misses, 3)

        prog2, m2 = self.build(jd)
        prog2.run("pipeline")
        self.assertEqual(m2.misses, 3, "a finished run should not resume")


class TestProviderAdapters(unittest.TestCase):
    """LIMITATIONS §2.1 — the parts testable without network."""

    def test_schema_becomes_json_schema(self):
        from humbaba.providers import _schema_to_json
        js = _schema_to_json([("vendor", "string"), ("total", "number")])
        self.assertEqual(js["properties"]["total"]["type"], "number")
        self.assertIn("vendor", js["required"])
        self.assertFalse(js["additionalProperties"])

    def test_json_extraction_handles_fences_and_prose(self):
        from humbaba.providers import _extract_json
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(_extract_json('sure: {"a": 2} done'), {"a": 2})

    def test_unparseable_response_is_a_soft_failure(self):
        from humbaba.providers import _extract_json
        with self.assertRaises(RefusalError):
            _extract_json("not json at all")

    def test_missing_key_is_reported_clearly(self):
        from humbaba.providers import Anthropic
        with self.assertRaises(RuntimeError) as cm:
            Anthropic(api_key="")
        self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))


# ---------------------------------------------------------------- §3


class TestBudgetReservation(unittest.TestCase):
    """LIMITATIONS §3.1 — parallel blocks refuse rather than half-complete."""

    SRC = BASE + """
    fn main() uses { model } budget { max: 0.02 } {
      let docs = ["INVOICE from A Ltd. 100", "INVOICE from B Ltd. 200",
                  "INVOICE from C Ltd. 300", "INVOICE from D Ltd. 400",
                  "INVOICE from E Ltd. 500", "INVOICE from F Ltd. 600"]
      let out = parallel for d in docs {
        gen<Invoice> from extract(document: d)
      } limit 3
    }"""

    def test_refuses_before_starting(self):
        with self.assertRaises(BudgetExceeded) as cm:
            run(self.SRC)
        self.assertIn("needs up to", str(cm.exception))

    def test_nothing_is_spent_on_refusal(self):
        types, prompts, fns = parse(self.SRC)
        m = MockModel()
        prog = FastProgram(types, prompts, fns, m, trace=False)
        with self.assertRaises(BudgetExceeded):
            prog.run()
        self.assertEqual(m.misses, 0, "no call should have been made")

    def test_sufficient_budget_completes(self):
        _, budget, _ = run(self.SRC.replace("max: 0.02", "max: 1.00"))
        self.assertGreater(budget.spent, 0)


class TestUserCapabilities(CheckerCase):
    """LIMITATIONS §3.2 — the capability set is open."""

    def test_declared_capability_accepted(self):
        self.assertClean("capability db.write\nfn main() uses { db.write } { }")

    def test_undeclared_capability_rejected(self):
        self.assertCaught("fn main() uses { fs.delete } { }",
                          "undeclared capability")


class TestStaticChecker(CheckerCase):
    """LIMITATIONS §3.3 — caught at compile time, before money is spent."""

    def test_assign_to_let_rejected(self):
        self.assertCaught("fn main() { let x = 1  x = 2 }", "bound with `let`")

    def test_type_mismatch_rejected(self):
        self.assertCaught('fn main() { var x = 1  x = "s" }',
                          "cannot assign string")

    def test_unknown_field_rejected(self):
        self.assertCaught(
            'type T { a: string }\nfn main() { let t = T { a: "x" }  print(t.b) }',
            "has no field")

    def test_break_outside_loop_rejected(self):
        self.assertCaught("fn main() { break }", "outside a loop")

    def test_missing_prompt_argument_rejected(self):
        self.assertCaught(
            BASE + "fn main() uses { model } { let i = gen<Invoice> from extract() }",
            "missing argument")

    def test_wrong_arity_rejected(self):
        self.assertCaught("fn f(a: number) { }\nfn main() { f() }",
                          "takes 1 argument")

    def test_gen_without_model_capability_rejected(self):
        self.assertCaught(
            BASE + 'fn main() { let i = gen<Invoice> from extract(document: "x") }',
            "does not declare `model`")

    def test_return_type_mismatch_rejected(self):
        self.assertCaught('fn f() -> number { return "s" }', "declares -> number")

    def test_prompt_referencing_unknown_parameter_rejected(self):
        self.assertCaught(
            'prompt p(a: string) { system: "s" user: "{b}" }\nfn main() { }',
            "no such parameter")


class TestConcurrencySafety(CheckerCase):
    """LIMITATIONS §3.4 — assignment must not reintroduce races."""

    def test_outer_mutation_in_parallel_rejected(self):
        self.assertCaught("""
        fn main() {
          var total = 0
          let ys = parallel for x in [1, 2] { total = total + 1 } limit 2
        }""", "would race")

    def test_iteration_local_mutation_allowed(self):
        self.assertClean("""
        fn main() {
          let ys = parallel for x in [1, 2] {
            var local = 0
            local = local + x
            local
          } limit 2
        }""")


class TestTry(unittest.TestCase):
    """LIMITATIONS §3.5 — recovery beyond policy."""

    def test_try_converts_failure_into_a_value(self):
        prog, _, _ = run(BASE + """
        fn main() uses { model } budget { max: 0.0001 } {
          let r = try gen<Invoice> from extract(document: "x")
          print("survived")
        }""")
        self.assertEqual(prog.gen_calls, 0)


# ---------------------------------------------------------------- §4


class TestTaintPropagation(CheckerCase):
    """LIMITATIONS §4.4 — untrusted data must not reach a capability."""

    def test_model_output_from_untrusted_input_is_tainted(self):
        self.assertCaught(BASE + """
        fn main() uses { model, db.dump } {
          let i = gen<Invoice> from extract(document: "x")
          let r = db.dump(i.vendor)
        }""", "tainted")

    def test_taint_survives_an_intermediate_binding(self):
        self.assertCaught(BASE + """
        fn main() uses { model, db.dump } {
          let i = gen<Invoice> from extract(document: "x")
          let v = i.vendor
          let r = db.dump(v)
        }""", "tainted")

    def test_untainted_value_may_reach_a_capability(self):
        self.assertClean("""
        fn main() uses { db.dump } {
          let r = db.dump("a literal is not untrusted")
        }""")


# ---------------------------------------------------------------- §5


class TestSchedulers(unittest.TestCase):
    """LIMITATIONS §5 — the concurrency ceiling."""

    SRC = ("fn main() { let ys = parallel for x in ["
           + ", ".join(str(i) for i in range(2000))
           + "] { x * 2 } limit 1000\n print(len(ys)) }")

    def _run(self, scheduler):
        types, prompts, fns = parse(self.SRC)
        prog = FastProgram(types, prompts, fns, MockModel(), trace=False,
                           scheduler=scheduler)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            prog.run()
        return prog

    def test_both_schedulers_agree(self):
        self._run("threads")
        self._run("asyncio")


if __name__ == "__main__":
    unittest.main(verbosity=2)
