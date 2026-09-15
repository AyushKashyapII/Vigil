package metrics

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type indexState struct {
	LastIdxScan    int64
	UnchangedSince time.Time
}

// IndexDelta reports how long an index's usage count has stayed
// unchanged -- i.e. how long since it was last actually used.
type IndexDelta struct {
	IndexRelID uint32
	SchemaName string
	TableName  string
	IndexName  string
	IdxScan    int64
	UnusedFor  time.Duration
}

// IndexPoller tracks, in memory, how long each index's idx_scan counter
// has stayed unchanged. Unlike Poller/TablePoller, this doesn't compare
// against just the immediately previous poll -- it remembers *when* the
// value last changed at all, across as many polls as it takes.
//
// State is in-memory only: a collector restart resets the clock for
// every index. A version that persists this to the store (surviving
// restarts) is deferred -- see ROADMAP.md.
type IndexPoller struct {
	previous map[uint32]indexState
}

// NewIndexPoller returns an IndexPoller with no prior state.
func NewIndexPoller() *IndexPoller {
	return &IndexPoller{previous: make(map[uint32]indexState)}
}

// PollDeltas polls pg_stat_user_indexes and, for each index, reports how
// long its idx_scan count has stayed unchanged since this poller started
// watching it (or since it was last actually used, whichever is more
// recent). An index seen for the first time -- or whose idx_scan has
// moved since the last poll, in either direction -- has its clock
// (re)started rather than being reported.
func (p *IndexPoller) PollDeltas(ctx context.Context, pool *pgxpool.Pool) ([]IndexDelta, error) {
	current, err := PollIndexes(ctx, pool)
	if err != nil {
		return nil, err
	}

	now := time.Now()
	var deltas []IndexDelta
	for _, idx := range current {
		prev, ok := p.previous[idx.IndexRelID]
		if !ok || idx.IdxScan != prev.LastIdxScan {
			p.previous[idx.IndexRelID] = indexState{LastIdxScan: idx.IdxScan, UnchangedSince: now}
			continue
		}

		deltas = append(deltas, IndexDelta{
			IndexRelID: idx.IndexRelID,
			SchemaName: idx.SchemaName,
			TableName:  idx.TableName,
			IndexName:  idx.IndexName,
			IdxScan:    idx.IdxScan,
			UnusedFor:  now.Sub(prev.UnchangedSince),
		})
	}

	return deltas, nil
}
