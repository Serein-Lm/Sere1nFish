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
- `hyperswap_1a_256` with `512x512` pixel boost is configured for authorized
  research use. The gateway owns named quality profiles so API callers do not
  depend on FaceFusion processor names or model arguments.
- One to four source images are accepted under repeated `source` multipart
  fields. FaceFusion averages the largest detected face from each image; the
  gateway rejects missing faces and clearly inconsistent identities.
- `quality` is the default for image and realtime inference. It combines
  occlusion masking with GFPGAN 1.4 at a measured 60 percent blend. `balanced`
  keeps occlusion masking without restoration, while `fast` uses box masking
  only. All profiles use the benchmarked `0.65` source weight.

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
