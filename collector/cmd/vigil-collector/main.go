package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/ayushkashyap/vigil/collector/internal/metrics"
	"github.com/ayushkashyap/vigil/collector/internal/rules"
	"github.com/ayushkashyap/vigil/collector/internal/store"
)

const (
	pollInterval = 5 * time.Second
	// indexPollInterval is how often pg_stat_user_indexes actually gets
	// polled. The unused-index signal only matters on a day-scale
	// timescale, so polling it every 5s like everything else would be
	// pure waste -- instead this runs on the same ticker but is skipped
	// on most ticks (see ticksPerIndexPoll below), avoiding a second
	// goroutine/ticker for now (see ROADMAP.md).
	indexPollInterval = 1 * time.Hour
)

var ticksPerIndexPoll = int(indexPollInterval / pollInterval)

func main() {
	fmt.Println("vigil collector starting")

	ctx := context.Background()

	pool, err := metrics.OpenPool(ctx)
	if err != nil {
		fmt.Println("failed to connect:", err)
		os.Exit(1)
	}
	defer pool.Close()

	st, err := store.Open()
	if err != nil {
		fmt.Println("failed to open store:", err)
		os.Exit(1)
	}
	defer st.Close()

	poller := metrics.NewPoller()
	tablePoller := metrics.NewTablePoller()
	indexPoller := metrics.NewIndexPoller()
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	tick := 0
	for {
		tick++
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

			reportFindings(ctx, st, rules.EvaluateStatements(deltas))
		}

		activity, err := metrics.PollActivity(ctx, pool)
		if err != nil {
			fmt.Println("failed to poll pg_stat_activity:", err)
		} else {
			reportFindings(ctx, st, rules.EvaluateActivity(activity))
		}

		tableDeltas, err := tablePoller.PollDeltas(ctx, pool)
		if err != nil {
			fmt.Println("failed to poll pg_stat_user_tables:", err)
		} else {
			for _, t := range tableDeltas {
				// n_live_tup/n_dead_tup dropped from this line -- bloat
				// detection disabled, see ROADMAP.md.
				fmt.Printf("table=%s.%s delta_seq_scan=%d delta_seq_tup_read=%d interval_mean_seq_tup_read=%.1f delta_idx_scan=%d\n",
					t.SchemaName, t.TableName, t.DeltaSeqScan, t.DeltaSeqTupRead, t.IntervalMeanSeqTupRead, t.DeltaIdxScan)
			}
			reportFindings(ctx, st, rules.EvaluateTables(tableDeltas))
		}

		if (tick-1)%ticksPerIndexPoll == 0 {
			indexDeltas, err := indexPoller.PollDeltas(ctx, pool)
			if err != nil {
				fmt.Println("failed to poll pg_stat_user_indexes:", err)
			} else {
				for _, idx := range indexDeltas {
					fmt.Printf("index=%s.%s idx_scan=%d unused_for=%s\n",
						idx.SchemaName, idx.IndexName, idx.IdxScan, idx.UnusedFor.Round(time.Minute))
				}
				reportFindings(ctx, st, rules.EvaluateIndexes(indexDeltas))
			}
		}

		<-ticker.C
	}
}

// reportFindings prints each finding and persists it to the store.
func reportFindings(ctx context.Context, st store.Store, findings []rules.Finding) {
	for _, f := range findings {
		fmt.Printf("FINDING [%s] %s: %s\n  query=%q\n", f.Rule, f.Subject, f.Detail, f.Query)
		if err := st.SaveFinding(ctx, f); err != nil {
			fmt.Println("failed to save finding:", err)
		}
	}
}
