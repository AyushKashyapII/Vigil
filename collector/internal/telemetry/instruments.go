package telemetry

import (
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/metric"
)

// Attribute keys shared by spans and metrics. Namespaced under "vigil." so
// they can't collide with OpenTelemetry semantic conventions.
//
// Cardinality rule for anything used as a METRIC attribute: every distinct
// value creates a separate time series, forever. "vigil.rule" (7 values)
// and "vigil.source" (5 values) are fine. Query text, queryid, table names
// are not -- those go on span attributes/events, where each one is just a
// field on a single record rather than a new series.
const (
	AttrRule   = "vigil.rule"
	AttrSource = "vigil.source"
)

// Instruments are the collector's self-metrics.
type Instruments struct {
	PollDuration       metric.Float64Histogram
	PollErrors         metric.Int64Counter
	FindingsDetected   metric.Int64Counter
	StatementsTracked  metric.Int64Gauge
	StatementsOrphaned metric.Int64Gauge
	ConnectionsUsed    metric.Int64Gauge
	ConnUtilization    metric.Float64Gauge
}

// NewInstruments creates the instruments from the global MeterProvider.
// If Setup failed (or was never called) that's the no-op provider, so
// every Record/Add below is safe and simply does nothing.
func NewInstruments() (*Instruments, error) {
	m := otel.Meter(ScopeName)
	var (
		inst Instruments
		err  error
	)

	// Seconds, per OTel semantic conventions for durations. The SDK's
	// default histogram buckets (0, 5, 10, 25, ... 10000) assume
	// milliseconds-ish magnitudes -- with values in seconds every poll
	// would land in the first bucket and the histogram would be useless.
	// So the buckets are set explicitly for a poll that should take
	// milliseconds and must never approach the 5s poll interval.
	if inst.PollDuration, err = m.Float64Histogram("vigil.poll.duration",
		metric.WithDescription("Wall-clock time of one full poll cycle (all stats views + rules + store writes)."),
		metric.WithUnit("s"),
		metric.WithExplicitBucketBoundaries(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
	); err != nil {
		return nil, err
	}

	if inst.PollErrors, err = m.Int64Counter("vigil.poll.errors",
		metric.WithDescription("Failed polls of a Postgres stats view, by source."),
		metric.WithUnit("{error}"),
	); err != nil {
		return nil, err
	}

	if inst.FindingsDetected, err = m.Int64Counter("vigil.findings.detected",
		metric.WithDescription("Findings produced by the detection rules, by rule."),
		metric.WithUnit("{finding}"),
	); err != nil {
		return nil, err
	}

	if inst.StatementsTracked, err = m.Int64Gauge("vigil.statements.tracked",
		metric.WithDescription("pg_stat_statements entries belonging to the monitored database."),
		metric.WithUnit("{statement}"),
	); err != nil {
		return nil, err
	}

	if inst.StatementsOrphaned, err = m.Int64Gauge("vigil.statements.orphaned",
		metric.WithDescription("pg_stat_statements entries whose database no longer exists (e.g. dropped sandbox clones)."),
		metric.WithUnit("{statement}"),
	); err != nil {
		return nil, err
	}

	if inst.ConnectionsUsed, err = m.Int64Gauge("vigil.connections.used",
		metric.WithDescription("Client connections open on the monitored Postgres (pg_stat_activity, excluding background processes)."),
		metric.WithUnit("{connection}"),
	); err != nil {
		return nil, err
	}

	// The raw measurement behind the approaching_max_connections rule,
	// exported so the THRESHOLD can live in the alerting tool (SigNoz)
	// instead of only in Go code -- alerting on a measurement, rather than
	// on a count of findings (which would be alerting on an alert).
	if inst.ConnUtilization, err = m.Float64Gauge("vigil.connections.utilization",
		metric.WithDescription("Open client connections / max_connections (0..1). Only recorded when max_connections is known."),
		metric.WithUnit("1"),
	); err != nil {
		return nil, err
	}

	return &inst, nil
}
