#!/bin/sh
# Writes /config.js from API_BASE_URL before nginx starts (M3 8.3).
# Render's service reference gives a bare host name, so https:// is added when no scheme is set.
set -eu

value="${API_BASE_URL:-}"
value="$(printf '%s' "$value" | sed 's#/*$##')"
if [ -n "$value" ]; then
  case "$value" in
    http://*|https://*) ;;
    *) value="https://$value" ;;
  esac
  # Only URL characters, so the value can never break out of the JavaScript string.
  if ! printf '%s' "$value" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$'; then
    echo "40-bodega-config.sh: API_BASE_URL is not a valid http(s) URL" >&2
    exit 1
  fi
fi

printf "window.__BODEGA_CONFIG__ = { apiBaseUrl: '%s' }\n" "$value" > /usr/share/nginx/html/config.js
echo "40-bodega-config.sh: apiBaseUrl='${value}'"
