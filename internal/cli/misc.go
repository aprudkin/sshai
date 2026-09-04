// internal/cli/misc.go
package cli

import (
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/config"
	"github.com/aprudkin/sshai/internal/runlog"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/kevinburke/ssh_config"
)

// ---- log ----

// logCommandClipRunes is the brief's exact clip width for a log line's
// command field (its own example line ends "journalctl -u postgres…").
const logCommandClipRunes = 60

// Log implements `sshai log [--host H] [--since T] [--grep P] [--limit N]`:
// it searches the run-log index (runlog.Search) and prints one line per
// matching run, newest-first, in the brief's exact shape.
func Log(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("log", flag.ContinueOnError)
	fs.SetOutput(stderr)
	host := fs.String("host", "", "filter by host")
	grep := fs.String("grep", "", "filter: substring match on command text")
	since := fs.String("since", "", `only runs at or after this time: a duration ("2h", "30m", "7d") or a date ("2026-08-01")`)
	limit := fs.Int("limit", 20, "maximum number of runs to print")

	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(stderr, "log: usage: sshai log [--host H] [--since T] [--grep P] [--limit N]")
		return exitUsage
	}

	var sinceTime time.Time
	if *since != "" {
		t, err := parseSince(*since, time.Now())
		if err != nil {
			fmt.Fprintf(stderr, "log: %v\n", err)
			return exitUsage
		}
		sinceTime = t
	}

	store, err := openQueryStore()
	if err != nil {
		fmt.Fprintf(stderr, "log: %v\n", err)
		return exitUsage
	}
	defer store.Close()

	runs, err := runlog.Search(store.DB, *host, *grep, sinceTime, *limit)
	if err != nil {
		fmt.Fprintf(stderr, "log: %v\n", err)
		return exitUsage
	}

	for _, m := range runs {
		fmt.Fprintln(stdout, formatLogLine(m))
	}
	return 0
}

// formatLogLine renders one run the way `sshai log` prints it — the
// brief's exact shape: "<id>  <ts>  <host>  exit=<n>|transport-error=<x>
// <duration>  <command>", with the command clipped to
// logCommandClipRunes runes. Body-file runs store Meta.Command as
// "body:<hash> <preview>" (Task 11's contract, already redacted by
// runlog.Preview at write time) — the clip here applies to whatever the
// column holds, no special-casing needed.
func formatLogLine(m artifact.Meta) string {
	status := fmt.Sprintf("exit=%d", m.Exit)
	if m.TransportErr != "" {
		status = "transport-error=" + m.TransportErr
	}
	return fmt.Sprintf("%s  %s  %s  %s  %s  %s",
		m.ID, m.Ts.UTC().Format(time.RFC3339), m.Host, status,
		artifact.HumanDuration(m.DurationMs), clipRunes(m.Command, logCommandClipRunes))
}

// clipRunes returns s unchanged when it holds at most n runes, else the
// first n runes followed by a single "…" marker.
func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}

// parseSince parses --since's value relative to now: a manually-handled
// "<N>d" day suffix first (time.ParseDuration has no day unit), else
// anything time.ParseDuration itself accepts ("2h", "30m"), else an
// absolute date ("2006-01-02").
func parseSince(s string, now time.Time) (time.Time, error) {
	if d, ok := parseDaySuffix(s); ok {
		return now.Add(-d), nil
	}
	if d, err := time.ParseDuration(s); err == nil {
		return now.Add(-d), nil
	}
	if t, err := time.Parse("2006-01-02", s); err == nil {
		return t, nil
	}
	return time.Time{}, fmt.Errorf(`invalid --since %q: want a duration ("2h", "30m", "7d") or a date ("2026-08-01")`, s)
}

// parseDaySuffix recognizes "<N>d" (e.g. "7d"), the one duration unit
// time.ParseDuration does not support natively.
func parseDaySuffix(s string) (time.Duration, bool) {
	if len(s) < 2 || s[len(s)-1] != 'd' {
		return 0, false
	}
	n, err := strconv.Atoi(s[:len(s)-1])
	if err != nil || n < 0 {
		return 0, false
	}
	return time.Duration(n) * 24 * time.Hour, true
}

// ---- hosts ----

