package metrics

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// IndexStat is one row from pg_stat_user_indexes: cumulative usage stats
// for a single index, since the last stats reset.
type IndexStat struct {
	IndexRelID uint32
	SchemaName string
	TableName  string
	IndexName  string
	IdxScan    int64
}

// PollIndexes returns every user index's usage stats. No filtering here,
// same principle as the other Poll* functions.
func PollIndexes(ctx context.Context, pool *pgxpool.Pool) ([]IndexStat, error) {
	rows, err := pool.Query(ctx, `
		SELECT indexrelid, schemaname, relname, indexrelname, idx_scan
		FROM pg_stat_user_indexes
	`)
	if err != nil {
		return nil, fmt.Errorf("querying pg_stat_user_indexes: %w", err)
	}
	defer rows.Close()

	var stats []IndexStat
	for rows.Next() {
		var s IndexStat
		if err := rows.Scan(&s.IndexRelID, &s.SchemaName, &s.TableName, &s.IndexName, &s.IdxScan); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		stats = append(stats, s)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	return stats, nil
}
