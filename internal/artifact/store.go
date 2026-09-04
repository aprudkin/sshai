// internal/artifact/store.go
package artifact

import (
	"bytes"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  art_id TEXT UNIQUE,
  ts TEXT NOT NULL,
  host TEXT NOT NULL,
  ctx TEXT NOT NULL,
  command TEXT NOT NULL,
  key TEXT NOT NULL,
  exit INTEGER,
  transport_error TEXT NOT NULL DEFAULT '',
  transport_diagnostic TEXT NOT NULL DEFAULT '',
  local_error TEXT NOT NULL DEFAULT '',
  accepted_host_key_algorithm TEXT NOT NULL DEFAULT '',
  accepted_host_key_fingerprint TEXT NOT NULL DEFAULT '',
  bytes INTEGER NOT NULL, lines INTEGER NOT NULL,
  sha256 TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  truncated INTEGER NOT NULL DEFAULT 0, binary INTEGER NOT NULL DEFAULT 0,
  delta_base TEXT NOT NULL DEFAULT '', pruned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_key ON runs(key, id);
CREATE INDEX IF NOT EXISTS idx_runs_host_ts ON runs(host, ts);
`

// ErrPruned is returned by Get when the artifact file for an existing run
// has been pruned (retention policy) while the row remains for audit history.
var ErrPruned = errors.New("artifact pruned")

// Store holds the SQLite run-log index and the artifact directory beneath
// a single root directory.
type Store struct {
	Root string
	DB   *sql.DB
}

// OpenStore creates <root>/art/ and opens <root>/db.sqlite (WAL,
// busy_timeout 5000), creating the schema if needed.
func OpenStore(root string) (*Store, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("create store root: %w", err)
	}
	if err := ensureStoreDir(root); err != nil {
		return nil, fmt.Errorf("validate store root: %w", err)
	}
	artDir := filepath.Join(root, "art")
	if err := ensureStoreDir(artDir); err != nil {
		return nil, fmt.Errorf("create artifact dir: %w", err)
	}
	dbPath := filepath.Join(root, "db.sqlite")
	if info, err := os.Lstat(dbPath); err == nil && !info.Mode().IsRegular() {
		return nil, fmt.Errorf("refuse non-regular database destination %s", dbPath)
	} else if err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("inspect database destination: %w", err)
	}
	dsn := fmt.Sprintf("file:%s?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)", dbPath)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("create schema: %w", err)
	}
	if err := ensureRunColumns(db); err != nil {
		_ = db.Close()
		return nil, err
	}
	return &Store{Root: root, DB: db}, nil
}

func ensureStoreDir(path string) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return os.Mkdir(path, 0o700)
	}
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("refuse non-directory or symlink path %s", path)
	}
	return nil
}

// ensureRunColumns migrates databases created before optional transport
// evidence fields were persisted. The post-error recheck makes concurrent
// first-open migrations idempotent without matching driver error strings.
func ensureRunColumns(db *sql.DB) error {
	columns := []struct {
		name       string
		definition string
	}{
		{"transport_diagnostic", "transport_diagnostic TEXT NOT NULL DEFAULT ''"},
		{"local_error", "local_error TEXT NOT NULL DEFAULT ''"},
		{"accepted_host_key_algorithm", "accepted_host_key_algorithm TEXT NOT NULL DEFAULT ''"},
		{"accepted_host_key_fingerprint", "accepted_host_key_fingerprint TEXT NOT NULL DEFAULT ''"},
	}
	for _, column := range columns {
		exists, err := runColumnExists(db, column.name)
		if err != nil {
			return fmt.Errorf("inspect runs schema: %w", err)
		}
		if exists {
			continue
		}
		if _, err := db.Exec(`ALTER TABLE runs ADD COLUMN ` + column.definition); err != nil {
			exists, checkErr := runColumnExists(db, column.name)
			if checkErr == nil && exists {
				continue
			}
			return fmt.Errorf("add runs.%s: %w", column.name, err)
		}
	}
	return nil
}

func runColumnExists(db *sql.DB, want string) (bool, error) {
	rows, err := db.Query(`PRAGMA table_info(runs)`)
	if err != nil {
		return false, err
	}
	defer rows.Close()
	for rows.Next() {
		var cid, notNull, primaryKey int
		var name, columnType string
		var defaultValue any
		if err := rows.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			return false, err
		}
		if name == want {
			return true, nil
		}
	}
	if err := rows.Err(); err != nil {
		return false, err
	}
	return false, nil
}

// Save inserts a new run row and writes its artifact data to
// <root>/art/<art_id>, all within a single transaction: the row is inserted
// first, the resulting rowid is used to derive art_id ("a<rowid>"), and the
// row is updated with that art_id before the artifact file is written. The
// returned Meta has ID, Bytes, Lines and SHA256 populated.
func (s *Store) Save(m Meta, key string, data []byte) (Meta, error) {
	m.Bytes = int64(len(data))
	m.Lines = countLines(data)
	sum := sha256.Sum256(data)
	m.SHA256 = hex.EncodeToString(sum[:])

	tx, err := s.DB.Begin()
	if err != nil {
		return Meta{}, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	res, err := tx.Exec(
		`INSERT INTO runs (ts,host,ctx,command,key,exit,transport_error,transport_diagnostic,local_error,accepted_host_key_algorithm,accepted_host_key_fingerprint,bytes,lines,sha256,duration_ms,truncated,binary,delta_base)
		 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		m.Ts.UTC().Format(time.RFC3339), m.Host, m.Ctx, m.Command, key, m.Exit, m.TransportErr, m.TransportDiagnostic, m.LocalError,
		m.AcceptedHostKeyAlgorithm, m.AcceptedHostKeyFingerprint, m.Bytes, m.Lines, m.SHA256, m.DurationMs,
		boolToInt(m.Truncated), boolToInt(m.Binary), m.DeltaBase,
	)
	if err != nil {
		return Meta{}, fmt.Errorf("insert run: %w", err)
	}
	rowid, err := res.LastInsertId()
	if err != nil {
		return Meta{}, fmt.Errorf("get rowid: %w", err)
	}
	artID := fmt.Sprintf("a%d", rowid)

	if _, err := tx.Exec(`UPDATE runs SET art_id=? WHERE id=?`, artID, rowid); err != nil {
		return Meta{}, fmt.Errorf("set art_id: %w", err)
	}

	if err := writeArtifactFile(filepath.Join(s.Root, "art", artID), data); err != nil {
		return Meta{}, fmt.Errorf("write artifact file: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return Meta{}, fmt.Errorf("commit tx: %w", err)
	}

	m.ID = artID
	return m, nil
}