// Hosts implements `sshai hosts`: the union of every non-wildcard Host
// pattern in $HOME/.ssh/config and every key in config.Hosts, one line
// each, sorted by name for deterministic output.
func Hosts(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("hosts", flag.ContinueOnError)
	fs.SetOutput(stderr)
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(stderr, "hosts: usage: sshai hosts")
		return exitUsage
	}

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintf(stderr, "hosts: load config: %v\n", err)
		return exitUsage
	}

	sshHosts, err := sshConfigHosts()
	if err != nil {
		fmt.Fprintf(stderr, "hosts: %v\n", err)
		return exitUsage
	}

	seen := make(map[string]bool, len(sshHosts)+len(cfg.Hosts))
	for _, h := range sshHosts {
		seen[h] = true
	}
	for h := range cfg.Hosts {
		seen[h] = true
	}
	names := make([]string, 0, len(seen))
	for h := range seen {
		names = append(names, h)
	}
	sort.Strings(names)

	for _, h := range names {
		fmt.Fprintf(stdout, "%s  os=%s  readonly=%t\n", h, hostOS(cfg, h), cfg.Hosts[h].Readonly)
	}
	return 0
}

// hostOS resolves a host's displayed OS per the brief's fallback order:
// cached session facts first (ground truth from an actual probe), else
// config.toml's [hosts.X] os, else "-" when neither is known. A LoadFacts
// error (e.g. a corrupt facts.json) is treated the same as "no facts" —
// hosts is a best-effort inventory listing, not a place to abort on a
// single host's cache corruption.
func hostOS(cfg config.Config, host string) string {
	if facts, ok, err := session.LoadFacts(cfg.Root, host); err == nil && ok && facts.OS != "" {
		return facts.OS
	}
	if hc, ok := cfg.Hosts[host]; ok && hc.OS != "" {
		return hc.OS
	}
	return "-"
}

// sshConfigHosts parses $HOME/.ssh/config via ssh_config.Decode on an
// explicitly opened file — never ssh_config.Get/UserSettings' own
// config-file lookup, which resolves $HOME internally via its own
// unexported homedir() and so can't be redirected from a test. Returns
// every non-wildcard Host pattern across every Host block, including the
// file's own implicit leading "Host *" (itself a "*" pattern, so it is
// filtered by the very same rule as any real wildcard entry like "Host
// *.internal" — no special-casing needed). A missing ~/.ssh/config is not
// an error: it just means no ssh_config-based hosts to report.
func sshConfigHosts() ([]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("resolve home dir: %w", err)
	}
	path := filepath.Join(home, ".ssh", "config")
	f, err := os.Open(path) // #nosec G304 -- path is the fixed .ssh/config child of the OS-reported home directory.
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()

	parsed, err := ssh_config.Decode(f)
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}

	var hosts []string
	for _, h := range parsed.Hosts {
		for _, p := range h.Patterns {
			s := p.String()
			if strings.ContainsAny(s, "*?") {
				continue
			}
			hosts = append(hosts, s)
		}
	}
	return hosts, nil
}

// ---- gc ----

// tmpOrphanAge is how old a Store.Save ".tmp" file (see writeArtifactFile
// in internal/artifact/store.go: write-to-tmp, then rename) must be
// before gc treats it as orphaned by a crashed Save rather than a
// concurrent Save's own in-flight write. A normal Save's tmp file exists
// only for the duration of one os.WriteFile call — sub-second — so this
// margin is deliberately generous: it must never be mistaken for, and
// race, a real writer.
const tmpOrphanAge = 10 * time.Minute

// Gc implements `sshai gc`: prunes artifacts past config's retention
// policy (age and size cap) plus any orphaned Save .tmp file, and reports
// what was freed.
func Gc(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("gc", flag.ContinueOnError)
	fs.SetOutput(stderr)
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(stderr, "gc: usage: sshai gc")
		return exitUsage
	}

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintf(stderr, "gc: load config: %v\n", err)
		return exitUsage
	}
	store, err := artifact.OpenStore(cfg.Root)
	if err != nil {
		fmt.Fprintf(stderr, "gc: open store: %v\n", err)
		return exitUsage
	}
	defer store.Close()

	// Standalone `sshai gc` protects nothing: a user invoking it explicitly
	// gets the brief's literal contract, "oldest first until under", with
	// no exemptions — unlike run.go's opportunistic maybeGC call, which
	// passes the ids this very invocation just wrote (see gcStore's and
	// maybeGC's doc comments for why that call needs a non-empty set and
	// this one deliberately does not).
	pruned, freed, err := gcStore(store, retentionCutoff(cfg.RetentionDays, time.Now()), cfg.RetentionMaxBytes, nil)
	if err != nil {
		fmt.Fprintf(stderr, "gc: %v\n", err)
		return exitUsage
	}

	fmt.Fprintf(stdout, "pruned %d artifacts, freed %s\n", pruned, artifact.HumanBytes(freed))
	return 0
}

