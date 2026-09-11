package metrics

import (
	"context"
	"fmt"
	"os"

	"github.com/jackc/pgx/v5/pgxpool"
)

// OpenPool opens a connection pool to the Postgres database being monitored,
// using the COLLECTOR_DATABASE_URL environment variable (falling back to a
// local default for development).
func OpenPool(ctx context.Context) (*pgxpool.Pool, error) {
	dsn := os.Getenv("COLLECTOR_DATABASE_URL")
	if dsn == "" {
		dsn = "postgres://postgres:postgres@localhost:5432/demo"
	}

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("opening pool: %w", err)
	}

	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("pinging database: %w", err)
	}

	return pool, nil
}
