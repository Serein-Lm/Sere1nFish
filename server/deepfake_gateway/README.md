# Deepfake GPU Gateway

This standalone service wraps FaceFusion behind a versioned HTTPS/WSS API. It
must run with one Uvicorn worker because FaceFusion owns process-global model
state and realtime sessions are held in memory.

Security requirements:

- Caddy is the only public HTTP listener. Open `443/tcp` for WHIP/WHEP
  signaling and `8189/udp` plus `8189/tcp` for WebRTC ICE. Keep gateway
  `8443`, RTSP `8554`, WebRTC HTTP `8889`, API `9997` and metrics `9998`
  bound to loopback.
- Use a private CA or a public certificate with hostname verification. Never set
  TLS verification to false in the Sere1nFish provider.
- Mount a random API token and TLS private key from root-only files. Do not put
  either value in Compose, Git, logs, or frontend code.
- OBS publishes the camera and microphone in one WHIP session. The gateway
  decodes microphone audio to 16 kHz PCM, sends it to the loopback-only MeanVC
  runtime, and muxes converted speech as Opus into the processed H.264 output.
  The input words are preserved; this path does not call a conversational model.
- Converted audio retains only the latest 300 ms by default
  (`DEEPFAKE_MEDIA_AUDIO_BUFFER_MS`), so reconnect and startup bursts cannot
  leave a persistent stale-audio delay.
- Session deletion bounds media shutdown with
  `DEEPFAKE_MEDIA_SHUTDOWN_TIMEOUT_SECONDS` and releases the private MeanVC
  session even when an FFmpeg or WebSocket child does not stop promptly.
- MeanVC accepts a short-lived authorized reference sample when the session is
  created. Reference audio and microphone PCM remain in memory and are released
  with the session.
- `hyperswap_1a_256` is configured for authorized research use. The gateway
  owns named quality profiles so API callers do not depend on FaceFusion
  processor names or model arguments.
- One to four source images are accepted under repeated `source` multipart
  fields. FaceFusion averages the largest detected face from each image; the
  gateway rejects missing faces and clearly inconsistent identities.
- `quality` is the default for image inference. It uses a `768x768` pixel boost,
  YOLO 640 detection, 2DFAN4 landmarks, occlusion masking and a 1280-pixel
  input cap. Benchmarks showed that GFPGAN and CodeFormer reduced identity or
  temporal consistency, so restoration is not enabled by default.
- `fast` is the default for realtime inference. It uses a `256x256` pixel boost,
  SCRFD 320 detection, Peppa Wutz landmarks, box masking and a 640-pixel input
  cap. `balanced` retains a `512x512` middle profile for compatibility.
- All profiles use the benchmarked `0.65` source weight. On a Tesla T4 with
  TensorRT 10.9, the isolated benchmark measured about 16 FPS end to end for
  `fast` and 3.4 FPS for `quality`.
- TensorRT engine and timing caches are persisted under
  `/opt/facefusion/.caches`. Keep that mount across image upgrades and rebuild
  the cache whenever FaceFusion, ONNX Runtime, TensorRT, model files or GPU
  architecture changes.
- Runtime warmup loads the content analyser before readiness. This moves the
  multi-second first-use initialization cost into container startup.
- Source face metadata is prepared once per session. Target-frame face cache
  entries are cleared after every inference so long realtime sessions do not
  grow the process-global FaceFusion cache.

API surface:

- `GET /health`: unauthenticated liveness only.
- `GET /v1/status`: authenticated model/GPU/runtime status.
- `POST /v1/swap/image`: authenticated source/target image inference with an
  optional `profile` field.
- `POST /v1/sessions`: create an ephemeral realtime source session with an
  optional `profile`, `transport` (`frame_ws` or `obs_whip`) and authorized
  `voice_reference` sample for MeanVC conversion.
- `WS /v1/realtime/{session_id}`: JPEG frame input/output stream.
- `POST /internal/mediamtx/auth`: loopback-only per-session media
  authorization endpoint.
- `GET|DELETE /v1/sessions/{session_id}`: session metrics and cleanup.

OBS direct-session status includes a sanitized `media.publish` diagnostic block.
It reports publish-attempt counts, authorization outcome, source IP and protocol,
but never stores or returns the Bearer Token. Polling an owned session also keeps
the pending endpoint alive; abandoned sessions still expire after the configured
TTL.

The browser-capture fallback publishes its protected OBS viewer through the GPU
node's trusted HTTPS origin. `sere1nfish-media-output-relay.service` maintains a
loopback-only SSH reverse tunnel from GPU port `18443` to the application server's
local HTTPS port. Caddy proxies only `/api/v1/media-output/view` and
`/api/v1/media-output/watch` through this tunnel; no additional public port is
required.

Sere1nFish should call this service through `api.services.deepfake`; application
code must not call the gateway or FaceFusion directly.

Validate and deploy the standalone Compose project with an explicit project
name so Compose v1 and v2 retain the same container identity:

```bash
docker compose -p sere1nfish-deepfake -f compose.example.yaml config
docker compose -p sere1nfish-deepfake -f compose.example.yaml up -d --build
```

Before starting MediaMTX, install `sysctl-mediamtx.conf` under
`/etc/sysctl.d/99-sere1nfish-mediamtx.conf` and run `sysctl --system`. Caddy
keeps the legacy private-CA IP endpoint for Sere1nFish while automatically
issuing a public certificate for `DEEPFAKE_PUBLIC_HOST`. The public site
disables HTTP-01 and uses the TLS-ALPN-01 challenge on TCP `443`, so TCP `80`
does not need to be exposed. Certificate-authority validators must still be
able to reach TCP `443`; restrict application routes separately when the media
service must remain limited to approved clients.

The server-side SSH tunnel must additionally expose local nginx to GPU
loopback. The complete forwarding set is:

```text
-L 172.18.0.1:18443:127.0.0.1:443
-R 127.0.0.1:17890:43.106.0.54:18818
```