// retentionCutoff turns a RetentionDays value into an absolute cutoff
// relative to now. Shared by Gc and run.go's opportunistic maybeGC call so
// both compute the same cutoff the same way.
func retentionCutoff(retentionDays int, now time.Time) time.Time {
	return now.AddDate(0, 0, -retentionDays)
}

// gcStore prunes artifact files for rows whose ts is before cutoff (a
// zero cutoff disables age-based pruning entirely — Search's own "no
// lower bound" convention), OR — evaluated afterward, against whatever
// remains — whose total size still exceeds maxBytes (maxBytes <= 0
// disables the size cap), removed oldest-first until under — except any
// row whose id is a key in protect (nil/empty is valid and protects
// nothing: reading a missing key from a nil map is safe in Go). A
// protected row is exempt from BOTH passes, not just the size-based one:
// deliberately so, rather than protecting only against the size cap —
// see maybeGC's doc comment in run.go for the scenario (a
// RetentionDays=0 misconfiguration) that a size-only exemption would
// still miss. protect is how run.go's opportunistic maybeGC call keeps
// itself from pruning the artifact(s) it just wrote; standalone `sshai
// gc` (Gc, above) always passes nil, restoring the literal "oldest first
// until under, no exceptions" contract for the explicit command. Rows are
// never deleted, only their artifact file is removed and pruned set to
// 1, keeping the row for audit history — the same contract Store.Get's
// ErrPruned already documents (see store.go's Get). It also removes
// orphaned Save .tmp files older than tmpOrphanAge (see that constant's
// doc comment), counting their freed bytes into the same total.
func gcStore(store *artifact.Store, cutoff time.Time, maxBytes int64, protect map[string]bool) (pruned int, freed int64, err error) {
	// Ordered by ts (not id): both pruning passes below are age-based —
	// pass 1 directly, pass 2 ("oldest first") too — so this must be the
	// same clock pass 1 already compares against cutoff on, not
	// insertion order. The two normally coincide (Save always stamps
	// Ts=time.Now()), but nothing guarantees it — a manually-adjusted or
	// backdated row must still be treated as old by size-based pruning
	// too. id is the tiebreaker for rows sharing a timestamp. Lexical
	// ordering on the ts column is safe because Store.Save always writes
	// it as m.Ts.UTC().Format(time.RFC3339) — fixed-width and always
	// "Z"-suffixed — so lexical order equals chronological order for
	// every row this codebase's own writer ever produces.
	rows, err := store.DB.Query(`SELECT art_id, ts, bytes FROM runs WHERE pruned=0 ORDER BY ts ASC, id ASC`)
	if err != nil {
		return 0, 0, fmt.Errorf("gc: query runs: %w", err)
	}

	type candidate struct {
		id    string
		ts    time.Time
		bytes int64
	}
	var all []candidate
	for rows.Next() {
		var c candidate
		var tsStr string
		if scanErr := rows.Scan(&c.id, &tsStr, &c.bytes); scanErr != nil {
			_ = rows.Close()
			return 0, 0, fmt.Errorf("gc: scan run: %w", scanErr)
		}
		ts, parseErr := time.Parse(time.RFC3339, tsStr)
		if parseErr != nil {
			_ = rows.Close()
			return 0, 0, fmt.Errorf("gc: parse ts: %w", parseErr)
		}
		c.ts = ts
		all = append(all, c)
	}
	rowsErr := rows.Err()
	_ = rows.Close()
	if rowsErr != nil {
		return 0, 0, fmt.Errorf("gc: query runs: %w", rowsErr)
	}

	// Pass 1: age. Rows older than cutoff are pruned unconditionally,
	// except a protected row, which counts toward remaining (pass 2's
	// input) exactly like any other live row but is never itself pruned.
	toPrune := make(map[string]bool)
	var remaining int64
	for _, c := range all {
		if protect[c.id] {
			remaining += c.bytes
			continue
		}
		if !cutoff.IsZero() && c.ts.Before(cutoff) {
			toPrune[c.id] = true
			continue
		}
		remaining += c.bytes
	}

	// Pass 2: size cap, oldest-first, only over whatever pass 1 left
	// standing, protected rows excepted. This can leave the store over
	// maxBytes when every unprotected row has already been pruned and
	// only protected ones remain — an accepted trade-off (size-based
	// pruning is a best-effort cap, not a hard guarantee) rather than a
	// bug: see gcStore's own doc comment on why protect exists at all.
	if maxBytes > 0 && remaining > maxBytes {
		for _, c := range all {
			if toPrune[c.id] || protect[c.id] || remaining <= maxBytes {
				continue
			}
			toPrune[c.id] = true
			remaining -= c.bytes
		}
	}

	for _, c := range all {
		if !toPrune[c.id] {
			continue
		}
		path := filepath.Join(store.Root, "art", c.id)
		if rmErr := os.Remove(path); rmErr != nil && !os.IsNotExist(rmErr) {
			return pruned, freed, fmt.Errorf("gc: remove artifact %s: %w", c.id, rmErr)
		}
		if _, execErr := store.DB.Exec(`UPDATE runs SET pruned=1 WHERE art_id=?`, c.id); execErr != nil {
			return pruned, freed, fmt.Errorf("gc: mark %s pruned: %w", c.id, execErr)
		}
		pruned++
		freed += c.bytes
	}

	tmpFreed, tmpErr := cleanOrphanedTmp(store.Root)
	freed += tmpFreed
	if tmpErr != nil {
		return pruned, freed, fmt.Errorf("gc: clean orphaned tmp files: %w", tmpErr)
	}

	return pruned, freed, nil
}

