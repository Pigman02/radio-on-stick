package stream

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"os/exec"
	"sync"
	"time"

	"radio-on-stick/station-go/internal/library"
)

const (
	frameMagic      = "RAD4"
	frameHeaderSize = 40
	chunkMS         = 500
	ringSize        = 24
)

var bitrates = [2][3][15]int{
	{
		{0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448},
		{0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384},
		{0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320},
	},
	{
		{0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256},
		{0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160},
		{0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160},
	},
}
var sampleRates = [3][3]int{{44100, 48000, 32000}, {22050, 24000, 16000}, {11025, 12000, 8000}}

type frameInfo struct {
	length     int
	durationMS int
}

func parseMP3Frame(data []byte) *frameInfo {
	if len(data) < 4 || data[0] != 0xff || data[1]&0xe0 != 0xe0 {
		return nil
	}
	version := int((data[1] >> 3) & 3)
	layer := int((data[1] >> 1) & 3)
	if version == 1 || layer == 0 {
		return nil
	}
	bitrateIndex := int(data[2] >> 4)
	sampleIndex := int((data[2] >> 2) & 3)
	padding := int((data[2] >> 1) & 1)
	if bitrateIndex == 0 || bitrateIndex == 15 || sampleIndex == 3 {
		return nil
	}
	vi := 1
	si := 1
	switch version {
	case 3:
		vi, si = 0, 0
	case 2:
		vi, si = 1, 1
	case 0:
		vi, si = 1, 2
	default:
		return nil
	}
	li := 4 - layer
	if li < 1 || li > 3 {
		return nil
	}
	bitrate := bitrates[vi][li-1][bitrateIndex] * 1000
	rate := sampleRates[si][sampleIndex]
	if bitrate == 0 || rate == 0 {
		return nil
	}
	frameLen := 0
	samples := 1152
	switch layer {
	case 1: // Layer III
		frameLen = (144*bitrate)/rate + padding
	case 2: // Layer II
		frameLen = (72*bitrate)/rate + padding
	case 3: // Layer I
		frameLen = (48*bitrate)/rate + padding
		samples = 384
	default:
		return nil
	}
	if frameLen < 4 || frameLen > 8192 {
		return nil
	}
	return &frameInfo{length: frameLen, durationMS: samples * 1000 / rate}
}

type Chunk struct {
	Seq        uint32
	StartMS    int
	DurationMS int
	ServerMS   float64
	Payload    []byte
}

type Session struct {
	mu       sync.Mutex
	cond     *sync.Cond
	streamID uint32
	track    library.Track
	ring     []Chunk
	nextSeq  uint32
	startMS  int
	eof      bool
	err      error
	closed   bool
	cmd      *exec.Cmd
	duration int64
}

