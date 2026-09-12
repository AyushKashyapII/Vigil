package main

import (
	"context"
	"fmt"
	"time"

	"github.com/ayushkashyap/vigil/collector/internal/metrics"
	"github.com/ayushkashyap/vigil/collector/internal/rules"
)

const pollInterval = 5 * time.Second

func main() {
	fmt.Println("vigil collector starting")

	ctx := context.Background()

	pool, err := metrics.OpenPool(ctx)
	if err != nil {
		fmt.Println("failed to connect:", err)
		return
	}
	defer pool.Close()

	poller := metrics.NewPoller()
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	for {
		deltas, err := poller.PollDeltas(ctx, pool)
		if err != nil {
			fmt.Println("failed to poll pg_stat_statements:", err)
		} else if len(deltas) == 0 {
			fmt.Println("no deltas yet (baseline established)")
		} else {
			for _, d := range deltas {
				fmt.Printf("queryid=%d delta_calls=%d delta_total_exec_time=%.2fms interval_mean_exec_time=%.2fms delta_rows=%d interval_mean_rows=%.1f query=%q\n",
					d.QueryID, d.DeltaCalls, d.DeltaTotalExecTime, d.IntervalMeanExecTime, d.DeltaRows, d.IntervalMeanRows, d.Query)
			}

			for _, f := range rules.EvaluateStatements(deltas) {
				fmt.Printf("FINDING [%s] %s: %s\n  query=%q\n", f.Rule, f.Subject, f.Detail, f.Query)
			}
		}

		activity, err := metrics.PollActivity(ctx, pool)
		if err != nil {
			fmt.Println("failed to poll pg_stat_activity:", err)
		} else {
			for _, f := range rules.EvaluateActivity(activity) {
				fmt.Printf("FINDING [%s] %s: %s\n  query=%q\n", f.Rule, f.Subject, f.Detail, f.Query)
			}
		}

		<-ticker.C
	}
}
