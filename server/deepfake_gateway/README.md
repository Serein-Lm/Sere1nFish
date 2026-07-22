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
- `simswap_unofficial_512` is configured for authorized non-commercial use.

API surface:

- `GET /health`: unauthenticated liveness only.
- `GET /v1/status`: authenticated model/GPU/runtime status.
- `POST /v1/swap/image`: authenticated source/target image inference.
- `POST /v1/sessions`: create an ephemeral realtime source session.
- `WS /v1/realtime/{session_id}`: JPEG frame input/output stream.
- `GET|DELETE /v1/sessions/{session_id}`: session metrics and cleanup.

Sere1nFish should call this service through `api.services.deepfake`; application
code must not call the gateway or FaceFusion directly.
