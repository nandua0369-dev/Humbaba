// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright 2026 Nandu Aravindakshan

package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Magic is the first line of every HBX file. A host that accepts anything
// else is not reading HBX.
const Magic = "HBX 2"

// Opcode is the interned form of an instruction name.
//
// The file format spells opcodes as text, which keeps a program greppable and
// diffable. Executing them as text would mean a string comparison on every
// instruction, so names are resolved to integers once at load and the
// interpreter switches on those.
type Opcode uint8

const (
	OpInvalid Opcode = iota
	OpPushK
	OpLoad
	OpStore
	OpPop
	OpDup
	OpAdd
	OpSub
	OpMul
	OpDiv
	OpMod
	OpNeg
	OpNot
	OpLt
	OpGt
	OpLe
	OpGe
	OpEq
	OpNe
	OpJmp
	OpJz
	OpJnz
	OpList
	OpIndex
	OpLen
	OpAppend
	OpRecord
	OpField
	OpPrint
	OpCall
	OpRet
	OpRetNil
	OpRequire
	OpCharge
	OpReserve
	OpRelease
	OpFence
	OpTaint
	OpUntaint
	OpGen
	OpParallel
	OpStep
	OpTry
)

// opNames maps the textual form to the interned one. An opcode missing here
// is rejected at load rather than at the moment it first executes, which is
// the difference between a program that fails to start and a program that
// fails in production on an untested branch.
var opNames = map[string]Opcode{
	"PUSHK": OpPushK, "LOAD": OpLoad, "STORE": OpStore, "POP": OpPop,
	"DUP": OpDup,
	"ADD": OpAdd, "SUB": OpSub, "MUL": OpMul, "DIV": OpDiv, "MOD": OpMod,
	"NEG": OpNeg, "NOT": OpNot,
	"LT": OpLt, "GT": OpGt, "LE": OpLe, "GE": OpGe, "EQ": OpEq, "NE": OpNe,
	"JMP": OpJmp, "JZ": OpJz, "JNZ": OpJnz,
	"LIST": OpList, "INDEX": OpIndex, "LEN": OpLen, "APPEND": OpAppend,
	"RECORD": OpRecord, "FIELD": OpField, "PRINT": OpPrint,
	"CALL": OpCall, "RET": OpRet, "RETNIL": OpRetNil,
	"REQUIRE": OpRequire, "CHARGE": OpCharge, "RESERVE": OpReserve,
	"RELEASE": OpRelease, "FENCE": OpFence, "TAINT": OpTaint,
	"UNTAINT": OpUntaint, "GEN": OpGen,
	"PARALLEL": OpParallel, "STEP": OpStep, "TRY": OpTry,
}

var opText = func() map[Opcode]string {
	m := make(map[Opcode]string, len(opNames))
	for k, v := range opNames {
		m[v] = k
	}
	return m
}()

// String returns the textual opcode, for error messages and disassembly.
// Implementing fmt.Stringer rather than a bespoke Name() means %s and %v
// work on an Opcode everywhere, including in code written later.
func (o Opcode) String() string {
	if s, ok := opText[o]; ok {
		return s
	}
	return "?"
}

// Instruction is one opcode with up to six integer operands. GEN needs six:
// type, prompt, argument count, model, retry count, and fallback model.
type Instruction struct {
	Op   Opcode
	Args [6]int
	N    int
}

func (in Instruction) Arg(i int) int {
	if i < in.N {
		return in.Args[i]
	}
	return 0
}

// Prompt is a system/user template pair with named parameters.
type Prompt struct {
	Name   string
	System int // constant index
	User   int // constant index
	Params []string
}

// TypeDef is a record schema: field names and their declared types.
type TypeDef struct {
	Name   string
	Fields []string // "name:type" or "name:type?"
	// fieldNames is Fields with the type suffix stripped, computed once at
	// load rather than by splitting strings on every RECORD instruction.
	fieldNames []string
}

// Fn is one compiled function, including everything enforcement needs.
type Fn struct {
	// declared is the capability set resolved once at load time. Rebuilding
	// it per call cost an allocation on every call, which on a recursive
	// program is the dominant cost of running the program at all.
	declared  map[string]bool
	taintSet  map[int]bool
	Name      string
	Arity     int
	NSlots    int
	MaxStack  int
	Caps      []int // indices into Module.Caps
	Budget    float64
	HasBudget bool
	Taint     []int // parameter positions declared untrusted
	Durable   bool
	Code      []Instruction
}

