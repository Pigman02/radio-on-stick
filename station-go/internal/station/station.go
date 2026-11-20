package station

import (
	"sync"
	"time"

	"radio-on-stick/station-go/internal/library"
)

type State struct {
	mu       sync.RWMutex
	index    *library.Index
	name     string
	current  library.Track
	queue    []library.Track
	seq      int
	started  time.Time
	paused   bool
	audioRev int
}

func New(index *library.Index, name string) *State {
	return &State{index: index, name: name}
}

func (s *State) Rebuild() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queue = append([]library.Track(nil), s.index.List(50000)...)
	s.current = library.Track{}
	s.started = time.Time{}
	s.seq = 0
	s.advanceLocked()
}

func (s *State) advanceLocked() {
	if s.paused || s.current.ID != "" {
		return
	}
	if len(s.queue) == 0 {
		return
	}
	s.current = s.queue[0]
	s.queue = s.queue[1:]
	s.started = time.Now()
	s.seq++
	s.audioRev++
}

func (s *State) tickLocked(now time.Time) {
	if s.paused || s.current.ID == "" {
		return
	}
	if s.current.DurationMS <= 0 {
		return
	}
	if now.Sub(s.started) >= time.Duration(s.current.DurationMS)*time.Millisecond {
		s.current = library.Track{}
		s.started = time.Time{}
		s.advanceLocked()
	}
}

func (s *State) Tick() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tickLocked(time.Now())
}

func (s *State) Pause(paused bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.paused = paused
	if paused {
		return
	}
	if s.current.ID == "" {
		s.advanceLocked()
	}
}

func (s *State) Skip() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.current = library.Track{}
	s.started = time.Time{}
	s.advanceLocked()
}

func (s *State) Request(track library.Track) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, item := range s.queue {
		if item.ID == track.ID {
			return
		}
	}
	if s.current.ID == track.ID {
		return
	}
	s.queue = append([]library.Track{track}, s.queue...)
	s.advanceLocked()
}

func (s *State) Current() library.Track {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.current
}

func (s *State) Snapshot() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tickLocked(time.Now())
	return s.SnapshotLocked()
}

func (s *State) SnapshotLocked() map[string]any {
	now := time.Now()
	position := int64(0)
	startedMS := int64(0)
	if s.current.ID != "" {
		startedMS = s.started.UnixMilli()
		position = now.Sub(s.started).Milliseconds()
		if s.current.DurationMS > 0 && position > s.current.DurationMS {
			position = s.current.DurationMS
		}
	}
	entry := func(track library.Track) map[string]any {
		if track.ID == "" {
			return nil
		}
		return map[string]any{
			"id": track.ID, "source": "local", "title": track.Title,
			"artist": track.Artist, "album": track.Album,
			"duration_ms": track.DurationMS,
			"audio": map[string]any{"direct": "/native/" + track.ID},
		}
	}
	next := map[string]any(nil)
	if len(s.queue) > 0 {
		next = entry(s.queue[0])
	}
	return map[string]any{
		"seq": s.seq, "audio_rev": s.audioRev, "server_time_ms": now.UnixMilli(),
		"started_at_ms": startedMS, "position_ms": position,
		"duration_ms": s.current.DurationMS, "paused": s.paused,
		"track": entry(s.current), "next": next, "listeners": 1,
	}
}

func (s *State) State() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tickLocked(time.Now())
	upcoming := make([]library.Track, len(s.queue))
	copy(upcoming, s.queue)
	return map[string]any{
		"now": s.SnapshotLocked(), "upcoming": upcoming, "queue": []any{},
		"messages": []any{}, "history": []any{}, "listeners": 1,
	}
}

func (s *State) Name() string { return s.name }
