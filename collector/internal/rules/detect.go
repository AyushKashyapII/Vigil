package rules

import (
	"fmt"
	"strings"

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
)

// Finding is a single rule match against a statement's delta. It's a
// candidate worth a human (or the brain) looking at -- not a confirmed
// verdict. Neither rule below can see request-level context, so both are
// heuristics with known false-positive shapes.
type Finding struct {
	QueryID int64
	Query   string
	Rule    string
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
			QueryID: d.QueryID,
			Query:   d.Query,
			Rule:    "nested_subquery",
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
			QueryID: d.QueryID,
			Query:   d.Query,
			Rule:    "possible_n_plus_one",
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
			QueryID: d.QueryID,
			Query:   d.Query,
			Rule:    "possible_unbounded_query",
			Detail:  fmt.Sprintf("avg %.1f rows/call over %d calls this interval", d.IntervalMeanRows, d.DeltaCalls),
		}, true
	}
	return Finding{}, false
}

// Evaluate runs every rule against each delta and returns all findings.
func Evaluate(deltas []metrics.StatementDelta) []Finding {
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
