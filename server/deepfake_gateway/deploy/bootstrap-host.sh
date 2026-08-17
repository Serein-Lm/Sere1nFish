#!/usr/bin/env bash
set -euo pipefail

PROXY_URL=""
SYSCTL_FILE=""
NVIDIA_TOOLKIT_VERSION="${NVIDIA_TOOLKIT_VERSION:-1.19.1-1}"

usage() {
  echo "Usage: $0 [--proxy-url http://127.0.0.1:17890] [--sysctl-file PATH]" >&2
}

while (($#)); do
  case "$1" in
    --proxy-url)
      PROXY_URL="${2:-}"
      shift 2
      ;;
    --sysctl-file)
      SYSCTL_FILE="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "bootstrap-host.sh must run as root" >&2
  exit 1
fi

if [[ -n "${PROXY_URL}" && ! "${PROXY_URL}" =~ ^http://127\.0\.0\.1:[0-9]{1,5}$ ]]; then
  echo "The GPU build proxy must use a loopback HTTP URL" >&2
  exit 2
fi
if [[ -n "${PROXY_URL}" ]]; then
  proxy_port="${PROXY_URL##*:}"
  if ((proxy_port < 1 || proxy_port > 65535)); then
    echo "Invalid GPU build proxy port" >&2
    exit 2
  fi
fi
if [[ -n "${SYSCTL_FILE}" && ! -f "${SYSCTL_FILE}" ]]; then
  echo "MediaMTX sysctl file does not exist: ${SYSCTL_FILE}" >&2
  exit 2
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Only x86_64 GPU hosts are supported" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
  echo "Ubuntu 22.04 or 24.04 is required" >&2
  exit 1
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA driver is missing. Recreate the node from a GPU image with a working driver." >&2
  exit 1
fi
nvidia-smi >/dev/null

if [[ -n "${PROXY_URL}" ]]; then
  export HTTP_PROXY="${PROXY_URL}"
  export HTTPS_PROXY="${PROXY_URL}"
  export http_proxy="${PROXY_URL}"
  export https_proxy="${PROXY_URL}"
  export NO_PROXY="localhost,127.0.0.1,::1,169.254.169.254"
  export no_proxy="${NO_PROXY}"
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git gnupg openssl zstd

if ! command -v docker >/dev/null 2>&1; then
  apt-get remove -y \
    docker.io docker-compose docker-compose-v2 docker-doc docker-buildx \
    podman-docker containerd runc 2>/dev/null || true
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-${VERSION_CODENAME}}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is unavailable" >&2
  exit 1
fi

if ! command -v nvidia-ctk >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    >/etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  if ! apt-get install -y \
    "nvidia-container-toolkit=${NVIDIA_TOOLKIT_VERSION}" \
    "nvidia-container-toolkit-base=${NVIDIA_TOOLKIT_VERSION}" \
    "libnvidia-container-tools=${NVIDIA_TOOLKIT_VERSION}" \
    "libnvidia-container1=${NVIDIA_TOOLKIT_VERSION}"; then
    apt-get install -y nvidia-container-toolkit
  fi
fi

nvidia-ctk runtime configure --runtime=docker
DOCKER_PROXY_FILE=/etc/systemd/system/docker.service.d/10-sere1nfish-proxy.conf
if [[ -n "${PROXY_URL}" ]]; then
  install -d -m 0755 /etc/systemd/system/docker.service.d
  cat >"${DOCKER_PROXY_FILE}" <<EOF
[Service]
Environment="HTTP_PROXY=${PROXY_URL}"
Environment="HTTPS_PROXY=${PROXY_URL}"
Environment="NO_PROXY=localhost,127.0.0.1,::1,169.254.169.254"
EOF
elif [[ -f "${DOCKER_PROXY_FILE}" ]]; then
  rm -f "${DOCKER_PROXY_FILE}"
fi
systemctl daemon-reload
systemctl enable --now docker
systemctl restart docker

if [[ -n "${SYSCTL_FILE}" ]]; then
  install -m 0644 "${SYSCTL_FILE}" /etc/sysctl.d/99-sere1nfish-mediamtx.conf
  sysctl --system >/dev/null
fi

docker run --rm --runtime=nvidia --gpus all \
  nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi >/dev/null

echo "GPU host bootstrap complete"
