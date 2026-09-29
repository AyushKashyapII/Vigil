package metrics

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// StatementStat is one row from pg_stat_statements: cumulative stats for a
// single normalized query, since the last stats reset.
type StatementStat struct {
	QueryID       int64
	Query         string
	Calls         int64
	TotalExecTime float64 // milliseconds
	MeanExecTime  float64 // milliseconds
	Rows          int64
}

// PollStatements runs a single snapshot query against pg_stat_statements,
// returning every statement Postgres is currently tracking for the
// database this collector is monitoring. No LIMIT here on purpose:
// filtering to "the top N by total time" would decide which queries
// matter before the poller/rules ever get a chance to look -- which
// would silently hide exactly the cheap-but-frequent queries an N+1 rule
// needs to see. Relevance is the rules' job, not this query's.
//
// The dbid filter is not optional. pg_stat_statements is cluster-wide,
// not per-database -- without it, every statement ever run against any
// database on this instance shows up here, including the sandbox's own
// disposable clones (vigil_sandbox_*). Those clones get dropped after
// use, but their pg_stat_statements rows don't: they linger indefinitely
// and get correlated against this database's real table stats, which is
// how the sandbox's own benchmark queries ended up misdetected as real
// application traffic. See ROADMAP.md and learning-log.md.
func PollStatements(ctx context.Context, pool *pgxpool.Pool) ([]StatementStat, error) {
	rows, err := pool.Query(ctx, `
		SELECT queryid, query, calls, total_exec_time, mean_exec_time, rows
		FROM pg_stat_statements
		WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
		ORDER BY total_exec_time DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("querying pg_stat_statements: %w", err)
	}
	defer rows.Close()

	var stats []StatementStat
	for rows.Next() {
		var s StatementStat
		if err := rows.Scan(&s.QueryID, &s.Query, &s.Calls, &s.TotalExecTime, &s.MeanExecTime, &s.Rows); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		stats = append(stats, s)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	return stats, nil
}
