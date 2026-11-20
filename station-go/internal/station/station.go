package station

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"radio-on-stick/station-go/internal/library"
)

type Message struct {
	ID        string `json:"id"`
	Nickname  string `json:"nickname"`
	Body      string `json:"body"`
	CreatedAt int64  `json:"created_at"`
}

type History struct {
	ID        string `json:"id"`
	TrackID   string `json:"track_id"`
	Title     string `json:"title"`
	Artist    string `json:"artist"`
	CoverURL  string `json:"cover_url"`
	Nickname  string `json:"nickname"`
	CreatedAt int64  `json:"created_at"`
}

type QueueEntry struct {
	QueueID   int64  `json:"queue_id"`
	TrackID   string `json:"track_id"`
	Title     string `json:"title"`
	Artist    string `json:"artist"`
	CoverURL  string `json:"cover_url"`
	Nickname  string `json:"nickname"`
	Status    string `json:"status"`
	CreatedAt int64  `json:"created_at"`
	PinCount  int    `json:"pin_count"`
}

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
	messages []Message
	history  []History
	requests []QueueEntry
	nextID   int
	queueID  int64
	votes       map[string]bool
	settings    map[string]any
	policy      map[string]any
	persistPath string
	listeners   int
}

func New(index *library.Index, name string) *State {
	return &State{
		index: index, name: name, votes: map[string]bool{},
		settings: map[string]any{
			"station_name": name, "subtitle": "local library", "notice": "",
			"page_title": name, "background_interval_sec": 300,
		},
		policy: map[string]any{
			"max_listeners": 0, "mp3_upload_only": false, "mp3_remote_only": false,
			"requests_enabled": true, "uploads_enabled": true, "messages_enabled": true,
		},
	}
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
	for index := range s.requests {
		switch {
		case s.requests[index].TrackID == s.current.ID:
			s.requests[index].Status = "playing"
		case s.requests[index].Status == "playing":
			s.requests[index].Status = "played"
		}
	}
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
	s.votes = map[string]bool{}
	s.current = library.Track{}
	s.started = time.Time{}
	s.advanceLocked()
}

func (s *State) Request(track library.Track, nickname string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.current.ID == track.ID {
		return
	}
	s.nextID++
	s.history = append([]History{{
		ID: fmt.Sprintf("h%d", s.nextID), TrackID: track.ID, Title: track.Title,
		Artist: track.Artist, Nickname: nickname, CreatedAt: time.Now().UnixMilli(),
	}}, s.history...)
	if len(s.history) > 200 {
		s.history = s.history[:200]
	}
	for _, entry := range s.requests {
		if entry.TrackID == track.ID && entry.Status == "pending" {
			return
		}
	}
	s.queue = append([]library.Track{track}, s.queue...)
	s.queueID++
	s.requests = append(s.requests, QueueEntry{
		QueueID: s.queueID, TrackID: track.ID, Title: track.Title,
		Artist: track.Artist, Nickname: nickname, Status: "pending",
		CreatedAt: time.Now().UnixMilli(),
	})
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
		if s.started.IsZero() {
			s.started = now
		}
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
		source := track.Source
		if source == "" {
			source = "local"
		}
		direct := "/native/" + track.ID
		if track.URL != "" {
			direct = ""
		}
		return map[string]any{
			"id": track.ID, "source": source, "title": track.Title,
			"artist": track.Artist, "album": track.Album,
			"duration_ms": track.DurationMS, "cover_url": track.CoverURL,
			"audio": map[string]any{"direct": direct},
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
		"track": entry(s.current), "next": next, "listeners": s.listeners,
		"vote": map[string]any{"votes": len(s.votes), "needed": s.voteNeededLocked(), "voted": false},
	}
}

func (s *State) State() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tickLocked(time.Now())
	messages := make([]Message, len(s.messages))
	copy(messages, s.messages)
	history := make([]History, len(s.history))
	copy(history, s.history)
	requests := make([]QueueEntry, 0, len(s.requests))
	for _, entry := range s.requests {
		if entry.Status == "pending" {
			requests = append(requests, entry)
		}
	}
	return map[string]any{
		"now": s.SnapshotLocked(), "upcoming": requests, "queue": requests,
		"messages": messages, "history": history, "listeners": s.listeners,
	}
}

