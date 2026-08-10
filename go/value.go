// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright 2026 Nandu Aravindakshan

package main

import (
	"fmt"
	"sort"
	"strings"
)

// Kind is the runtime type of a Value.
type Kind uint8

const (
	KNil Kind = iota
	KNum
	KStr
	KBool
	KList
	KRec
)

// Value is a Humbaba runtime value.
//
// Taint is a field rather than a wrapper type because every operation has to
// propagate it, and a wrapper would mean allocating on every arithmetic step.
// The rule is simple and absolute: if any input to an operation is tainted,
// the result is tainted.
type Value struct {
	Kind  Kind
	Num   float64
	Str   string
	Bool  bool
	List  []Value
	Rec   map[string]Value
	Taint bool
}

func Nil() Value           { return Value{Kind: KNil} }
func Num(f float64) Value  { return Value{Kind: KNum, Num: f} }
func Str(s string) Value   { return Value{Kind: KStr, Str: s} }
func Bool(b bool) Value    { return Value{Kind: KBool, Bool: b} }
func List(v []Value) Value { return Value{Kind: KList, List: v} }
func Rec(m map[string]Value) Value {
	return Value{Kind: KRec, Rec: m}
}

// Tainted returns v marked tainted. Taint never comes off except via UNTAINT,
// which the compiler emits only for an explicit written-reason unwrap.
func Tainted(v Value) Value {
	v.Taint = true
	return v
}

// Truthy follows the interpreter: zero and empty are false.
func (v Value) Truthy() bool {
	switch v.Kind {
	case KNil:
		return false
	case KNum:
		return v.Num != 0
	case KStr:
		return v.Str != ""
	case KBool:
		return v.Bool
	case KList:
		return len(v.List) > 0
	case KRec:
		return len(v.Rec) > 0
	}
	return false
}

// Display formats a value the way print does. Numbers that are whole print
// without a decimal point, so 5.0 shows as 5 and matches the interpreter.
func (v Value) Display() string {
	switch v.Kind {
	case KNil:
		return "nil"
	case KBool:
		if v.Bool {
			return "true"
		}
		return "false"
	case KNum:
		if v.Num == float64(int64(v.Num)) {
			return fmt.Sprintf("%d", int64(v.Num))
		}
		return fmt.Sprintf("%g", v.Num)
	case KStr:
		return v.Str
	case KList:
		parts := make([]string, len(v.List))
		for i, item := range v.List {
			parts[i] = item.Display()
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case KRec:
		keys := make([]string, 0, len(v.Rec))
		for k := range v.Rec {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = k + ": " + v.Rec[k].Display()
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return "?"
}

// Equal compares by value. Taint is provenance, not identity, so it is not
// part of equality — otherwise a tainted 1 would differ from a clean 1.
func (v Value) Equal(o Value) bool {
	if v.Kind != o.Kind {
		return false
	}
	switch v.Kind {
	case KNil:
		return true
	case KNum:
		return v.Num == o.Num
	case KStr:
		return v.Str == o.Str
	case KBool:
		return v.Bool == o.Bool
	case KList:
		if len(v.List) != len(o.List) {
			return false
		}
		for i := range v.List {
			if !v.List[i].Equal(o.List[i]) {
				return false
			}
		}
		return true
	case KRec:
		if len(v.Rec) != len(o.Rec) {
			return false
		}
		for k, a := range v.Rec {
			b, ok := o.Rec[k]
			if !ok || !a.Equal(b) {
				return false
			}
		}
		return true
	}
	return false
}
