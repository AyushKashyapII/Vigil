package main

import (
	"context"
	"fmt"

	"github.com/ayushkashyap/vigil/collector/internal/metrics"
)

func main() {
	fmt.Println("vigil collector starting")

	ctx := context.Background()

	pool, err := metrics.OpenPool(ctx)
	if err != nil {
		fmt.Println("failed to connect:", err)
		return
	}
	defer pool.Close()

	stats, err := metrics.PollStatements(ctx, pool)
	if err != nil {
		fmt.Println("failed to poll pg_stat_statements:", err)
		return
	}

	for _, s := range stats {
		fmt.Printf("queryid=%d calls=%d total_exec_time=%.2fms mean_exec_time=%.2fms rows=%d query=%q\n",
			s.QueryID, s.Calls, s.TotalExecTime, s.MeanExecTime, s.Rows, s.Query)
	}
}
