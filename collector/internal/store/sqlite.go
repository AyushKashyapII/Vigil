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
func (s *SQLiteStore) SaveFinding(ctx context.Context, f rules.Finding) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO findings (recorded_at, rule, subject, query, detail)
		VALUES (?, ?, ?, ?, ?)
	`, time.Now(), f.Rule, f.Subject, f.Query, f.Detail)
	if err != nil {
		return fmt.Errorf("saving finding: %w", err)
	}
	return nil
}

// Close closes the underlying database handle.
func (s *SQLiteStore) Close() error {
	return s.db.Close()
}
