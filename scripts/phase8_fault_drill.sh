#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUN_PHASE8_FAULT_DRILL:-}" != "1" ]]; then
  echo "Set RUN_PHASE8_FAULT_DRILL=1 to run the disruptive local compose drill."
  exit 2
fi

api_url="${ROUTEBOOK_API_URL:-http://localhost:8000}"

docker compose up -d --build
curl --fail --silent --show-error "${api_url}/health/ready" >/dev/null

for dependency in redis postgres; do
  docker compose stop "${dependency}"
  if curl --fail --silent --show-error "${api_url}/health/ready" >/dev/null; then
    echo "readiness unexpectedly stayed healthy while ${dependency} was stopped"
    docker compose start "${dependency}"
    exit 1
  fi
  docker compose start "${dependency}"
  docker compose up -d migrate api worker
  deadline=$((SECONDS + 60))
  until curl --fail --silent --show-error "${api_url}/health/ready" >/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "service did not recover after restarting ${dependency}"
      exit 1
    fi
    sleep 2
  done
done

docker compose restart worker
docker compose ps --status running worker | grep -q worker
echo "Phase 8 dependency and worker restart drill passed."