func (s *State) AddMessage(nickname, body string) Message {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nextID++
	message := Message{ID: fmt.Sprintf("m%d", s.nextID), Nickname: nickname, Body: body, CreatedAt: time.Now().UnixMilli()}
	s.messages = append(s.messages, message)
	if len(s.messages) > 200 {
		s.messages = s.messages[len(s.messages)-200:]
	}
	return message
}

func (s *State) Messages() []Message {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]Message, len(s.messages))
	copy(result, s.messages)
	return result
}

func (s *State) History() []History {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]History, len(s.history))
	copy(result, s.history)
	return result
}

func (s *State) Queue() []QueueEntry {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]QueueEntry, len(s.requests))
	copy(result, s.requests)
	return result
}

func (s *State) Cancel(queueID int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index, entry := range s.requests {
		if entry.QueueID == queueID {
			s.requests = append(s.requests[:index], s.requests[index+1:]...)
			return true
		}
	}
	return false
}

func (s *State) Pin(queueID int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index, entry := range s.requests {
		if entry.QueueID == queueID {
			entry.PinCount++
			s.requests = append([]QueueEntry{entry}, append(s.requests[:index], s.requests[index+1:]...)...)
			return true
		}
	}
	return false
}

func (s *State) Vote(client string) (bool, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.current.ID == "" || s.votes[client] {
		return false, false
	}
	s.votes[client] = true
	if len(s.votes) >= s.voteNeededLocked() {
		s.votes = map[string]bool{}
		s.current = library.Track{}
		s.started = time.Time{}
		s.advanceLocked()
		return true, true
	}
	return true, false
}

func (s *State) VoteSnapshot(client string) map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return map[string]any{"votes": len(s.votes), "needed": 3, "voted": s.votes[client]}
}

func (s *State) Settings() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := map[string]any{}
	for key, value := range s.settings {
		result[key] = value
	}
	return result
}

func (s *State) UpdateSettings(values map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, key := range []string{"station_name", "subtitle", "notice", "page_title", "background_interval_sec"} {
		if value, ok := values[key]; ok {
			s.settings[key] = value
			if key == "station_name" {
				s.name = fmt.Sprint(value)
			}
		}
	}
	s.saveLocked()
}

func (s *State) Policy() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := map[string]any{}
	for key, value := range s.policy {
		result[key] = value
	}
	return result
}

func (s *State) UpdatePolicy(values map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, value := range values {
		s.policy[key] = value
	}
	s.saveLocked()
}

func (s *State) ClearQueue() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = nil
}

func (s *State) DeleteMessage(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index, message := range s.messages {
		if message.ID == id {
			s.messages = append(s.messages[:index], s.messages[index+1:]...)
			return true
		}
	}
	return false
}

func (s *State) SetPersistPath(path string) {
	s.mu.Lock()
	s.persistPath = path
	raw, err := os.ReadFile(path)
	if err == nil {
		var payload struct {
			Settings map[string]any `json:"settings"`
			Policy   map[string]any `json:"policy"`
		}
		if json.Unmarshal(raw, &payload) == nil {
			for key, value := range payload.Settings {
				s.settings[key] = value
				if key == "station_name" {
					s.name = fmt.Sprint(value)
				}
			}
			for key, value := range payload.Policy {
				s.policy[key] = value
			}
		}
	}
	s.mu.Unlock()
}

func (s *State) saveLocked() {
	if s.persistPath == "" {
		return
	}
	_ = os.MkdirAll(filepath.Dir(s.persistPath), 0o755)
	raw, _ := json.MarshalIndent(map[string]any{
		"settings": s.settings, "policy": s.policy,
	}, "", "  ")
	_ = os.WriteFile(s.persistPath, raw, 0o600)
}

func (s *State) SetListeners(count int) {
	s.mu.Lock()
	if count < 1 {
		count = 1
	}
	s.listeners = count
	s.mu.Unlock()
}

func (s *State) voteNeededLocked() int {
	needed := s.listeners/2 + 1
	if needed < 2 {
		needed = 2
	}
	return needed
}

func (s *State) Name() string { return s.name }