func NewSession(track library.Track) (*Session, error) {
	input := track.Path
	if track.URL != "" {
		input = track.URL
	}
	cmd := exec.Command("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", input, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	s := &Session{
		streamID: uint32(rand.Uint32()&0x7fffffff) + 1,
		track:    track,
		nextSeq:  0,
		cmd:      cmd,
		duration: track.DurationMS,
	}
	s.cond = sync.NewCond(&s.mu)
	go s.readLoop(stdout)
	return s, nil
}

func (s *Session) readLoop(reader io.ReadCloser) {
	defer reader.Close()
	buffered := bufio.NewReaderSize(reader, 64*1024)
	buffer := make([]byte, 0, 128*1024)
	var current []byte
	var duration int
	emit := func() {
		if len(current) == 0 {
			return
		}
		s.mu.Lock()
		chunk := Chunk{Seq: s.nextSeq, StartMS: s.startMS, DurationMS: duration, ServerMS: float64(time.Now().UnixMilli()), Payload: append([]byte(nil), current...)}
		s.nextSeq++
		s.startMS += duration
		s.ring = append(s.ring, chunk)
		if len(s.ring) > ringSize {
			s.ring = s.ring[len(s.ring)-ringSize:]
		}
		s.cond.Broadcast()
		s.mu.Unlock()
		current = nil
		duration = 0
	}
	for {
		buf := make([]byte, 64*1024)
		n, err := buffered.Read(buf)
		if n > 0 {
			buffer = append(buffer, buf[:n]...)
			for {
				if len(buffer) >= 3 && string(buffer[:3]) == "ID3" {
					if len(buffer) < 10 {
						break
					}
					size := int(buffer[6])<<21 | int(buffer[7])<<14 | int(buffer[8])<<7 | int(buffer[9])
					if len(buffer) < size+10 {
						break
					}
					buffer = buffer[size+10:]
					continue
				}
				frame := parseMP3Frame(buffer)
				if frame == nil {
					if len(buffer) > 1 {
						buffer = buffer[1:]
						continue
					}
					break
				}
				if len(buffer) < frame.length {
					break
				}
				current = append(current, buffer[:frame.length]...)
				duration += frame.durationMS
				buffer = buffer[frame.length:]
				if duration >= chunkMS || len(current) >= 48*1024 {
					emit()
				}
			}
		}
		if err != nil {
			emit()
			s.mu.Lock()
			s.eof = true
			s.cond.Broadcast()
			s.mu.Unlock()
			_ = s.cmd.Wait()
			return
		}
	}
}

func (s *Session) Manifest() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	first := s.nextSeq
	if len(s.ring) > 0 {
		first = s.ring[0].Seq
	}
	latest := int64(s.nextSeq) - 1
	return map[string]any{
		"ok": true, "supported": true, "stream_id": s.streamID,
		"format": "mp3", "mime": "audio/mpeg", "header_bytes": frameHeaderSize,
		"chunk_ms": chunkMS, "first_seq": first, "latest_seq": latest,
		"from_seq": int64(first), "start_position_ms": 0,
		"started_at_ms": time.Now().Add(-time.Duration(s.startMS) * time.Millisecond).UnixMilli(),
		"server_time_ms": time.Now().UnixMilli(), "duration_ms": s.duration,
		"paused": false, "eof": s.eof,
	}
}

func fnv1a(data []byte) uint32 {
	hash := uint32(0x811c9dc5)
	for _, value := range data {
		hash ^= uint32(value)
		hash *= 0x01000193
	}
	return hash
}

func (s *Session) Chunk(seq uint32) ([]byte, error) {
	deadline := time.Now().Add(8 * time.Second)
	s.mu.Lock()
	defer s.mu.Unlock()
	for {
		for _, chunk := range s.ring {
			if chunk.Seq == seq {
				header := make([]byte, frameHeaderSize)
				copy(header[0:4], frameMagic)
				binary.BigEndian.PutUint32(header[4:8], s.streamID)
				binary.BigEndian.PutUint32(header[8:12], chunk.Seq)
				binary.BigEndian.PutUint32(header[12:16], uint32(len(chunk.Payload)))
				binary.BigEndian.PutUint32(header[16:20], uint32(chunk.StartMS))
				binary.BigEndian.PutUint32(header[20:24], uint32(chunk.DurationMS))
				binary.BigEndian.PutUint64(header[24:32], uint64(chunk.ServerMS))
				binary.BigEndian.PutUint32(header[32:36], fnv1a(chunk.Payload))
				header[36] = 1
				return append(header, chunk.Payload...), nil
			}
		}
		if seq < s.nextSeq {
			return nil, errors.New("stream gone")
		}
		if s.eof {
			return nil, io.EOF
		}
		if s.err != nil {
			return nil, s.err
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("chunk timeout")
		}
		s.cond.Wait()
	}
}

func (s *Session) Close() {
	s.mu.Lock()
	s.closed = true
	s.cond.Broadcast()
	s.mu.Unlock()
	if s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
	}
}

type Hub struct {
	mu      sync.Mutex
	session *Session
	trackID string
}

func NewHub() *Hub { return &Hub{} }

func (h *Hub) Manifest(track library.Track) (map[string]any, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if track.ID == "" {
		return nil, io.EOF
	}
	if h.session == nil || h.trackID != track.ID {
		if h.session != nil {
			h.session.Close()
		}
		session, err := NewSession(track)
		if err != nil {
			return nil, err
		}
		h.session = session
		h.trackID = track.ID
	}
	return h.session.Manifest(), nil
}

func (h *Hub) Chunk(streamID, seq uint32) ([]byte, error) {
	h.mu.Lock()
	session := h.session
	h.mu.Unlock()
	if session == nil || session.streamID != streamID {
		return nil, io.EOF
	}
	return session.Chunk(seq)
}

func (h *Hub) Close() {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.session != nil {
		h.session.Close()
		h.session = nil
	}
}
