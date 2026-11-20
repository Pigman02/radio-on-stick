# Go Station cutover from Python

The Go station uses the same browser shell and the same private source helper.
It keeps its durable state in `data/station-go-state.json`.

## 1. Export the Python station state

With the Python station stopped or idle:

```bash
python3 tools/export_python_state.py \
  --db data/radio.db \
  --out data/station-go-state.json \
  --uploads data/uploads
```

This exports:

- current track and position
- pending queue and requests
- history
- visible messages
- station title/subtitle/notice/background interval

## 2. Back up the current Go state

```bash
cp data/station-go-state.json data/station-go-state.json.bak
```

## 3. Start the Go station on the Python port

Stop the Python process, then point the Nginx/Relay upstream at the Go station:

```bash
RADIO_GO_ADDR=127.0.0.1:8090 \
RADIO_GO_STATE=data/station-go-state.json \
RADIO_LIBRARY_DIRS=data/library \
RADIO_GO_UPLOADS=data/uploads \
data/campus/bin/radio-station-go
```

The Go station will restore the current track, position, queue, history and
messages from the exported state.

## 4. Verify

```bash
curl -s http://127.0.0.1:8090/healthz
curl -s http://127.0.0.1:8090/now.json
curl -s http://127.0.0.1:8090/api/state
```

Open the `/admin` page from the server and verify:

- current track and progress
- queue and history
- messages
- directory invite code
- library settings
- pause/resume

## 5. Rollback

Stop Go and start Python with the original `RADIO_PORT=8090`. The Python
database was not modified by the exporter.

## Notes

- The Go station does not import local music files from the Python database;
  they remain in `data/library` / `data/uploads` and are scanned normally.
- Remote tracks keep their server-side audio URL in the state file.
- macOS may require `codesign --force --sign - data/campus/bin/radio-station-go`
  after copying a cross-built binary from the package.
