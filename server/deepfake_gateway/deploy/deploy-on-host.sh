#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/opt/sere1nfish/deepfake"
RELEASE_DIR=""
ENV_FILE=""
BUNDLE_DIR=""

usage() {
  echo "Usage: $0 --release-dir PATH --env-file PATH [--root-dir PATH] [--bundle-dir PATH]" >&2
}

while (($#)); do
  case "$1" in
    --root-dir)
      ROOT_DIR="${2:-}"
      shift 2
      ;;
    --release-dir)
      RELEASE_DIR="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --bundle-dir)
      BUNDLE_DIR="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "deploy-on-host.sh must run as root" >&2
  exit 1
fi
if [[ -z "${RELEASE_DIR}" || -z "${ENV_FILE}" ]]; then
  usage
  exit 2
fi
if [[ ! -f "${RELEASE_DIR}/deepfake_gateway/compose.example.yaml" ]]; then
  echo "Release does not contain deepfake_gateway" >&2
  exit 1
fi
if [[ ! -f "${RELEASE_DIR}/meanvc_runtime/Dockerfile" ]]; then
  echo "Release does not contain meanvc_runtime" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

: "${FACEFUSION_COMMIT:?FACEFUSION_COMMIT is required}"
: "${FACEFUSION_BASE_IMAGE:?FACEFUSION_BASE_IMAGE is required}"
: "${DEEPFAKE_GATEWAY_IMAGE:?DEEPFAKE_GATEWAY_IMAGE is required}"

install -d -m 0755 \
  "${ROOT_DIR}/data/assets" \
  "${ROOT_DIR}/data/cache" \
  "${ROOT_DIR}/data/meanvc" \
  "${ROOT_DIR}/data/caddy" \
  "${ROOT_DIR}/data/caddy-config" \
  "${ROOT_DIR}/data/output" \
  "${ROOT_DIR}/data/temp" \
  "${ROOT_DIR}/data/jobs"
install -d -m 0700 "${ROOT_DIR}/secrets"

if [[ -n "${BUNDLE_DIR}" && -f "${BUNDLE_DIR}/SHA256SUMS" ]]; then
  (
    cd "${BUNDLE_DIR}"
    sha256sum --check SHA256SUMS
  )
  for archive in facefusion-assets.tar.zst meanvc-models.tar.zst; do
    if [[ -f "${BUNDLE_DIR}/${archive}" ]]; then
      tar --zstd -xf "${BUNDLE_DIR}/${archive}" -C "${ROOT_DIR}/data"
    fi
  done
  compute_capability="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n 1 | tr -d ' ')"
  if [[ "${compute_capability}" == "7.5" && -f "${BUNDLE_DIR}/tensorrt-sm75.tar.zst" ]]; then
    tar --zstd -xf "${BUNDLE_DIR}/tensorrt-sm75.tar.zst" -C "${ROOT_DIR}/data"
  fi
fi

FACEFUSION_DIR="${RELEASE_DIR}/facefusion"
if [[ ! -f "${FACEFUSION_DIR}/.git-commit" ]] \
  || [[ "$(cat "${FACEFUSION_DIR}/.git-commit")" != "${FACEFUSION_COMMIT}" ]]; then
  rm -rf "${FACEFUSION_DIR}"
  git_proxy_args=()
  if [[ -n "${HTTP_PROXY:-}" ]]; then
    git_proxy_args=(-c "http.proxy=${HTTP_PROXY}" -c "https.proxy=${HTTPS_PROXY:-${HTTP_PROXY}}")
  fi
  git "${git_proxy_args[@]}" init "${FACEFUSION_DIR}"
  git -C "${FACEFUSION_DIR}" remote add origin https://github.com/facefusion/facefusion.git
  git -C "${FACEFUSION_DIR}" "${git_proxy_args[@]}" fetch --depth 1 origin "${FACEFUSION_COMMIT}"
  git -C "${FACEFUSION_DIR}" checkout --detach FETCH_HEAD
  test "$(git -C "${FACEFUSION_DIR}" rev-parse HEAD)" = "${FACEFUSION_COMMIT}"
  printf '%s\n' "${FACEFUSION_COMMIT}" >"${FACEFUSION_DIR}/.git-commit"
  rm -rf "${FACEFUSION_DIR}/.git"
fi

base_revision="$(docker image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  "${FACEFUSION_BASE_IMAGE}" 2>/dev/null || true)"
if [[ "${base_revision}" != "${FACEFUSION_COMMIT}" ]]; then
  docker build \
    --network host \
    --file "${RELEASE_DIR}/deepfake_gateway/Dockerfile.facefusion-base" \
    --tag "${FACEFUSION_BASE_IMAGE}" \
    --build-arg "FACEFUSION_COMMIT=${FACEFUSION_COMMIT}" \
    --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
    --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
    --build-arg "NO_PROXY=${NO_PROXY:-localhost,127.0.0.1,::1}" \
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.org/simple}" \
    "${RELEASE_DIR}"
fi

export DEEPFAKE_FACEFUSION_CONFIG_FILE="${RELEASE_DIR}/deepfake_gateway/facefusion.ini"
compose=(
  docker compose
  --project-name sere1nfish-deepfake
  --env-file "${ENV_FILE}"
  --file "${RELEASE_DIR}/deepfake_gateway/compose.example.yaml"
)

"${compose[@]}" config >/dev/null
"${compose[@]}" build meanvc gateway
"${compose[@]}" up -d --remove-orphans

deadline=$((SECONDS + 900))
until curl --fail --silent http://127.0.0.1:8443/health >/dev/null; do
  if ((SECONDS >= deadline)); then
    "${compose[@]}" ps
    "${compose[@]}" logs --tail 200 gateway meanvc
    echo "GPU gateway did not become ready within 900 seconds" >&2
    exit 1
  fi
  sleep 5
done

api_token="$(tr -d '\r\n' <"${DEEPFAKE_TOKEN_FILE}")"
curl --fail --silent \
  --header "Authorization: Bearer ${api_token}" \
  http://127.0.0.1:8443/v1/status >/dev/null

if ss -ltnH | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\[::\]):(8443|8554|8766|8889|9997|9998)([[:space:]]|$)'; then
  echo "A private model port is listening on a public address" >&2
  exit 1
fi

ln -sfn "${RELEASE_DIR}" "${ROOT_DIR}/current"
"${compose[@]}" ps
echo "GPU release is ready: ${RELEASE_DIR}"
