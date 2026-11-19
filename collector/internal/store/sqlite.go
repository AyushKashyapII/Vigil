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
	SaveFindings(ctx context.Context, fs []rules.Finding) error
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

	return &SQLiteStore{db: db}, nil
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
func (s *SQLiteStore) SaveFindings(ctx context.Context, fs []rules.Finding) error {
	if len(fs) == 0 {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("beginning findings tx: %w", err)
	}
	defer tx.Rollback() // no-op after a successful Commit

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO findings (recorded_at, rule, subject, query, detail)
		VALUES (?, ?, ?, ?, ?)
	`)
	if err != nil {
		return fmt.Errorf("preparing finding insert: %w", err)
	}
	defer stmt.Close()

	now := time.Now().UTC().Format(time.RFC3339Nano)
	for _, f := range fs {
		if _, err := stmt.ExecContext(ctx, now, f.Rule, f.Subject, f.Query, f.Detail); err != nil {
			return fmt.Errorf("saving finding: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing findings: %w", err)
	}
	return nil
}

// Close closes the underlying database handle.
func (s *SQLiteStore) Close() error {
	return s.db.Close()
}
