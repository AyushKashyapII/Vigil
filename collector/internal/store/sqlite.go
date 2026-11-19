package store

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"time"

	_ "modernc.org/sqlite"

	"github.com/ayushkashyap/vigil/collector/internal/rules"
)

// Store persists Findings. Defined as an interface so the backing
// implementation can change later (e.g. to a Postgres-backed store for a
// multi-collector deployment) without touching any calling code.
type Store interface {
	SaveFinding(ctx context.Context, f rules.Finding) error
	SaveFindings(ctx context.Context, fs []rules.Finding, traceparent string) ([]int64, error)
	Close() error
}

// SQLiteStore is a Store backed by a local SQLite file. This is a
// deliberate choice, not just a placeholder: a single-binary agent
// shipping its own embedded storage (no server process, no credentials to
// provision) is a legitimate production pattern for a per-machine agent,
// not only a shortcut for local development.
type SQLiteStore struct {
	db *sql.DB
}

// Open opens (creating if necessary) the SQLite store at the path in
// COLLECTOR_STORE_PATH, falling back to "vigil.db" in the current
// directory for local dev.
func Open() (*SQLiteStore, error) {
	path := os.Getenv("COLLECTOR_STORE_PATH")
	if path == "" {
		path = "vigil.db"
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("opening sqlite store: %w", err)
	}

	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("pinging sqlite store: %w", err)
	}

	// WAL mode decouples our writes from concurrent readers (brain, or
	// anything else reading the store): without it, SQLite's default
	// rollback-journal mode blocks readers for the duration of every
	// write, which surfaced as real "database is locked" errors.
	if _, err := db.Exec(`PRAGMA journal_mode=WAL;`); err != nil {
		db.Close()
		return nil, fmt.Errorf("enabling WAL mode: %w", err)
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS findings (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			recorded_at TIMESTAMP NOT NULL,
			rule TEXT NOT NULL,
			subject TEXT NOT NULL,
			query TEXT NOT NULL,
			detail TEXT NOT NULL
		)
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("creating findings table: %w", err)
	}

	// traceparent: the W3C trace context of the collector span that
	// detected this finding (Phase 3). brain reads it and starts its
	// verification trace with a span LINK back to it -- the only way to
	// connect the two services' traces, since they never call each other
	// and only ever meet through this table. NULL for findings saved
	// before this column existed.
	if err := addColumnIfMissing(db, "findings", "traceparent", "TEXT"); err != nil {
		db.Close()
		return nil, fmt.Errorf("migrating findings table: %w", err)
	}

	return &SQLiteStore{db: db}, nil
}

// addColumnIfMissing is a minimal schema migration: SQLite has no
// "ADD COLUMN IF NOT EXISTS", and CREATE TABLE IF NOT EXISTS above won't
// change an already-existing table -- so an existing vigil.db needs the
// column added explicitly, once.
func addColumnIfMissing(db *sql.DB, table, column, decl string) error {
	var n int
	if err := db.QueryRow(`SELECT count(*) FROM pragma_table_info(?) WHERE name = ?`, table, column).Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	// Identifiers can't be bound parameters; table/column/decl are
	// compile-time constants from this file, never user input.
	_, err := db.Exec(fmt.Sprintf(`ALTER TABLE %s ADD COLUMN %s %s`, table, column, decl))
	return err
}

// SaveFinding persists one finding, timestamped at the moment it's saved.
// Stored as an explicit RFC3339Nano string, not a bare time.Time: passed
// raw, the driver falls back to Go's debug String() format, which embeds a
// monotonic clock reading that's meaningless across process restarts and
// awkward to parse from anything other than Go.
func (s *SQLiteStore) SaveFinding(ctx context.Context, f rules.Finding) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO findings (recorded_at, rule, subject, query, detail)
		VALUES (?, ?, ?, ?, ?)
	`, time.Now().UTC().Format(time.RFC3339Nano), f.Rule, f.Subject, f.Query, f.Detail)
	if err != nil {
		return fmt.Errorf("saving finding: %w", err)
	}
	return nil
}

// SaveFindings persists a batch of findings in ONE transaction.
//
// Why this exists: every SaveFinding call is its own implicit transaction,
// and every commit in WAL mode ends in an fsync. Tracing the collector
// (Phase 1, store.save_finding spans) showed ~40ms per insert -- with
// 10+ findings per cycle, store writes were most of a 1.3s poll cycle.
// One transaction means one commit, so one fsync per batch.
//
// traceparent is stored on every row in the batch (they all came from the
// same rules span). The returned IDs are the new rows' primary keys, in
// order -- the collector puts them on its trace so a finding can be
// looked up from either side.
func (s *SQLiteStore) SaveFindings(ctx context.Context, fs []rules.Finding, traceparent string) ([]int64, error) {
	if len(fs) == 0 {
		return nil, nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("beginning findings tx: %w", err)
	}
	defer tx.Rollback() // no-op after a successful Commit

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO findings (recorded_at, rule, subject, query, detail, traceparent)
		VALUES (?, ?, ?, ?, ?, NULLIF(?, ''))
	`)
	if err != nil {
		return nil, fmt.Errorf("preparing finding insert: %w", err)
	}
	defer stmt.Close()

	now := time.Now().UTC().Format(time.RFC3339Nano)
	ids := make([]int64, 0, len(fs))
	for _, f := range fs {
		res, err := stmt.ExecContext(ctx, now, f.Rule, f.Subject, f.Query, f.Detail, traceparent)
		if err != nil {
			return nil, fmt.Errorf("saving finding: %w", err)
		}
		id, err := res.LastInsertId()
		if err != nil {
			return nil, fmt.Errorf("reading finding id: %w", err)
		}
		ids = append(ids, id)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing findings: %w", err)
	}
	return ids, nil
}

// Close closes the underlying database handle.
func (s *SQLiteStore) Close() error {
	return s.db.Close()
}
