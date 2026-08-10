// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright 2026 Nandu Aravindakshan

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// These mirror the assertions in tests/test_hbx.py. If the Python host and
// this one disagree about any of them, one of us is running a different
// language.

func compile(t *testing.T, hbx string) *Module {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "p.hbx")
	if err := os.WriteFile(path, []byte(hbx), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	m, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	return m
}

func runVM(t *testing.T, hbx string) ([]string, *VM, error) {
	t.Helper()
	var out []string
	vm := NewVM(compile(t, hbx), MockModel{}, func(s string) {
		out = append(out, s)
	})
	_, err := vm.Run("main")
	return out, vm, err
}

const arith = `HBX 2
K 3
N 2.0
N 3.0
N 4.0
Y 1
model
T 0
P 0
F 1
main 0 0 8 - - - 0
PUSHK 0
PUSHK 1
PUSHK 2
MUL
ADD
PRINT
RETNIL
ENDF
`

func TestArithmeticAndPrecedence(t *testing.T) {
	out, _, err := runVM(t, arith)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(out) != 1 || out[0] != "14" {
		t.Fatalf("got %v, want [14]", out)
	}
}

func TestForeignFileIsRefused(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "old.hbir")
	if err := os.WriteFile(path, []byte("HBIR 1\nK 0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("a non-HBX file was accepted")
	}
}

// A function that does not hold a capability must be refused, and the error
// must name both the function and the capability.
func TestCapabilityIsEnforced(t *testing.T) {
	const prog = `HBX 2
K 0
Y 2
model
db.dump
T 0
P 0
F 1
main 0 0 4 0 - - 0
REQUIRE 1 0
RETNIL
ENDF
`
	_, vm, err := runVM(t, prog)
	if err == nil {
		t.Fatal("capability was not enforced")
	}
	ce, ok := err.(*CapabilityError)
	if !ok {
		t.Fatalf("got %T, want *CapabilityError", err)
	}
	if ce.Cap != "db.dump" || ce.Fn != "main" {
		t.Fatalf("wrong error detail: %v", ce)
	}
	if vm.Blocked != 1 {
		t.Fatalf("blocked=%d, want 1", vm.Blocked)
	}
}

func TestHeldCapabilityIsAllowed(t *testing.T) {
	const prog = `HBX 2
K 0
Y 2
model
db.dump
T 0
P 0
F 1
main 0 0 4 0,1 - - 0
REQUIRE 1 0
RETNIL
ENDF
`
	if _, vm, err := runVM(t, prog); err != nil || vm.Blocked != 0 {
		t.Fatalf("wrongly refused: err=%v blocked=%d", err, vm.Blocked)
	}
}

// Attenuation: a callee declaring db.dump gets it only if the caller held it.
func TestCapabilityCannotBeAmplified(t *testing.T) {
	const prog = `HBX 2
K 0
Y 2
model
db.dump
T 0
P 0
F 2
main 0 0 4 0 - - 0
CALL 1 0
POP
RETNIL
ENDF
leak 0 0 4 0,1 - - 0
REQUIRE 1 0
RETNIL
ENDF
`
	_, _, err := runVM(t, prog)
	if err == nil {
		t.Fatal("callee amplified its authority")
	}
	if _, ok := err.(*CapabilityError); !ok {
		t.Fatalf("got %T, want *CapabilityError", err)
	}
}

// A tainted value must not reach a capability call.
func TestTaintIsRefusedAtACapability(t *testing.T) {
	const prog = `HBX 2
K 1
S dirty
Y 2
model
db.write
T 0
P 0
F 2
main 0 0 4 0,1 - - 0
PUSHK 0
CALL 1 1
POP
RETNIL
ENDF
handle 1 1 4 0,1 - 0 0
LOAD 0
REQUIRE 1 0
RETNIL
ENDF
`
	_, _, err := runVM(t, prog)
	if err == nil {
		t.Fatal("tainted value reached a capability")
	}
	if _, ok := err.(*TaintError); !ok {
		t.Fatalf("got %T (%v), want *TaintError", err, err)
	}
}

