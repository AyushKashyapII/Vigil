package metrics

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

// prevStats holds just the numeric fields needed to compute deltas between
// polls -- the query text never changes, so there's no need to store it
// twice.
type prevStats struct {
	Calls         int64
	TotalExecTime float64
	Rows          int64
}

// StatementDelta is the change in a statement's stats between the previous
// poll and this one, plus the average latency and row count of calls made
// during that interval.
type StatementDelta struct {
	QueryID              int64
	Query                string
	DeltaCalls           int64
	DeltaTotalExecTime   float64 // milliseconds
	IntervalMeanExecTime float64 // milliseconds; 0 if DeltaCalls == 0
	DeltaRows            int64
	IntervalMeanRows     float64 // rows per call; 0 if DeltaCalls == 0
}

// Poller tracks pg_stat_statements snapshots across polls so it can report
// deltas. The raw counters from PollStatements are cumulative since the
// last stats reset, so a single snapshot alone isn't a meaningful rate.
type Poller struct {
	previous map[int64]prevStats
}

// NewPoller returns a Poller with no prior state.
func NewPoller() *Poller {
	return &Poller{previous: make(map[int64]prevStats)}
}

// PollDeltas polls pg_stat_statements and returns the change in each
// statement's stats since the last call to PollDeltas.
//
// A query seen for the first time -- including every query on the very
// first call -- has nothing to diff against yet, so it's recorded as the
// new baseline and not included in the returned deltas. The same applies
// if a query's counters have gone down since the last poll (e.g. after a
// Postgres restart or pg_stat_statements_reset()): that's treated as a
// fresh baseline rather than a negative delta.
func (p *Poller) PollDeltas(ctx context.Context, pool *pgxpool.Pool) ([]StatementDelta, error) {
	current, err := PollStatements(ctx, pool)
	if err != nil {
		return nil, err
	}

	var deltas []StatementDelta
	for _, s := range current {
		prev, ok := p.previous[s.QueryID]
		if ok && s.Calls >= prev.Calls {
			deltaCalls := s.Calls - prev.Calls
			deltaTotal := s.TotalExecTime - prev.TotalExecTime
			deltaRows := s.Rows - prev.Rows

			var intervalMean, intervalMeanRows float64
			if deltaCalls > 0 {
				intervalMean = deltaTotal / float64(deltaCalls)
				intervalMeanRows = float64(deltaRows) / float64(deltaCalls)
			}

			deltas = append(deltas, StatementDelta{
				QueryID:              s.QueryID,
				Query:                s.Query,
				DeltaCalls:           deltaCalls,
				DeltaTotalExecTime:   deltaTotal,
				IntervalMeanExecTime: intervalMean,
				DeltaRows:            deltaRows,
				IntervalMeanRows:     intervalMeanRows,
			})
		}

		p.previous[s.QueryID] = prevStats{Calls: s.Calls, TotalExecTime: s.TotalExecTime, Rows: s.Rows}
	}

	return deltas, nil
}
