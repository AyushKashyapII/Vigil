package metrics

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// TableStat is one row from pg_stat_user_tables: cumulative scan stats for
// a single table, since the last stats reset.
type TableStat struct {
	// RelID is Postgres's oid type, which pgx requires as uint32 (not
	// int64 like the bigint columns elsewhere in this package).
	RelID      uint32
	SchemaName string
	TableName  string
	SeqScan    int64
	SeqTupRead int64
	IdxScan    int64
}

// PollTables returns every user table's stats. No filtering here, same
// principle as PollStatements/PollActivity.
func PollTables(ctx context.Context, pool *pgxpool.Pool) ([]TableStat, error) {
	rows, err := pool.Query(ctx, `
		SELECT relid, schemaname, relname, seq_scan, seq_tup_read, idx_scan
		FROM pg_stat_user_tables
	`)
	if err != nil {
		return nil, fmt.Errorf("querying pg_stat_user_tables: %w", err)
	}
	defer rows.Close()

	var stats []TableStat
	for rows.Next() {
		var t TableStat
		if err := rows.Scan(&t.RelID, &t.SchemaName, &t.TableName, &t.SeqScan, &t.SeqTupRead, &t.IdxScan); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		stats = append(stats, t)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	return stats, nil
}
