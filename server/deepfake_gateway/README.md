# Deepfake GPU Gateway

This standalone service wraps FaceFusion behind a versioned HTTPS/WSS API. It
must run with one Uvicorn worker because FaceFusion owns process-global model
state and realtime sessions are held in memory.

Security requirements:

- Bind only one TLS port (normally `443/tcp`) and restrict its security-group
  source range where possible.
- Use a private CA or a public certificate with hostname verification. Never set
  TLS verification to false in the Sere1nFish provider.
- Mount a random API token and TLS private key from root-only files. Do not put
  either value in Compose, Git, logs, or frontend code.
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
  optional `profile` field.
- `WS /v1/realtime/{session_id}`: JPEG frame input/output stream.
- `GET|DELETE /v1/sessions/{session_id}`: session metrics and cleanup.

Sere1nFish should call this service through `api.services.deepfake`; application
code must not call the gateway or FaceFusion directly.

Validate and deploy the standalone Compose project with an explicit project
name so Compose v1 and v2 retain the same container identity:

```bash
docker compose -p sere1nfish-deepfake -f compose.example.yaml config
docker compose -p sere1nfish-deepfake -f compose.example.yaml up -d --build
```
