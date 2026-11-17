# Browser mesh E2E

This is an optional local test (not part of `unittest`). It needs Node 22+ and a
Chrome/Chromium binary; the Python fixture generates a synthetic MP3 stream and
does not use ffmpeg or any pip package.

```bash
# terminal 1
python3 tests/e2e/server.py --port 8795

# terminal 2
node tests/e2e/mesh.js
```

Override `E2E_APP`, `E2E_PAGES`, `CHROME_BIN` or `E2E_CDP_PORT` if needed.
The script opens separate Chrome processes so every page is an active tab,
then checks dual seeds, dual parents, duplicate suppression, sync spread,
seed failure/promotion, and continued playback.

## No-WebRTC HTTP leaf E2E

```bash
python3 tests/e2e/server.py --port 8795
node tests/e2e/http_leaf.js
```

The fixture accepts `--http-leaf-max 0` to force the admission cap and
exercise the app's native direct fallback.

This exposes one Chrome page with `RTCPeerConnection` deleted before the app
loads.  With the default MP3 fixture it asserts `role=http`, continued
`/source` fetching, and playback without claiming to be a P2P child.  With a
container that MSE cannot initialize, the same script accepts the app's
intentional server-side `/native` `<audio>` fallback once playback starts.
