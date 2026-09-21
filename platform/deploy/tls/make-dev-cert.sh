#!/usr/bin/env bash
#  A self-signed certificate for development.
#
#  Not for deployment. A browser will warn about it, and it should: nobody
#  vouched for it. What it is for is being able to run and test the TLS path
#  - the redirect, the headers, the proxy - rather than leaving it untested
#  until an institution hands over a real certificate.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
certs="$here/certs"
mkdir -p "$certs"

if [[ -f "$certs/privkey.pem" && "${1:-}" != "--force" ]]; then
  echo "이미 있습니다: $certs/privkey.pem (다시 만들려면 --force)"
  exit 0
fi

openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout "$certs/privkey.pem" \
  -out "$certs/fullchain.pem" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"

chmod 600 "$certs/privkey.pem"
echo "개발용 인증서를 만들었습니다: $certs"
echo "브라우저가 경고합니다. 자체 서명이므로 당연합니다."
