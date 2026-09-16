package metrics

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

type prevTableStats struct {
	SeqScan    int64
	SeqTupRead int64
	IdxScan    int64
}

// TableDelta is the change in a table's scan stats between the previous
// poll and this one.
//
// NLiveTup/NDeadTup (bloat detection) are disabled -- see the note in
// tables.go and ROADMAP.md -- commented out here to match.
type TableDelta struct {
	RelID                  uint32
	SchemaName             string
	TableName              string
	DeltaSeqScan           int64
	DeltaSeqTupRead        int64
	IntervalMeanSeqTupRead float64 // rows read per seq scan; 0 if DeltaSeqScan == 0
	DeltaIdxScan           int64
	// NLiveTup int64
	// NDeadTup int64
}

// TablePoller tracks pg_stat_user_tables snapshots across polls, same
// reasoning as Poller: seq_scan/idx_scan are cumulative counters, so a
// single snapshot alone isn't a meaningful rate.
type TablePoller struct {
	previous map[uint32]prevTableStats
}

// NewTablePoller returns a TablePoller with no prior state.
func NewTablePoller() *TablePoller {
	return &TablePoller{previous: make(map[uint32]prevTableStats)}
}

// PollDeltas polls pg_stat_user_tables and returns the change in each
// table's stats since the last call. A table seen for the first time, or
// whose counters have gone down since the last poll (a reset), is
// recorded as a new baseline and not included in the returned deltas --
// same handling as Poller.PollDeltas.
func (p *TablePoller) PollDeltas(ctx context.Context, pool *pgxpool.Pool) ([]TableDelta, error) {
	current, err := PollTables(ctx, pool)
	if err != nil {
		return nil, err
	}

	var deltas []TableDelta
	for _, t := range current {
		prev, ok := p.previous[t.RelID]
		if ok && t.SeqScan >= prev.SeqScan && t.IdxScan >= prev.IdxScan {
			deltaSeqScan := t.SeqScan - prev.SeqScan
			deltaSeqTupRead := t.SeqTupRead - prev.SeqTupRead
			deltaIdxScan := t.IdxScan - prev.IdxScan

			var intervalMeanSeqTupRead float64
			if deltaSeqScan > 0 {
				intervalMeanSeqTupRead = float64(deltaSeqTupRead) / float64(deltaSeqScan)
			}

			deltas = append(deltas, TableDelta{
				RelID:                  t.RelID,
				SchemaName:             t.SchemaName,
				TableName:              t.TableName,
				DeltaSeqScan:           deltaSeqScan,
				DeltaSeqTupRead:        deltaSeqTupRead,
				IntervalMeanSeqTupRead: intervalMeanSeqTupRead,
				DeltaIdxScan:           deltaIdxScan,
				// NLiveTup: t.NLiveTup,
				// NDeadTup: t.NDeadTup,
			})
		}

		p.previous[t.RelID] = prevTableStats{SeqScan: t.SeqScan, SeqTupRead: t.SeqTupRead, IdxScan: t.IdxScan}
	}

	return deltas, nil
}
