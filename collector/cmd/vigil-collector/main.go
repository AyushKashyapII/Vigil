package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/trace"

	"github.com/ayushkashyap/vigil/collector/internal/metrics"
	"github.com/ayushkashyap/vigil/collector/internal/rules"
	"github.com/ayushkashyap/vigil/collector/internal/store"
	"github.com/ayushkashyap/vigil/collector/internal/telemetry"
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

// collector holds everything one poll cycle needs, so pollOnce can be a
// single function wrapped in a single span.
type collector struct {
	pool           *pgxpool.Pool
	st             store.Store
	maxConnections int
	poller         *metrics.Poller
	tablePoller    *metrics.TablePoller
	indexPoller    *metrics.IndexPoller
	tracer         trace.Tracer
	inst           *telemetry.Instruments
}

func main() {
	fmt.Println("vigil collector starting")

	// Cancelled on SIGINT/SIGTERM (`docker stop` sends SIGTERM), so the
	// loop below returns normally and the deferred telemetry shutdown gets
	// to flush buffered spans/metrics. Without this the process is killed
	// mid-loop and the last few seconds of telemetry are lost.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Telemetry is best-effort: if it can't be set up, the collector still
	// does its actual job. The global providers stay no-ops, so every
	// span/metric call below is still safe.
	if shutdown, err := telemetry.Setup(ctx); err != nil {
		fmt.Println("telemetry disabled:", err)
	} else {
		defer func() {
			// Fresh context: ctx is already cancelled by the time this runs.
			sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := shutdown(sctx); err != nil {
				fmt.Println("telemetry shutdown:", err)
			}
		}()
	}
	inst, err := telemetry.NewInstruments()
	if err != nil {
		fmt.Println("failed to create telemetry instruments:", err)
		os.Exit(1)
	}

	pool, err := metrics.OpenPool(ctx)
	if err != nil {
		fmt.Println("failed to connect:", err)
		os.Exit(1)
	}
	defer pool.Close()

	// max_connections only changes on a Postgres restart, so it's fetched
	// once here rather than polled every cycle. Non-fatal if it fails --
	// the connection-count rule just won't fire (DetectApproachingMax
	// Connections treats max==0 as "nothing to check against").
	maxConnections, err := metrics.MaxConnections(ctx, pool)
	if err != nil {
		fmt.Println("failed to fetch max_connections:", err)
	}

	st, err := store.Open()
	if err != nil {
		fmt.Println("failed to open store:", err)
		os.Exit(1)
	}
	defer st.Close()

	c := &collector{
		pool:           pool,
		st:             st,
		maxConnections: maxConnections,
		poller:         metrics.NewPoller(),
		tablePoller:    metrics.NewTablePoller(),
		indexPoller:    metrics.NewIndexPoller(),
		tracer:         otel.Tracer(telemetry.ScopeName),
		inst:           inst,
	}

	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	for tick := 1; ; tick++ {
		c.pollOnce(ctx, tick)

		select {
		case <-ctx.Done():
			fmt.Println("vigil collector shutting down")
			return
		case <-ticker.C:
		}
	}
}

// pollOnce runs one full cycle -- every stats view, every rule group --
// as one trace:
//
//	vigil.poll_cycle
//	├── poll pg_stat_statements
//	├── rules statements           (one event per finding)
//	├── poll pg_stat_statements_counts
//	├── poll pg_stat_activity
//	├── rules activity
//	├── rules connections
//	├── poll pg_stat_user_tables
//	├── rules tables
//	├── poll pg_stat_user_indexes  (only on index ticks)
//	└── rules indexes
func (c *collector) pollOnce(ctx context.Context, tick int) {
	start := time.Now()
	indexTick := (tick-1)%ticksPerIndexPoll == 0

	ctx, span := c.tracer.Start(ctx, "vigil.poll_cycle", trace.WithAttributes(
		attribute.Int("vigil.poll.tick", tick),
		attribute.Bool("vigil.poll.includes_indexes", indexTick),
	))
	defer func() {
		// Recorded with the span's ctx so a backend that supports
		// exemplars can jump from a slow histogram bucket to this trace.
		c.inst.PollDuration.Record(ctx, time.Since(start).Seconds())
		span.End()
	}()

	deltas, err := poll(ctx, c, "pg_stat_statements", func(ctx context.Context) ([]metrics.StatementDelta, error) {
		return c.poller.PollDeltas(ctx, c.pool)
	})
	if err == nil {
		if len(deltas) == 0 {
			fmt.Println("no deltas yet (baseline established)")
		} else {
			for _, d := range deltas {
				fmt.Printf("queryid=%d delta_calls=%d delta_total_exec_time=%.2fms interval_mean_exec_time=%.2fms delta_rows=%d interval_mean_rows=%.1f query=%q\n",
					d.QueryID, d.DeltaCalls, d.DeltaTotalExecTime, d.IntervalMeanExecTime, d.DeltaRows, d.IntervalMeanRows, d.Query)
			}
			c.evaluate(ctx, "statements", func() []rules.Finding { return rules.EvaluateStatements(deltas) })
		}
	}

	if counts, err := poll(ctx, c, "pg_stat_statements_counts", func(ctx context.Context) (metrics.StatementCounts, error) {
		return metrics.CountStatements(ctx, c.pool)
	}); err == nil {
		c.inst.StatementsTracked.Record(ctx, counts.Tracked)
		c.inst.StatementsOrphaned.Record(ctx, counts.Orphaned)
	}

	activity, err := poll(ctx, c, "pg_stat_activity", func(ctx context.Context) ([]metrics.ActivitySnapshot, error) {
		return metrics.PollActivity(ctx, c.pool)
	})
	if err == nil {
		c.evaluate(ctx, "activity", func() []rules.Finding { return rules.EvaluateActivity(activity) })
		c.evaluate(ctx, "connections", func() []rules.Finding {
			return rules.EvaluateConnectionCount(len(activity), c.maxConnections)
		})
	}

	tableDeltas, err := poll(ctx, c, "pg_stat_user_tables", func(ctx context.Context) ([]metrics.TableDelta, error) {
		return c.tablePoller.PollDeltas(ctx, c.pool)
	})
	if err == nil {
		for _, t := range tableDeltas {
			// n_live_tup/n_dead_tup dropped from this line -- bloat
			// detection disabled, see ROADMAP.md.
			fmt.Printf("table=%s.%s delta_seq_scan=%d delta_seq_tup_read=%d interval_mean_seq_tup_read=%.1f delta_idx_scan=%d\n",
				t.SchemaName, t.TableName, t.DeltaSeqScan, t.DeltaSeqTupRead, t.IntervalMeanSeqTupRead, t.DeltaIdxScan)
		}
		c.evaluate(ctx, "tables", func() []rules.Finding { return rules.EvaluateTables(tableDeltas, deltas) })
	}

	if indexTick {
		indexDeltas, err := poll(ctx, c, "pg_stat_user_indexes", func(ctx context.Context) ([]metrics.IndexDelta, error) {
			return c.indexPoller.PollDeltas(ctx, c.pool)
		})
		if err == nil {
			for _, idx := range indexDeltas {
				fmt.Printf("index=%s.%s idx_scan=%d unused_for=%s\n",
					idx.SchemaName, idx.IndexName, idx.IdxScan, idx.UnusedFor.Round(time.Minute))
			}
			c.evaluate(ctx, "indexes", func() []rules.Finding { return rules.EvaluateIndexes(indexDeltas) })
		}
	}
}

