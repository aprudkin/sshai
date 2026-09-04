// internal/runlog/query.go
package runlog

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
)

// defaultSearchLimit is Search's own default row cap, applied whenever the
// caller passes limit <= 0 — `sshai log`'s own --limit flag default (20),
// per the task brief.
const defaultSearchLimit = 20

// Search returns runs matching the given filters, newest-first (SQLite's
// own autoincrement id order, matching Store.LastByKey's own ordering — ts
// alone is not a safe sort key since two rows can share a timestamp).
// host and grep are optional: an empty string means "no filter" for each.
// grep is a case-sensitive substring match on the command column (LIKE
// "%grep%"), with SQL LIKE's own wildcard characters ('%', '_') in grep
// escaped first so a literal '%' or '_' in a search term is never
// mistaken for a wildcard. since is optional: a zero time.Time means "no
// lower bound", otherwise only rows with ts >= since are returned. limit
// <= 0 defaults to defaultSearchLimit.
func Search(db *sql.DB, host, grep string, since time.Time, limit int) ([]artifact.Meta, error) {
	if limit <= 0 {
		limit = defaultSearchLimit
	}

	var conds []string
	var args []any
	if host != "" {
		conds = append(conds, "host = ?")
		args = append(args, host)
	}
	if grep != "" {
		conds = append(conds, "command LIKE ? ESCAPE '\\'")
		args = append(args, "%"+escapeLike(grep)+"%")
	}
	if !since.IsZero() {
		conds = append(conds, "ts >= ?")
		args = append(args, since.UTC().Format(time.RFC3339))
	}

	query := `SELECT art_id, ts, host, ctx, command, exit, transport_error, transport_diagnostic, setup_error, setup_diagnostic, local_error, bytes, lines, sha256, duration_ms, truncated, binary, delta_base FROM runs`
	if len(conds) > 0 {
		query += " WHERE " + strings.Join(conds, " AND ") // #nosec G202 -- conds contains only fixed clauses; values remain SQL parameters.
	}
	query += " ORDER BY id DESC LIMIT ?"
	args = append(args, limit)

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("search runs: %w", err)
	}
	defer rows.Close()

	var out []artifact.Meta
	for rows.Next() {
		var m artifact.Meta
		var tsStr string
		var truncated, binary int
		if err := rows.Scan(&m.ID, &tsStr, &m.Host, &m.Ctx, &m.Command, &m.Exit, &m.TransportErr, &m.TransportDiagnostic, &m.SetupErr, &m.SetupDiagnostic, &m.LocalError,
			&m.Bytes, &m.Lines, &m.SHA256, &m.DurationMs, &truncated, &binary, &m.DeltaBase); err != nil {
			return nil, fmt.Errorf("scan run: %w", err)
		}
		m.Truncated = truncated != 0
		m.Binary = binary != 0
		ts, err := time.Parse(time.RFC3339, tsStr)
		if err != nil {
			return nil, fmt.Errorf("parse ts: %w", err)
		}
		m.Ts = ts
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("search runs: %w", err)
	}
	return out, nil
}

// escapeLike backslash-escapes SQL LIKE's own wildcard characters ('%',
// '_') in s, plus a literal backslash itself, so grep's substring is
// matched literally under "LIKE ? ESCAPE '\\'" rather than partly
// interpreted as a pattern.
func escapeLike(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, "%", `\%`)
	s = strings.ReplaceAll(s, "_", `\_`)
	return s
}
