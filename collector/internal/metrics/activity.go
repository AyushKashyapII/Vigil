package metrics

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// ActivitySnapshot is one row from pg_stat_activity: a live look at a
// single open backend connection at the moment of polling. Unlike
// StatementStat, there's nothing cumulative here to diff between polls --
// each snapshot is a complete picture on its own.
type ActivitySnapshot struct {
	PID         int32
	Username    string
	State       string
	StateChange time.Time
	Query       string
}

// PollActivity returns every client backend connection currently known to
// Postgres, in whatever state it's in. No filtering by state here on
// purpose -- deciding which states matter is the rules' job, not this
// query's (same principle as PollStatements). Background processes
// (autovacuum launcher, checkpointer, etc.) have no meaningful state at
// all -- it's NULL -- so they're excluded; that's not a relevance guess,
// it's that they don't have the property being measured.
func PollActivity(ctx context.Context, pool *pgxpool.Pool) ([]ActivitySnapshot, error) {
	rows, err := pool.Query(ctx, `
		SELECT pid, usename, state, state_change, query
		FROM pg_stat_activity
		WHERE state IS NOT NULL
	`)
	if err != nil {
		return nil, fmt.Errorf("querying pg_stat_activity: %w", err)
	}
	defer rows.Close()

	var snapshots []ActivitySnapshot
	for rows.Next() {
		var a ActivitySnapshot
		if err := rows.Scan(&a.PID, &a.Username, &a.State, &a.StateChange, &a.Query); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		snapshots = append(snapshots, a)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	return snapshots, nil
}