// poll runs one stats-view query inside its own span. On failure it does
// the three things a failure needs: log it, mark the span as an error (so
// the trace shows red), and count it (so it can be graphed/alerted on).
//
// A generic free function rather than a method because Go methods can't
// have type parameters.
func poll[T any](ctx context.Context, c *collector, source string, fn func(context.Context) (T, error)) (T, error) {
	ctx, span := c.tracer.Start(ctx, "poll "+source,
		trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(
			attribute.String("db.system", "postgresql"),
			attribute.String(telemetry.AttrSource, source),
		),
	)
	defer span.End()

	v, err := fn(ctx)
	if err != nil {
		fmt.Printf("failed to poll %s: %v\n", source, err)
		span.RecordError(err)
		span.SetStatus(codes.Error, "poll failed")
		c.inst.PollErrors.Add(ctx, 1, metric.WithAttributes(attribute.String(telemetry.AttrSource, source)))
	}
	return v, err
}

// evaluate runs one group of detection rules inside a span, then records
// and persists whatever it found.
//
// One span per rule GROUP, not per rule: rules run inside per-row loops,
// so a span per rule invocation would be (rules x statements) spans every
// 5s, each microseconds long -- volume with no information. Each finding
// instead becomes a span EVENT, which can carry high-cardinality detail
// (subject, queryid) that must never go on a metric attribute.
func (c *collector) evaluate(ctx context.Context, group string, run func() []rules.Finding) {
	ctx, span := c.tracer.Start(ctx, "rules "+group)
	defer span.End()

	findings := run()
	span.SetAttributes(attribute.Int("vigil.findings.count", len(findings)))
	for _, f := range findings {
		span.AddEvent("finding", trace.WithAttributes(
			attribute.String(telemetry.AttrRule, f.Rule),
			attribute.String("vigil.subject", f.Subject),
			attribute.String("vigil.detail", f.Detail),
		))
		// Only the bounded rule name goes on the metric.
		c.inst.FindingsDetected.Add(ctx, 1, metric.WithAttributes(attribute.String(telemetry.AttrRule, f.Rule)))
	}

	c.reportFindings(ctx, findings)
}

// reportFindings prints each finding and persists the whole batch in one
// store transaction, inside its own span so store latency is visible
// separately from rule time.
//
// It used to be one SaveFinding (= one commit = one fsync) per finding;
// the store.save_finding spans that replaced showed ~40ms each, most of
// the poll cycle. See SQLiteStore.SaveFindings.
func (c *collector) reportFindings(ctx context.Context, findings []rules.Finding) {
	if len(findings) == 0 {
		return
	}
	for _, f := range findings {
		fmt.Printf("FINDING [%s] %s: %s\n  query=%q\n", f.Rule, f.Subject, f.Detail, f.Query)
	}

	ctx, span := c.tracer.Start(ctx, "store.save_findings", trace.WithAttributes(
		attribute.String("db.system", "sqlite"),
		attribute.Int("vigil.findings.count", len(findings)),
	))
	defer span.End()
	if err := c.st.SaveFindings(ctx, findings); err != nil {
		fmt.Println("failed to save findings:", err)
		span.RecordError(err)
		span.SetStatus(codes.Error, "save failed")
	}
}
