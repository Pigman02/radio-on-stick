package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"radio-on-stick/station-go/internal/library"
)

type api struct {
	index      *library.Index
	libraryDir []string
	adminToken string
	scanMu     sync.Mutex
}

func env(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func (a *api) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"ok": true, "service": "radio-station-go",
		"tracks": a.index.Count(), "time": time.Now().UTC().Format(time.RFC3339),
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func (a *api) authorized(r *http.Request) bool {
	if a.adminToken == "" {
		return false
	}
	auth := r.Header.Get("Authorization")
	return strings.HasPrefix(auth, "Bearer ") &&
		strings.TrimSpace(strings.TrimPrefix(auth, "Bearer ")) == a.adminToken
}

func (a *api) scan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "method"})
		return
	}
	if !a.authorized(r) {
		writeJSON(w, 401, map[string]any{"error": "unauthorized"})
		return
	}
	if !a.scanMu.TryLock() {
		writeJSON(w, 409, map[string]any{"error": "scan_in_progress"})
		return
	}
	defer a.scanMu.Unlock()
	count, err := a.index.Scan(a.libraryDir, 50000)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "scan_failed", "message": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "tracks": count})
}

func (a *api) search(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	writeJSON(w, 200, map[string]any{"tracks": a.index.Search(query, limit)})
}

func (a *api) list(w http.ResponseWriter, r *http.Request) {
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	writeJSON(w, 200, map[string]any{"tracks": a.index.List(limit)})
}

func (a *api) track(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/library/track/")
	track, ok := a.index.Get(id)
	if !ok {
		writeJSON(w, 404, map[string]any{"error": "not_found"})
		return
	}
	writeJSON(w, 200, track)
}

func (a *api) stream(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/library/stream/")
	track, ok := a.index.Get(id)
	if !ok || track.Path == "" {
		writeJSON(w, 404, map[string]any{"error": "not_found"})
		return
	}
	file, err := os.Open(track.Path)
	if err != nil {
		writeJSON(w, 404, map[string]any{"error": "not_found"})
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "stat_failed"})
		return
	}
	http.ServeContent(w, r, filepath.Base(track.Path), info.ModTime(), file)
}

func main() {
	dirs := strings.Split(env("RADIO_LIBRARY_DIRS", ""), string(os.PathListSeparator))
	clean := make([]string, 0, len(dirs))
	for _, dir := range dirs {
		if strings.TrimSpace(dir) != "" {
			clean = append(clean, strings.TrimSpace(dir))
		}
	}
	index := library.New()
	if len(clean) > 0 {
		if count, err := index.Scan(clean, 50000); err != nil {
			log.Printf("initial library scan: %v", err)
		} else {
			log.Printf("library indexed %d tracks", count)
		}
	}
	a := &api{
		index:      index,
		libraryDir: clean,
		adminToken: os.Getenv("RADIO_GO_ADMIN_TOKEN"),
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", a.health)
	mux.HandleFunc("/api/library/search", a.search)
	mux.HandleFunc("/api/library", a.list)
	mux.HandleFunc("/api/library/track/", a.track)
	mux.HandleFunc("/api/library/stream/", a.stream)
	mux.HandleFunc("/api/admin/library/scan", a.scan)
	addr := env("RADIO_GO_ADDR", "127.0.0.1:8091")
	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	log.Printf("radio station go listening on %s", addr)
	log.Fatal(server.ListenAndServe())
}
