#!/usr/bin/env bash
set -euo pipefail

uv --version
uv sync --locked --extra dev

npm --prefix tests/e2e ci
npm --prefix tests/e2e exec -- playwright install --with-deps chromium
