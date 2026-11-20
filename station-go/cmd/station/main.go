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
	"radio-on-stick/station-go/internal/mesh"
	"radio-on-stick/station-go/internal/station"
	"radio-on-stick/station-go/internal/stream"
)

type api struct {
	index      *library.Index
	libraryDir []string
	adminToken string
	scanMu     sync.Mutex
	state      *station.State
	source     *stream.Hub
	mesh       *mesh.Hub
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
	a.state.Rebuild()
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

func (a *api) now(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, a.state.Snapshot())
}

func (a *api) stateJSON(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, a.state.State())
}

func (a *api) config(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"station_name": "Go Radio Station", "subtitle": "local library",
		"page_title": "Go Radio Station", "notice": "",
		"background_interval_sec": 300, "backgrounds": []any{},
	})
}

func (a *api) request(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "method"})
		return
	}
	var body struct {
		TrackID string `json:"track_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, 400, map[string]any{"error": "bad_json"})
		return
	}
	track, ok := a.index.Get(body.TrackID)
	if !ok {
		writeJSON(w, 404, map[string]any{"error": "not_found"})
		return
	}
	a.state.Request(track)
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *api) skip(w http.ResponseWriter, r *http.Request) {
	if !a.authorized(r) {
		writeJSON(w, 401, map[string]any{"error": "unauthorized"})
		return
	}
	a.state.Skip()
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *api) pause(w http.ResponseWriter, r *http.Request) {
	if !a.authorized(r) {
		writeJSON(w, 401, map[string]any{"error": "unauthorized"})
		return
	}
	var body struct {
		Paused bool `json:"paused"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, 400, map[string]any{"error": "bad_json"})
		return
	}
	a.state.Pause(body.Paused)
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *api) meshStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, a.mesh.Status())
}

func (a *api) sourceManifest(w http.ResponseWriter, r *http.Request) {
	track := a.state.Current()
	manifest, err := a.source.Manifest(track)
	if err != nil {
		writeJSON(w, 409, map[string]any{"error": "no_stream"})
		return
	}
	writeJSON(w, 200, manifest)
}

func (a *api) sourceChunk(w http.ResponseWriter, r *http.Request) {
	streamID, _ := strconv.ParseUint(r.URL.Query().Get("stream_id"), 10, 32)
	seq, _ := strconv.ParseUint(r.URL.Query().Get("seq"), 10, 32)
	frame, err := a.source.Chunk(uint32(streamID), uint32(seq))
	if err != nil {
		writeJSON(w, 404, map[string]any{"error": "chunk_unavailable"})
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.Itoa(len(frame)))
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(200)
	_, _ = w.Write(frame)
}

func (a *api) native(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/native/")
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
	state := station.New(index, env("RADIO_STATION_NAME", "Go Radio Station"))
	state.Rebuild()
	a := &api{
		index:      index,
		libraryDir: clean,
		adminToken: os.Getenv("RADIO_GO_ADMIN_TOKEN"),
		state:      state,
		source:     stream.NewHub(),
		mesh:       mesh.NewHub(),
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", a.health)
	mux.HandleFunc("/now.json", a.now)
	mux.HandleFunc("/api/state", a.stateJSON)
	mux.HandleFunc("/api/config", a.config)
	mux.HandleFunc("/api/request", a.request)
	mux.HandleFunc("/api/admin/skip", a.skip)
	mux.HandleFunc("/api/admin/pause", a.pause)
	mux.HandleFunc("/native/", a.native)
	mux.HandleFunc("/ws", a.mesh.ServeHTTP)
	mux.HandleFunc("/mesh.json", a.meshStatus)
	mux.HandleFunc("/source/manifest", a.sourceManifest)
	mux.HandleFunc("/source/chunk", a.sourceChunk)
	mux.HandleFunc("/api/library/search", a.search)
	mux.HandleFunc("/api/library", a.list)
	mux.HandleFunc("/api/library/track/", a.track)
	mux.HandleFunc("/api/library/stream/", a.stream)
	mux.HandleFunc("/api/admin/library/scan", a.scan)
	addr := env("RADIO_GO_ADDR", "127.0.0.1:8091")
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for range ticker.C {
			state.Tick()
		}
	}()
	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	log.Printf("radio station go listening on %s", addr)
	log.Fatal(server.ListenAndServe())
}
