#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CERT_DIR="$ROOT_DIR/nginx/certs"

mkdir -p "$CERT_DIR"

if [ -f "$CERT_DIR/local.crt" ] && [ -f "$CERT_DIR/local.key" ]; then
  echo "certificates already exist: $CERT_DIR"
  exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout "$CERT_DIR/local.key" \
  -out "$CERT_DIR/local.crt" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "created $CERT_DIR/local.crt and $CERT_DIR/local.key"