// The same program without the taint marker must be allowed, or the check is
// simply refusing everything.
func TestCleanValueReachesTheCapability(t *testing.T) {
	const prog = `HBX 2
K 1
S clean
Y 2
model
db.write
T 0
P 0
F 2
main 0 0 4 0,1 - - 0
PUSHK 0
CALL 1 1
POP
RETNIL
ENDF
handle 1 1 4 0,1 - - 0
LOAD 0
REQUIRE 1 0
RETNIL
ENDF
`
	if _, _, err := runVM(t, prog); err != nil {
		t.Fatalf("clean value was refused: %v", err)
	}
}

func TestBudgetStopsOverspend(t *testing.T) {
	const prog = `HBX 2
K 2
N 1.0
S large
Y 1
model
T 1
R text:string
P 1
p 1 1 -
F 1
main 0 0 8 0 0.001 - 0
REQUIRE 0 1
GEN 0 0 0 1
POP
REQUIRE 0 1
GEN 0 0 0 1
POP
RETNIL
ENDF
`
	_, _, err := runVM(t, prog)
	if err == nil {
		t.Fatal("budget was not enforced")
	}
	if _, ok := err.(*BudgetError); !ok {
		t.Fatalf("got %T (%v), want *BudgetError", err, err)
	}
}

// A child cannot spend allowance its parent does not have, however generous
// its own declared limit.
func TestChildCannotExceedParentAllowance(t *testing.T) {
	const prog = `HBX 2
K 2
N 1.0
S large
Y 1
model
T 1
R text:string
P 1
p 1 1 -
F 2
main 0 0 4 0 0.001 - 0
CALL 1 0
POP
RETNIL
ENDF
child 0 0 8 0 100.0 - 0
REQUIRE 0 1
GEN 0 0 0 1
POP
RETNIL
ENDF
`
	if _, _, err := runVM(t, prog); err == nil {
		t.Fatal("child outspent its parent")
	}
}

func TestTaintPropagatesThroughArithmetic(t *testing.T) {
	const prog = `HBX 2
K 1
N 1.0
Y 2
model
db.write
T 0
P 0
F 2
main 0 0 4 0,1 - - 0
PUSHK 0
CALL 1 1
POP
RETNIL
ENDF
handle 1 1 6 0,1 - 0 0
LOAD 0
PUSHK 0
ADD
REQUIRE 1 0
RETNIL
ENDF
`
	_, _, err := runVM(t, prog)
	if err == nil {
		t.Fatal("taint did not survive arithmetic")
	}
	if _, ok := err.(*TaintError); !ok {
		t.Fatalf("got %T, want *TaintError", err)
	}
}

func TestDivisionByZeroIsAnError(t *testing.T) {
	const prog = `HBX 2
K 2
N 1.0
N 0.0
Y 0
T 0
P 0
F 1
main 0 0 6 - - - 0
PUSHK 0
PUSHK 1
DIV
PRINT
RETNIL
ENDF
`
	if _, _, err := runVM(t, prog); err == nil ||
		!strings.Contains(err.Error(), "division by zero") {
		t.Fatalf("got %v, want a division-by-zero error", err)
	}
}

// flakyModel fails n times, then succeeds. It records which model was asked
// for, so a fallback can be observed rather than inferred.
type flakyModel struct {
	left   int
	err    func(string) error
	models []string
}

func (m *flakyModel) Generate(model, system, user string, schema []string) (Value, float64, error) {
	m.models = append(m.models, model)
	if m.left > 0 {
		m.left--
		return Nil(), 0, m.err("boom")
	}
	return Rec(map[string]Value{"text": Str("ok")}), 0.001, nil
}

// A GEN carrying retry 2. Fallback index -1 means none.
const genRetry2 = `HBX 2
K 2
N 1.0
S large
Y 1
model
T 1
R text:string
P 1
p 1 1 -
F 1
main 0 0 8 0 - - 0
REQUIRE 0 1
GEN 0 0 0 1 2 -1
POP
RETNIL
ENDF
`

func TestTransientFailureIsRetried(t *testing.T) {
	m := &flakyModel{left: 2, err: func(s string) error {
		return &TransientError{Msg: s}
	}}
	vm := NewVM(compile(t, genRetry2), m, func(string) {})
	if _, err := vm.Run("main"); err != nil {
		t.Fatalf("retry did not recover: %v", err)
	}
	if vm.Retries != 2 {
		t.Fatalf("Retries = %d, want 2", vm.Retries)
	}
	if vm.Gens != 1 {
		t.Fatalf("Gens = %d, want 1", vm.Gens)
	}
}