// Module is a loaded HBX program.
type Module struct {
	Consts  []Value
	Caps    []string
	Types   []TypeDef
	Prompts []Prompt
	Fns     []Fn
	ByName  map[string]int
}

// CapSet resolves a function's declared capability indices to names.
func (m *Module) CapSet(idx []int) map[string]bool {
	s := make(map[string]bool, len(idx))
	for _, i := range idx {
		if i >= 0 && i < len(m.Caps) {
			s[m.Caps[i]] = true
		}
	}
	return s
}

// Load reads an HBX file. It is deliberately strict: a malformed file is an
// error rather than something to be guessed at, because a host that guesses
// is a host that silently runs a different program.
func Load(path string) (*Module, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	// 64 KB initial, growing to 4 MB if a line demands it. The previous
	// 1 MB initial allocation was paid on every load, for files that are
	// typically a few kilobytes.
	sc.Buffer(make([]byte, 0, 64*1024), 4<<20)

	var lines []string
	for sc.Scan() {
		if t := sc.Text(); strings.TrimSpace(t) != "" {
			lines = append(lines, t)
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	if len(lines) == 0 || strings.TrimSpace(lines[0]) != Magic {
		return nil, fmt.Errorf("not an %s file", Magic)
	}

	m := &Module{ByName: map[string]int{}}
	i := 1

	header := func(letter string) (int, error) {
		if i >= len(lines) {
			return 0, fmt.Errorf("unexpected end of file, wanted section %q", letter)
		}
		parts := strings.Fields(lines[i])
		if len(parts) < 2 || parts[0] != letter {
			return 0, fmt.Errorf("expected section %q, got %q", letter, lines[i])
		}
		n, err := strconv.Atoi(parts[1])
		if err != nil {
			return 0, fmt.Errorf("bad count in section %q: %v", letter, err)
		}
		i++
		return n, nil
	}

	// --- constants ---
	n, err := header("K")
	if err != nil {
		return nil, err
	}
	for k := 0; k < n; k++ {
		if i >= len(lines) {
			return nil, fmt.Errorf("truncated constant pool")
		}
		ln := lines[i]
		i++
		kind := ln
		rest := ""
		if sp := strings.Index(ln, " "); sp >= 0 {
			kind, rest = ln[:sp], ln[sp+1:]
		}
		switch kind {
		case "N":
			fv, err := strconv.ParseFloat(strings.TrimSpace(rest), 64)
			if err != nil {
				return nil, fmt.Errorf("bad number constant %q", rest)
			}
			m.Consts = append(m.Consts, Num(fv))
		case "S":
			m.Consts = append(m.Consts, Str(unescape(rest)))
		case "B":
			m.Consts = append(m.Consts, Bool(strings.TrimSpace(rest) == "1"))
		case "Z":
			m.Consts = append(m.Consts, Nil())
		default:
			return nil, fmt.Errorf("unknown constant kind %q", kind)
		}
	}

	// --- capabilities ---
	n, err = header("Y")
	if err != nil {
		return nil, err
	}
	for k := 0; k < n; k++ {
		if i >= len(lines) {
			return nil, fmt.Errorf("truncated capability table")
		}
		m.Caps = append(m.Caps, strings.TrimSpace(lines[i]))
		i++
	}

	// --- types ---
	n, err = header("T")
	if err != nil {
		return nil, err
	}
	for k := 0; k < n; k++ {
		if i >= len(lines) {
			return nil, fmt.Errorf("truncated type table")
		}
		name := lines[i]
		fields := ""
		if sp := strings.Index(lines[i], " "); sp >= 0 {
			name, fields = lines[i][:sp], lines[i][sp+1:]
		}
		i++
		var fs []string
		for _, f := range strings.Split(fields, ",") {
			if f != "" {
				fs = append(fs, f)
			}
		}
		names := make([]string, len(fs))
		for k, f := range fs {
			names[k] = strings.SplitN(f, ":", 2)[0]
		}
		m.Types = append(m.Types, TypeDef{Name: name, Fields: fs,
			fieldNames: names})
	}

	// --- prompts ---
	n, err = header("P")
	if err != nil {
		return nil, err
	}
	for k := 0; k < n; k++ {
		if i >= len(lines) {
			return nil, fmt.Errorf("truncated prompt table")
		}
		p := strings.Fields(lines[i])
		i++
		if len(p) < 3 {
			return nil, fmt.Errorf("bad prompt entry")
		}
		sysK, _ := strconv.Atoi(p[1])
		usrK, _ := strconv.Atoi(p[2])
		var params []string
		if len(p) > 3 && p[3] != "-" {
			params = strings.Split(p[3], ",")
		}
		m.Prompts = append(m.Prompts, Prompt{
			Name: p[0], System: sysK, User: usrK, Params: params})
	}

	// --- functions ---
	n, err = header("F")
	if err != nil {
		return nil, err
	}
	for k := 0; k < n; k++ {
		if i >= len(lines) {
			return nil, fmt.Errorf("truncated function table")
		}
		p := strings.Fields(lines[i])
		i++
		if len(p) < 8 {
			return nil, fmt.Errorf("bad function header %q", p)
		}
		fn := Fn{Name: p[0]}
		fn.Arity, _ = strconv.Atoi(p[1])
		fn.NSlots, _ = strconv.Atoi(p[2])
		fn.MaxStack, _ = strconv.Atoi(p[3])
		if p[4] != "-" {
			for _, c := range strings.Split(p[4], ",") {
				ci, err := strconv.Atoi(c)
				if err != nil {
					return nil, fmt.Errorf("bad capability index %q", c)
				}
				fn.Caps = append(fn.Caps, ci)
			}
		}
		if p[5] != "-" {
			b, err := strconv.ParseFloat(p[5], 64)
			if err != nil {
				return nil, fmt.Errorf("bad budget %q", p[5])
			}
			fn.Budget, fn.HasBudget = b, true
		}
		if p[6] != "-" {
			for _, t := range strings.Split(p[6], ",") {
				ti, err := strconv.Atoi(t)
				if err != nil {
					return nil, fmt.Errorf("bad taint position %q", t)
				}
				fn.Taint = append(fn.Taint, ti)
			}
		}
		fn.Durable = p[7] == "1"

		for i < len(lines) && strings.TrimSpace(lines[i]) != "ENDF" {
			parts := strings.Fields(lines[i])
			i++
			if len(parts) == 0 {
				continue
			}
			op, ok := opNames[parts[0]]
			if !ok {
				return nil, fmt.Errorf("unknown instruction %q in function %q",
					parts[0], fn.Name)
			}
			in := Instruction{Op: op}
			if op == OpGen {
				// A GEN written before policy support omits these; treat a
				// missing fallback as "none" rather than as constant 0.
				in.Args[5] = -1
			}
			for a := 1; a < len(parts) && a <= 6; a++ {
				v, err := strconv.Atoi(parts[a])
				if err != nil {
					return nil, fmt.Errorf("bad operand %q in %s", parts[a], in.Op)
				}
				in.Args[a-1] = v
				in.N = a
			}
			fn.Code = append(fn.Code, in)
		}
		if i >= len(lines) {
			return nil, fmt.Errorf("function %q has no ENDF", fn.Name)
		}
		i++ // consume ENDF

		fn.declared = m.CapSet(fn.Caps)
		if len(fn.Taint) > 0 {
			fn.taintSet = make(map[int]bool, len(fn.Taint))
			for _, ti := range fn.Taint {
				fn.taintSet[ti] = true
			}
		}
		m.ByName[fn.Name] = len(m.Fns)
		m.Fns = append(m.Fns, fn)
	}

	return m, nil
}

func unescape(s string) string {
	if !strings.ContainsRune(s, '\\') {
		return s
	}
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			switch s[i+1] {
			case 'n':
				b.WriteByte('\n')
			case 'r':
				b.WriteByte('\r')
			case 't':
				b.WriteByte('\t')
			case '\\':
				b.WriteByte('\\')
			default:
				b.WriteByte(s[i+1])
			}
			i++
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}