// cleanOrphanedTmp removes any "*.tmp" file directly under <root>/art/
// older than tmpOrphanAge — the crash-orphan case for Store.Save's
// write-then-rename mechanic (writeArtifactFile in
// internal/artifact/store.go): a process that dies between the
// os.WriteFile and the os.Rename (or whose Rename fails and whose own
// best-effort os.Remove(tmp) cleanup also fails) leaves a stray
// "<art_id>.tmp" behind. Save only reaches tx.Commit() after
// writeArtifactFile returns successfully, so a surviving tmp file's
// transaction was always rolled back — no committed row ever points at
// it. (Note: SQLite AUTOINCREMENT does NOT guarantee that art_id will
// never be reused by a later, successful Save — verified empirically, a
// rolled-back insert's rowid is reused by the very next insert when
// nothing else has committed in between. That is not a hazard here
// either way: os.WriteFile always truncates, so whether gc removes a
// stale tmp first or a reused Save just overwrites it directly, the
// file's final content is identical — this cleanup is purely reclaiming
// disk space, never a correctness precondition for a future write.) The
// age guard is what makes it safe to run concurrently with a live Save: a
// real in-flight tmp file is only ever open for the duration of one
// os.WriteFile call — well under a second — so it can never be mistaken
// for stale garbage and removed out from under its writer.
func cleanOrphanedTmp(root string) (freed int64, err error) {
	artDir := filepath.Join(root, "art")
	entries, err := os.ReadDir(artDir)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, fmt.Errorf("read %s: %w", artDir, err)
	}

	cutoff := time.Now().Add(-tmpOrphanAge)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".tmp") {
			continue
		}
		info, infoErr := e.Info()
		if infoErr != nil {
			// The file vanished between ReadDir and Info (e.g. its own
			// Save just completed the rename that removes the .tmp name) —
			// not a gc failure, just nothing left to remove.
			continue
		}
		if info.ModTime().After(cutoff) {
			continue
		}
		path := filepath.Join(artDir, e.Name())
		if rmErr := os.Remove(path); rmErr != nil {
			if os.IsNotExist(rmErr) {
				continue
			}
			return freed, fmt.Errorf("remove orphaned tmp %s: %w", path, rmErr)
		}
		freed += info.Size()
	}
	return freed, nil
}