// Get returns the metadata and artifact file path for id. If the run
// exists but its artifact has been pruned, it returns ErrPruned.
func (s *Store) Get(id string) (Meta, string, error) {
	var m Meta
	var tsStr string
	var truncated, binary, pruned int
	row := s.DB.QueryRow(
		`SELECT art_id, ts, host, ctx, command, exit, transport_error, transport_diagnostic, local_error, accepted_host_key_algorithm, accepted_host_key_fingerprint, bytes, lines, sha256, duration_ms, truncated, binary, delta_base, pruned
		 FROM runs WHERE art_id=?`, id)
	if err := row.Scan(&m.ID, &tsStr, &m.Host, &m.Ctx, &m.Command, &m.Exit, &m.TransportErr, &m.TransportDiagnostic, &m.LocalError,
		&m.AcceptedHostKeyAlgorithm, &m.AcceptedHostKeyFingerprint, &m.Bytes, &m.Lines, &m.SHA256, &m.DurationMs,
		&truncated, &binary, &m.DeltaBase, &pruned); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Meta{}, "", fmt.Errorf("run %s: not found", id)
		}
		return Meta{}, "", fmt.Errorf("get run %s: %w", id, err)
	}
	m.Truncated = truncated != 0
	m.Binary = binary != 0
	ts, err := time.Parse(time.RFC3339, tsStr)
	if err != nil {
		return Meta{}, "", fmt.Errorf("parse ts for run %s: %w", id, err)
	}
	m.Ts = ts

	if pruned != 0 {
		// The row is retained for audit history even after its artifact
		// file is pruned, so callers (e.g. `log`, `gc`) still get the
		// metadata — only the path is withheld, alongside the error.
		return m, "", fmt.Errorf("run %s: %w", id, ErrPruned)
	}

	return m, filepath.Join(s.Root, "art", m.ID), nil
}

// LastByKey returns the most recent non-pruned run with the given key, for
// use as the base of a --delta comparison. ok is false if no such run exists.
func (s *Store) LastByKey(key string) (Meta, bool, error) {
	var m Meta
	var tsStr string
	var truncated, binary int
	row := s.DB.QueryRow(
		`SELECT art_id, ts, host, ctx, command, exit, transport_error, transport_diagnostic, local_error, accepted_host_key_algorithm, accepted_host_key_fingerprint, bytes, lines, sha256, duration_ms, truncated, binary, delta_base
		 FROM runs WHERE key=? AND pruned=0 ORDER BY id DESC LIMIT 1`, key)
	if err := row.Scan(&m.ID, &tsStr, &m.Host, &m.Ctx, &m.Command, &m.Exit, &m.TransportErr, &m.TransportDiagnostic, &m.LocalError,
		&m.AcceptedHostKeyAlgorithm, &m.AcceptedHostKeyFingerprint, &m.Bytes, &m.Lines, &m.SHA256, &m.DurationMs,
		&truncated, &binary, &m.DeltaBase); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Meta{}, false, nil
		}
		return Meta{}, false, fmt.Errorf("last by key %s: %w", key, err)
	}
	m.Truncated = truncated != 0
	m.Binary = binary != 0
	ts, err := time.Parse(time.RFC3339, tsStr)
	if err != nil {
		return Meta{}, false, fmt.Errorf("parse ts for key %s: %w", key, err)
	}
	m.Ts = ts
	return m, true, nil
}

// Close closes the underlying database connection.
func (s *Store) Close() error {
	return s.DB.Close()
}

// countLines counts newline-separated lines in data: the number of '\n'
// bytes, plus one more if data is non-empty and does not already end with
// a newline (a final unterminated segment still counts as a line).
func countLines(data []byte) int64 {
	n := int64(bytes.Count(data, []byte("\n")))
	if len(data) > 0 && data[len(data)-1] != '\n' {
		n++
	}
	return n
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// writeArtifactFile writes data to path atomically through a private sibling
// temporary file. It refuses an existing destination, including symlinks and
// non-regular files, before publishing the completed artifact by rename.
func writeArtifactFile(path string, data []byte) error {
	if info, err := os.Lstat(path); err == nil {
		return fmt.Errorf("refuse existing artifact destination %s (%s)", path, info.Mode().Type())
	} else if !os.IsNotExist(err) {
		return err
	}

	tmp, err := os.CreateTemp(filepath.Dir(path), ".artifact-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return err
	}
	return nil
}
