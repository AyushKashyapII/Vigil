package rules

import (
	"fmt"
	"strings"
	"time"

	"github.com/ayushkashyap/vigil/collector/internal/metrics"
)

const (
	// NPlusOneMinCalls is the minimum calls-per-poll-interval before a
	// query is even considered for the N+1 heuristic.
	NPlusOneMinCalls = 20
	// NPlusOneMaxRowsPerCall is the maximum average rows-per-call for a
	// query to still look like a single-row lookup.
	NPlusOneMaxRowsPerCall = 2.0
	// UnboundedMinRowsPerCall is the average rows-per-call above which a
	// query looks like it's missing a LIMIT.
	UnboundedMinRowsPerCall = 100.0
	// IdleInTransactionMaxDuration is how long a connection can sit in
	// "idle in transaction" before it's flagged as a likely leak.
	IdleInTransactionMaxDuration = 5 * time.Second
)

// Finding is a single rule match -- a candidate worth a human (or the
// brain) looking at, not a confirmed verdict. Subject is a plain,
// human-readable identifier ("queryid=..." or "pid=...") rather than a
// source-specific ID type, since a Finding may describe a query or a
// connection, and this needs to read cleanly whether a person or an LLM is
// the one looking at it.
type Finding struct {
	Rule    string
	Subject string
	Query   string
	Detail  string
}

// DetectNestedSubquery flags a query whose text contains more than one
// SELECT keyword, suggesting a subquery nested in the SELECT list instead
// of a single JOIN. Cheap and imperfect: a legitimate
// `WHERE x IN (SELECT ...)` will also match this.
func DetectNestedSubquery(d metrics.StatementDelta) (Finding, bool) {
	count := strings.Count(strings.ToUpper(d.Query), "SELECT")
	if count > 1 {
		return Finding{
			Rule:    "nested_subquery",
			Subject: fmt.Sprintf("queryid=%d", d.QueryID),
			Query:   d.Query,
			Detail:  fmt.Sprintf("query text contains %d SELECT keywords", count),
		}, true
	}
	return Finding{}, false
}

// DetectPossibleNPlusOne flags a query called many times in one poll
// interval while returning very few rows per call -- the signature of an
// N+1 pattern. It cannot distinguish that from a legitimately hot
// single-row lookup (e.g. a session/auth check called once per request),
// which has the identical shape. Treat this as "worth investigating," not
// "confirmed bug."
func DetectPossibleNPlusOne(d metrics.StatementDelta) (Finding, bool) {
	if d.DeltaCalls >= NPlusOneMinCalls && d.IntervalMeanRows <= NPlusOneMaxRowsPerCall {
		return Finding{
			Rule:    "possible_n_plus_one",
			Subject: fmt.Sprintf("queryid=%d", d.QueryID),
			Query:   d.Query,
			Detail:  fmt.Sprintf("%d calls this interval, avg %.1f rows/call", d.DeltaCalls, d.IntervalMeanRows),
		}, true
	}
	return Finding{}, false
}

// DetectUnboundedQuery flags a query returning a large number of rows per
// call, on average -- the signature of a missing LIMIT/pagination. Unlike
// the N+1 rule, call count doesn't matter here: even a single call pulling
// thousands of rows is already expensive in disk I/O, network, and memory.
func DetectUnboundedQuery(d metrics.StatementDelta) (Finding, bool) {
	if d.IntervalMeanRows >= UnboundedMinRowsPerCall {
		return Finding{
			Rule:    "possible_unbounded_query",
			Subject: fmt.Sprintf("queryid=%d", d.QueryID),
			Query:   d.Query,
			Detail:  fmt.Sprintf("avg %.1f rows/call over %d calls this interval", d.IntervalMeanRows, d.DeltaCalls),
		}, true
	}
	return Finding{}, false
}

// DetectIdleInTransaction flags a connection that has been sitting in
// "idle in transaction" for too long -- it's holding a transaction open
// (and potentially locks) while doing nothing. Other states (active,
// idle) are never flagged by duration; they're normal on their own.
func DetectIdleInTransaction(a metrics.ActivitySnapshot) (Finding, bool) {
	if a.State != "idle in transaction" {
		return Finding{}, false
	}
	idleFor := time.Since(a.StateChange)
	if idleFor >= IdleInTransactionMaxDuration {
		return Finding{
			Rule:    "idle_in_transaction",
			Subject: fmt.Sprintf("pid=%d", a.PID),
			Query:   a.Query,
			Detail:  fmt.Sprintf("idle in transaction for %s", idleFor.Round(time.Second)),
		}, true
	}
	return Finding{}, false
}

// EvaluateStatements runs every pg_stat_statements-based rule against each
// delta and returns all findings.
func EvaluateStatements(deltas []metrics.StatementDelta) []Finding {
	var findings []Finding
	for _, d := range deltas {
		if f, ok := DetectNestedSubquery(d); ok {
			findings = append(findings, f)
		}
		if f, ok := DetectPossibleNPlusOne(d); ok {
			findings = append(findings, f)
		}
		if f, ok := DetectUnboundedQuery(d); ok {
			findings = append(findings, f)
		}
	}
	return findings
}

// EvaluateActivity runs every pg_stat_activity-based rule against each
// connection snapshot and returns all findings.
func EvaluateActivity(snapshots []metrics.ActivitySnapshot) []Finding {
	var findings []Finding
	for _, a := range snapshots {
		if f, ok := DetectIdleInTransaction(a); ok {
			findings = append(findings, f)
		}
	}
	return findings
}