func TestRetriesAreFinite(t *testing.T) {
	m := &flakyModel{left: 99, err: func(s string) error {
		return &TransientError{Msg: s}
	}}
	vm := NewVM(compile(t, genRetry2), m, func(string) {})
	_, err := vm.Run("main")
	if err == nil {
		t.Fatal("exhausted retries did not surface an error")
	}
	if !strings.Contains(err.Error(), "3 attempt(s)") {
		t.Fatalf("got %v, want a 3-attempt failure", err)
	}
}

// Same program with no policy: retry 0, so one failure is fatal.
const genNoPolicy = `HBX 2
K 2
N 1.0
S large
Y 1
model
T 1
R text:string
P 1
p 1 1 -
F 1
main 0 0 8 0 - - 0
REQUIRE 0 1
GEN 0 0 0 1 0 -1
POP
RETNIL
ENDF
`

func TestWithoutPolicyOneFailureIsFatal(t *testing.T) {
	m := &flakyModel{left: 1, err: func(s string) error {
		return &TransientError{Msg: s}
	}}
	vm := NewVM(compile(t, genNoPolicy), m, func(string) {})
	if _, err := vm.Run("main"); err == nil {
		t.Fatal("a failure without a policy should be fatal")
	}
}

// retry 1, fallback pointing at constant 2 ("small").
const genFallback = `HBX 2
K 3
N 1.0
S large
S small
Y 1
model
T 1
R text:string
P 1
p 1 1 -
F 1
main 0 0 8 0 - - 0
REQUIRE 0 1
GEN 0 0 0 1 1 2
POP
RETNIL
ENDF
`

type refusingModel struct{ models []string }

func (m *refusingModel) Generate(model, system, user string, schema []string) (Value, float64, error) {
	m.models = append(m.models, model)
	if model == "large" {
		return Nil(), 0, &RefusalError{Msg: "missing field"}
	}
	return Rec(map[string]Value{"text": Str("ok")}), 0.001, nil
}

func TestSoftFailureFallsBackToAnotherModel(t *testing.T) {
	m := &refusingModel{}
	vm := NewVM(compile(t, genFallback), m, func(string) {})
	if _, err := vm.Run("main"); err != nil {
		t.Fatalf("fallback did not recover: %v", err)
	}
	if len(m.models) != 2 || m.models[0] != "large" || m.models[1] != "small" {
		t.Fatalf("models tried = %v, want [large small]", m.models)
	}
	if vm.Fallbacks != 1 {
		t.Fatalf("Fallbacks = %d, want 1", vm.Fallbacks)
	}
}

// Two steps, each doing one GEN. Enough to crash between them.
const twoSteps = `HBX 2
K 4
S one
S two
N 1.0
S large
Y 1
model
T 1
R text:string
P 1
p 3 3 -
F 3
main 0 0 8 0 - - 1
STEP 0 1 0
POP
STEP 1 2 0
POP
RETNIL
ENDF
s1 0 0 8 0 - - 0
REQUIRE 0 1
GEN 0 0 0 3 0 -1
RET
ENDF
s2 0 0 8 0 - - 0
REQUIRE 0 1
GEN 0 0 0 3 0 -1
RET
ENDF
`

// dyingModel succeeds `ok` times, then fails the way a killed process does.
type dyingModel struct{ ok, n int }

func (m *dyingModel) Generate(model, system, user string, schema []string) (Value, float64, error) {
	m.n++
	if m.n > m.ok {
		return Nil(), 0, &TransientError{Msg: "process died"}
	}
	return Rec(map[string]Value{"text": Str("v")}), 0.002, nil
}

