#!/usr/bin/env bash
# Records scripts/demo.sh with asciinema and renders it to docs/demo.gif
# (the recording embedded in README.md). Requires `asciinema` and `agg`
# (https://github.com/asciinema/agg), and a running docker-compose stack
# (`docker-compose up -d`) with a bootstrapped .env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

command -v asciinema >/dev/null || { echo "asciinema not found (brew install asciinema)" >&2; exit 1; }
command -v agg >/dev/null || { echo "agg not found (brew install agg)" >&2; exit 1; }

echo "--- restarting gatekeeper for a clean circuit-breaker/metrics slate ---"
docker-compose restart gatekeeper
sleep 3

echo "--- recording ---"
asciinema rec --command "bash scripts/demo.sh" --window-size 100x40 --overwrite docs/demo.cast

echo "--- rendering GIF ---"
agg --theme github-dark --font-size 14 --speed 1.3 --idle-time-limit 2 docs/demo.cast docs/demo.gif

rm -f docs/demo.cast
echo "--- done: docs/demo.gif ---"
