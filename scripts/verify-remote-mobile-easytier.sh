#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

cd "$ROOT_DIR"

echo "== EasyTier compose config =="
docker-compose -f docker-compose.yml config >/tmp/sere1nfish-easytier-compose.yml
grep -q "easytier-server:" /tmp/sere1nfish-easytier-compose.yml
grep -q "easytier-backend-peer:" /tmp/sere1nfish-easytier-compose.yml
grep -q "network_mode: service:backend" /tmp/sere1nfish-easytier-compose.yml
grep -q "11010:11010/tcp" /tmp/sere1nfish-easytier-compose.yml
grep -q "11010:11010/udp" /tmp/sere1nfish-easytier-compose.yml
grep -q "11011:11011/tcp" /tmp/sere1nfish-easytier-compose.yml
grep -q "11012:11012/tcp" /tmp/sere1nfish-easytier-compose.yml
grep -q "11013:11013/udp" /tmp/sere1nfish-easytier-compose.yml
echo "compose ok"

echo "== EasyTier access profile defaults =="
python3 - <<'PY'
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "sere1nfish_easytier", "server/core/mobile/easytier.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

for key in (
    "EASYTIER_PUBLIC_HOST",
    "EASYTIER_NETWORK_SECRET",
    "MOBILE_AGENT_ANDROID_URL",
):
    os.environ.pop(key, None)

profile = mod.build_easytier_access_profile("127.0.0.1:443")
assert profile.public_host == "127.0.0.1", profile.public_host
assert profile.network_name == "sere1nfish-mobile", profile.network_name
assert profile.backend_peer_ipv4 == "10.144.144.1", profile.backend_peer_ipv4
assert profile.auto_scan_enabled is True
assert profile.qr_payload["schema"] == "sere1nfish.mobile.easytier.v1"
assert "easytier_adb_scan" in profile.qr_payload["discovery"]["modes"]
assert any("默认值" in item for item in profile.warnings), profile.warnings
print("default profile ok")
PY

echo "== EasyTier access profile production sample =="
EASYTIER_PUBLIC_HOST="${EASYTIER_PUBLIC_HOST:-203.0.113.10}" \
EASYTIER_NETWORK_SECRET="${EASYTIER_NETWORK_SECRET:-sample-secret-not-for-prod}" \
MOBILE_AGENT_ANDROID_URL="${MOBILE_AGENT_ANDROID_URL:-https://example.com/mobile-agent.apk}" \
python3 - <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "sere1nfish_easytier_prod", "server/core/mobile/easytier.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

profile = mod.build_easytier_access_profile("127.0.0.1:443")
assert profile.public_host, "public host missing"
assert profile.peers[0].startswith("tcp://"), profile.peers
assert profile.network_secret != "change-me-before-production"
assert profile.agent_download_url, "agent download missing"
assert profile.backend_peer_ipv4 == "10.144.144.1", profile.backend_peer_ipv4
assert not profile.warnings, profile.warnings
print("production sample profile ok")
PY

echo "== Python syntax =="
python3 -m py_compile \
  server/api/routers/mobile.py \
  server/api/routers/downloads.py \
  server/core/mobile/easytier.py \
  server/core/mobile/pool.py
echo "python ok"

if [ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]; then
  echo "== Frontend build =="
  (cd view && npm run build)
fi

echo "remote mobile EasyTier verification passed"
