// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright 2026 Nandu Aravindakshan

package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// The durable-execution journal.
//
// A process that dies mid-run loses its work and, worse, its record of what it
// already paid for. The journal is an append-only list of completed steps: on
// restart, a step with an entry returns that value without executing, and the
// spend recorded before the crash is restored so a resumed run cannot quietly
// start counting from zero.
//
// The file format matches the Python host's: one JSON object per line, in a
// file named for a run id derived from the entry point. Writes are fsync'd,
// because a journal that survives a clean exit but not a power cut is not a
// journal.

// journalEntry is one line of the file.
type journalEntry struct {
	Op    string      `json:"op"`
	Label string      `json:"label,omitempty"`
	Value interface{} `json:"value,omitempty"`
	Spent float64     `json:"spent,omitempty"`
}

// Journal is the append-only record for one run.
type Journal struct {
	Path     string
	RunID    string
	Spent    float64
	Restored bool

	entries  map[string]journalEntry
	replayed map[string]bool
}

// OpenJournal loads any existing record for this entry point, or starts one.
func OpenJournal(dir, fnName string) (*Journal, error) {
	if dir == "" {
		dir = filepath.Join(os.TempDir(), "humbaba-journal")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}

	// The run id keys on the entry point, so retrying the same logical work
	// resumes rather than starting a second parallel record.
	sum := sha256.Sum256([]byte(fnName + "\x00[]"))
	runID := hex.EncodeToString(sum[:])[:16]

	j := &Journal{
		Path:     filepath.Join(dir, runID+".jsonl"),
		RunID:    runID,
		entries:  map[string]journalEntry{},
		replayed: map[string]bool{},
	}

	f, err := os.Open(j.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return j, nil
		}
		return nil, err
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 4<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var rec journalEntry
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			// A crash mid-write leaves a torn final line. Everything before
			// it is still valid, so stop here rather than discarding a
			// journal because its last byte is missing.
			break
		}
		if rec.Op == "done" {
			// The run completed. A rerun is new work, not a resumption.
			j.entries = map[string]journalEntry{}
			j.Spent = 0
			continue
		}
		j.entries[rec.Label] = rec
		if rec.Spent > j.Spent {
			j.Spent = rec.Spent
		}
	}
	j.Restored = len(j.entries) > 0
	return j, nil
}

// Replay returns the recorded value for a step, and whether there was one.
//
// A label appearing twice in a single execution makes replay ambiguous — the
// second occurrence would be handed the first one's result — so it is an
// error rather than a silent mismatch.
func (j *Journal) Replay(label string) (Value, bool, error) {
	rec, ok := j.entries[label]
	if !ok {
		return Nil(), false, nil
	}
	if j.replayed[label] {
		return Nil(), false, &RuntimeError{Msg: fmt.Sprintf(
			"step %q ran twice in one execution. Step labels must be unique "+
				"within a durable function, or replay is ambiguous.", label)}
	}
	j.replayed[label] = true
	return decodeJSON(rec.Value), true, nil
}

// Record appends a completed step and flushes it to stable storage.
func (j *Journal) Record(label string, v Value, spent float64) error {
	rec := journalEntry{Op: "step", Label: label, Value: encodeValue(v),
		Spent: round6(spent)}
	line, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(j.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write(append(line, '\n')); err != nil {
		return err
	}
	// fsync, not just flush. The point of the journal is surviving the case
	// where the process does not get to run its exit path.
	if err := f.Sync(); err != nil {
		return err
	}
	j.entries[label] = rec
	return nil
}

// Finish marks the run complete, so the next run starts fresh.
func (j *Journal) Finish() error {
	line, err := json.Marshal(journalEntry{Op: "done"})
	if err != nil {
		return err
	}
	f, err := os.OpenFile(j.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}

func round6(f float64) float64 {
	return float64(int64(f*1e6+0.5)) / 1e6
}

// encodeValue converts a runtime value to something json.Marshal accepts.
// Records carry a marker so they come back as records rather than as plain
// objects, matching the Python host's encoding.
func encodeValue(v Value) interface{} {
	switch v.Kind {
	case KNil:
		return nil
	case KNum:
		return v.Num
	case KStr:
		return v.Str
	case KBool:
		return v.Bool
	case KList:
		out := make([]interface{}, len(v.List))
		for i, item := range v.List {
			out[i] = encodeValue(item)
		}
		return out
	case KRec:
		fields := make(map[string]interface{}, len(v.Rec))
		for k, item := range v.Rec {
			fields[k] = encodeValue(item)
		}
		return map[string]interface{}{"__obj__": "", "fields": fields}
	}
	return nil
}

func decodeJSON(x interface{}) Value {
	switch t := x.(type) {
	case nil:
		return Nil()
	case bool:
		return Bool(t)
	case float64:
		return Num(t)
	case string:
		return Str(t)
	case []interface{}:
		out := make([]Value, len(t))
		for i, item := range t {
			out[i] = decodeJSON(item)
		}
		return List(out)
	case map[string]interface{}:
		if fields, ok := t["fields"].(map[string]interface{}); ok {
			if _, marked := t["__obj__"]; marked {
				rec := make(map[string]Value, len(fields))
				for k, item := range fields {
					rec[k] = decodeJSON(item)
				}
				return Rec(rec)
			}
		}
		rec := make(map[string]Value, len(t))
		for k, item := range t {
			rec[k] = decodeJSON(item)
		}
		return Rec(rec)
	}
	return Nil()
}
