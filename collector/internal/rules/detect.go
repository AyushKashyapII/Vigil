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
	// ApproachingMaxConnectionsRatio is the fraction of max_connections in
	// use above which the database is flagged as approaching its limit.
	ApproachingMaxConnectionsRatio = 0.8
	// MissingIndexMinSeqScans is the minimum sequential scans per poll
	// interval before a table is even considered for this heuristic.
	MissingIndexMinSeqScans = 1
	// MissingIndexMinRowsPerScan is the average rows read per sequential
	// scan above which a scan is considered expensive, not a trivial scan
	// of a small table.
	MissingIndexMinRowsPerScan = 1000.0
	// UnusedIndexMinDuration is how long an index's usage count must stay
	// unchanged before it's flagged as a candidate to drop. Duration only
	// -- doesn't weigh maintenance cost (see ROADMAP.md).
	UnusedIndexMinDuration = 10 * 24 * time.Hour
	// Bloat detection (BloatMinDeadRatio, BloatMinDeadTuples) is disabled
	// -- see DetectBloat below and ROADMAP.md. Twice in testing,
	// autovacuum cleaned up dead tuples before this rule's threshold was
	// ever crossed on our small demo table, so the rule was never
	// honestly exercised. Needs a bigger/busier table to trust before
	// re-enabling.
	// BloatMinDeadRatio  = 0.20
	// BloatMinDeadTuples = 1000
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

// DetectApproachingMaxConnections flags the database as a whole -- not a
// single query, table, or connection -- when open connections are
// approaching max_connections. Structurally different from every rule
// above: those each judge one row; this judges an aggregate (a count)
// against a server-wide limit, so it takes plain numbers, not a metrics
// type.
func DetectApproachingMaxConnections(current, max int) (Finding, bool) {
	if max == 0 {
		return Finding{}, false
	}
	ratio := float64(current) / float64(max)
	if ratio >= ApproachingMaxConnectionsRatio {
		return Finding{
			Rule:    "approaching_max_connections",
			Subject: "database",
			Detail:  fmt.Sprintf("%d/%d connections in use (%.0f%%)", current, max, ratio*100),
		}, true
	}
	return Finding{}, false
}

// EvaluateConnectionCount runs the max-connections check. A single-item
// slice, not a loop over rows, to match the calling convention every
// other Evaluate* function uses in main.go.
func EvaluateConnectionCount(current, max int) []Finding {
	var findings []Finding
	if f, ok := DetectApproachingMaxConnections(current, max); ok {
		findings = append(findings, f)
	}
	return findings
}

// DetectMissingIndex flags a table getting sequentially scanned often and
// expensively -- the signature of a query filtering on a column with no
// index, forcing Postgres to read most/all of the table to find matches.
// Call count doesn't gate this the way it does for N+1: even one seq scan
// reading a huge number of rows is worth flagging, same reasoning as
// DetectUnboundedQuery.
func DetectMissingIndex(t metrics.TableDelta) (Finding, bool) {
	if t.DeltaSeqScan >= MissingIndexMinSeqScans && t.IntervalMeanSeqTupRead >= MissingIndexMinRowsPerScan {
		return Finding{
			Rule:    "possible_missing_index",
			Subject: fmt.Sprintf("table=%s.%s", t.SchemaName, t.TableName),
			Detail:  fmt.Sprintf("%d sequential scans this interval, avg %.0f rows read per scan", t.DeltaSeqScan, t.IntervalMeanSeqTupRead),
		}, true
	}
	return Finding{}, false
}

// DetectUnusedIndex flags an index whose usage count has stayed unchanged
// for a long time -- a candidate to drop. This is duration-only, not a
// full cost/benefit judgment (see ROADMAP.md for why that's deferred).
func DetectUnusedIndex(idx metrics.IndexDelta) (Finding, bool) {
	if idx.UnusedFor >= UnusedIndexMinDuration {
		return Finding{
			Rule:    "possible_unused_index",
			Subject: fmt.Sprintf("index=%s.%s", idx.SchemaName, idx.IndexName),
			Detail:  fmt.Sprintf("unused for %s (idx_scan=%d, table=%s)", idx.UnusedFor.Round(time.Hour), idx.IdxScan, idx.TableName),
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

// DetectBloat flags a table with a high proportion of dead tuples relative
// to live ones -- a candidate for VACUUM. This is a snapshot ratio, not a
// rate: it reflects the table's current state, not what changed this
// interval.
//
// Disabled pending a more realistic test -- see the note on the
// Bloat* constants above and ROADMAP.md. Commented out rather than
// deleted, along with its wiring in EvaluateTables and the NLiveTup/
// NDeadTup fields it depended on (tables.go, table_poller.go).
//
// func DetectBloat(t metrics.TableDelta) (Finding, bool) {
// 	total := t.NLiveTup + t.NDeadTup
// 	if total == 0 || t.NDeadTup < BloatMinDeadTuples {
// 		return Finding{}, false
// 	}
// 	ratio := float64(t.NDeadTup) / float64(total)
// 	if ratio >= BloatMinDeadRatio {
// 		return Finding{
// 			Rule:    "possible_bloat",
// 			Subject: fmt.Sprintf("table=%s.%s", t.SchemaName, t.TableName),
// 			Detail:  fmt.Sprintf("%.0f%% dead tuples (%d dead / %d live)", ratio*100, t.NDeadTup, t.NLiveTup),
// 		}, true
// 	}
// 	return Finding{}, false
// }

// EvaluateTables runs every pg_stat_user_tables-based rule against each
// table delta and returns all findings.
func EvaluateTables(deltas []metrics.TableDelta) []Finding {
	var findings []Finding
	for _, t := range deltas {
		if f, ok := DetectMissingIndex(t); ok {
			findings = append(findings, f)
		}
		// if f, ok := DetectBloat(t); ok {
		// 	findings = append(findings, f)
		// }
	}
	return findings
}

// EvaluateIndexes runs every pg_stat_user_indexes-based rule against each
// index delta and returns all findings.
func EvaluateIndexes(deltas []metrics.IndexDelta) []Finding {
	var findings []Finding
	for _, idx := range deltas {
		if f, ok := DetectUnusedIndex(idx); ok {
			findings = append(findings, f)
		}
	}
	return findings
}
