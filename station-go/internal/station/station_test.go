package station

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"radio-on-stick/station-go/internal/library"
)

func TestPauseFreezesAuthoritativePosition(t *testing.T) {
	s := New(library.New(), "test")
	s.current = library.Track{ID: "track", Title: "Track", DurationMS: 60000}
	s.started = time.Now().Add(-3 * time.Second)
	s.seq = 1
	s.Pause(true)
	first := s.PositionMS()
	time.Sleep(30 * time.Millisecond)
	second := s.PositionMS()
	if first != second {
		t.Fatalf("position advanced while paused: %d -> %d", first, second)
	}
	s.Pause(false)
	time.Sleep(20 * time.Millisecond)
	if resumed := s.PositionMS(); resumed <= first {
		t.Fatalf("position did not resume: %d -> %d", first, resumed)
	}
}

func TestQueueCancelRequiresOwner(t *testing.T) {
	s := New(library.New(), "test")
	s.Request(library.Track{ID: "track", Title: "Track", DurationMS: 1000}, "alice", "client-a")
	queue := s.Queue()
	if len(queue) != 1 || queue[0].QueueID == 0 {
		t.Fatalf("request was not queued: %#v", queue)
	}
	if s.Cancel(queue[0].QueueID, "client-b") {
		t.Fatal("another client cancelled the request")
	}
	if !s.Cancel(queue[0].QueueID, "client-a") {
		t.Fatal("owner could not cancel the request")
	}
}

func TestStatePersistenceIncludesHistoryAndMessages(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	s := New(library.New(), "test")
	s.SetPersistPath(path)
	s.AddMessage("alice", "hello")
	s.Request(library.Track{ID: "track", Title: "Track", DurationMS: 1000}, "alice", "client-a")
	if err := os.WriteFile(path, mustJSON(s), 0o600); err != nil {
		t.Fatal(err)
	}
	loaded := New(library.New(), "test")
	loaded.SetPersistPath(path)
	if got := len(loaded.Messages()); got != 1 {
		t.Fatalf("messages not restored: %d", got)
	}
	if got := len(loaded.History()); got == 0 {
		t.Fatal("history not restored")
	}
	if got := len(loaded.Queue()); got == 0 {
		t.Fatal("queue not restored")
	}
}

func mustJSON(s *State) []byte {
	s.mu.RLock()
	defer s.mu.RUnlock()
	raw, err := os.ReadFile(s.persistPath)
	if err != nil {
		panic(err)
	}
	return raw
}