func TestACrashedRunResumesWhereItStopped(t *testing.T) {
	dir := t.TempDir()
	mod := compile(t, twoSteps)

	// First run: step one succeeds, step two dies.
	j1, err := OpenJournal(dir, "main")
	if err != nil {
		t.Fatalf("open journal: %v", err)
	}
	if j1.Restored {
		t.Fatal("a fresh journal reported itself as restored")
	}
	vm1 := NewVM(mod, &dyingModel{ok: 1}, func(string) {})
	vm1.Journal = j1
	if _, err := vm1.Run("main"); err == nil {
		t.Fatal("the run was supposed to fail at step two")
	}

	// Second run: step one must not execute again.
	j2, err := OpenJournal(dir, "main")
	if err != nil {
		t.Fatalf("reopen journal: %v", err)
	}
	if !j2.Restored {
		t.Fatal("journal did not survive the crash")
	}
	m2 := &dyingModel{ok: 99}
	vm2 := NewVM(mod, m2, func(string) {})
	vm2.Journal = j2
	if _, err := vm2.Run("main"); err != nil {
		t.Fatalf("resumed run failed: %v", err)
	}
	if vm2.Replayed != 1 {
		t.Fatalf("Replayed = %d, want 1", vm2.Replayed)
	}
	if m2.n != 1 {
		t.Fatalf("the model was called %d times on resume, want 1", m2.n)
	}
}

func TestAResumedRunDoesNotRespend(t *testing.T) {
	dir := t.TempDir()
	mod := compile(t, twoSteps)

	j1, _ := OpenJournal(dir, "main")
	vm1 := NewVM(mod, &dyingModel{ok: 1}, func(string) {})
	vm1.Journal = j1
	_, _ = vm1.Run("main")

	j2, _ := OpenJournal(dir, "main")
	if j2.Spent <= 0 {
		t.Fatalf("journal recorded spend %v, want the pre-crash amount", j2.Spent)
	}
	vm2 := NewVM(mod, &dyingModel{ok: 99}, func(string) {})
	vm2.Journal = j2
	if _, err := vm2.Run("main"); err != nil {
		t.Fatalf("resume failed: %v", err)
	}
	// Total spend carries the pre-crash amount, so a resumed run cannot
	// exceed a budget by counting from zero again.
	if vm2.Spent <= j2.Spent {
		t.Fatalf("Spent = %v, want more than the restored %v", vm2.Spent, j2.Spent)
	}
}

func TestACompletedRunStartsFreshNextTime(t *testing.T) {
	dir := t.TempDir()
	mod := compile(t, twoSteps)

	for i := 0; i < 2; i++ {
		j, _ := OpenJournal(dir, "main")
		vm := NewVM(mod, &dyingModel{ok: 99}, func(string) {})
		vm.Journal = j
		if _, err := vm.Run("main"); err != nil {
			t.Fatalf("run %d failed: %v", i, err)
		}
		if vm.Replayed != 0 {
			t.Fatalf("run %d replayed %d steps; a finished run was resumed",
				i, vm.Replayed)
		}
	}
}

func TestWithoutAJournalNothingIsReplayed(t *testing.T) {
	vm := NewVM(compile(t, twoSteps), &dyingModel{ok: 99}, func(string) {})
	if _, err := vm.Run("main"); err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if vm.Replayed != 0 {
		t.Fatalf("Replayed = %d without a journal", vm.Replayed)
	}
}

func TestATornFinalLineDoesNotDiscardTheJournal(t *testing.T) {
	dir := t.TempDir()
	mod := compile(t, twoSteps)

	j1, _ := OpenJournal(dir, "main")
	vm1 := NewVM(mod, &dyingModel{ok: 1}, func(string) {})
	vm1.Journal = j1
	_, _ = vm1.Run("main")

	// Simulate a crash partway through a write.
	raw, err := os.ReadFile(j1.Path)
	if err != nil {
		t.Fatalf("read journal: %v", err)
	}
	if err := os.WriteFile(j1.Path, append(raw, []byte(`{"op":"ste`)...), 0o600); err != nil {
		t.Fatalf("write journal: %v", err)
	}

	j2, err := OpenJournal(dir, "main")
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	if !j2.Restored {
		t.Fatal("a torn final line discarded the whole journal")
	}
}

func TestDisplayMatchesTheInterpreter(t *testing.T) {
	cases := []struct {
		v    Value
		want string
	}{
		{Num(5), "5"},
		{Num(2.5), "2.5"},
		{Bool(true), "true"},
		{Bool(false), "false"},
		{Nil(), "nil"},
		{Str("hi"), "hi"},
	}
	for _, c := range cases {
		if got := c.v.Display(); got != c.want {
			t.Errorf("Display() = %q, want %q", got, c.want)
		}
	}
}
