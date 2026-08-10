// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright 2026 Nandu Aravindakshan

package main

import (
	"crypto/sha256"
	"encoding/binary"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"
)

// MockModel is a deterministic stand-in so the host can be exercised without
// a network. It is keyed on the prompt inputs, so the same program produces
// the same values on every run.
//
// It deliberately does not reproduce the Python MockModel's cassette, chaos
// and injection-simulation behaviour. Equivalence with the Python host is
// claimed for enforcement — capability refusals, taint refusals, budget
// stops — not for generated field values.
type MockModel struct {
	Latency time.Duration
	Cost    float64
}

func (m MockModel) Generate(model, system, user string, schema []string) (Value, float64, error) {
	if m.Latency > 0 {
		time.Sleep(m.Latency)
	}
	sum := sha256.Sum256([]byte(model + "\x00" + system + "\x00" + user))
	seed := binary.BigEndian.Uint64(sum[:8])

	rec := make(map[string]Value, len(schema))
	for i, f := range schema {
		parts := strings.SplitN(f, ":", 2)
		name := parts[0]
		typ := "string"
		if len(parts) > 1 {
			typ = strings.TrimSuffix(parts[1], "?")
		}
		h := seed + uint64(i)*1103515245
		switch typ {
		case "number":
			rec[name] = Num(float64(h % 10000))
		case "bool":
			rec[name] = Bool(h%2 == 0)
		default:
			rec[name] = Str(fmt.Sprintf("%s-%04x", name, h%0xffff))
		}
	}
	cost := m.Cost
	if cost == 0 {
		cost = 0.0055
	}
	return Rec(rec), cost, nil
}

func main() {
	var (
		phases  = flag.Bool("phases", false, "report load and run time separately")
		journal = flag.String("journal", "", "directory for the durable-execution journal")
		entry   = flag.String("entry", "main", "function to start at")
		timeN   = flag.Int("time", 0, "run N times and report the best wall time")
		quiet   = flag.Bool("quiet", false, "suppress the summary line")
		latency = flag.Duration("latency", 0, "simulated model latency")
	)
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "usage: humbaba-runtime [flags] program.hbx\n\n")
		flag.PrintDefaults()
	}
	flag.Parse()

	if flag.NArg() < 1 {
		flag.Usage()
		os.Exit(2)
	}

	tLoad := time.Now()
	mod, err := Load(flag.Arg(0))
	if err != nil {
		fmt.Fprintln(os.Stderr, "humbaba-runtime:", err)
		os.Exit(1)
	}
	loadTime := time.Since(tLoad)

	if *phases {
		vm := NewVM(mod, MockModel{}, func(string) {})
		tRun := time.Now()
		if _, err := vm.Run(*entry); err != nil {
			fmt.Fprintln(os.Stderr, "humbaba-runtime:", err)
			os.Exit(1)
		}
		runTime := time.Since(tRun)
		fmt.Fprintf(os.Stderr, "  load %.3f ms\n  run  %.3f ms\n"+
			"  (anything not accounted for above is process start:\n"+
			"   Go runtime init, dynamic linking, and the OS exec itself)\n",
			float64(loadTime.Microseconds())/1000,
			float64(runTime.Microseconds())/1000)
		return
	}

	model := MockModel{Latency: *latency}

	if *timeN > 0 {
		best := time.Duration(1<<62 - 1)
		for i := 0; i < *timeN; i++ {
			vm := NewVM(mod, model, func(string) {})
			t0 := time.Now()
			if _, err := vm.Run(*entry); err != nil {
				fmt.Fprintln(os.Stderr, "humbaba-runtime:", err)
				os.Exit(1)
			}
			if d := time.Since(t0); d < best {
				best = d
			}
		}
		fmt.Fprintf(os.Stderr, "%.6f\n", best.Seconds())
		return
	}

	vm := NewVM(mod, model, nil)

	if *journal != "" {
		j, jerr := OpenJournal(*journal, *entry)
		if jerr != nil {
			fmt.Fprintln(os.Stderr, "humbaba-runtime: journal:", jerr)
			os.Exit(1)
		}
		vm.Journal = j
		if j.Restored {
			fmt.Fprintf(os.Stderr, "  resuming %s() from journal (%d step(s) already done)\n",
				*entry, len(j.entries))
		}
	}

	_, err = vm.Run(*entry)

	if err != nil {
		fmt.Fprintln(os.Stderr, "\n  runtime error:", err)
		os.Exit(1)
	}
	if !*quiet && vm.Gens > 0 {
		fmt.Fprintf(os.Stderr,
			"\n  %d gen call(s) · spent £%.4f · %d replayed · %d blocked\n",
			vm.Gens, vm.Spent, vm.Replayed, vm.Blocked)
	}
}
