#!/usr/bin/env sh
# Project-owned acceptance checks. The default path makes no paid calls.
set -eu
cd "$(dirname "$0")/.."
case "${1:-mock}" in
  mock)
    scratch="$(mktemp -d)"
    trap 'rm -r -- "$scratch"' EXIT
    export SELDON_DATA_DIR="$scratch" SELDON_SETTINGS_FILE="$scratch/settings.env"
    export MODEL_PROVIDER=mock SEARCH_PROVIDER=mock FINANCIAL_DATA_PROVIDER=mock
    unset NEBIUS_API_KEY TAVILY_API_KEY SEC_CONTACT_EMAIL SEC_USER_AGENT
    echo "Frontend clean install and build"
    case "$(uname -s)" in
      MINGW*|MSYS*) npm_command=npm.cmd ;;
      *) npm_command=npm ;;
    esac
    "$npm_command" ci --prefix frontend
    "$npm_command" run build --prefix frontend
    echo "Frontend regression tests"
    "$npm_command" test --prefix frontend
    echo "MOCK backend, API contract and storage"
    "${PYTHON:-python}" -m pytest -q backend/tests
    ;;
  catalog|minimal|structured|tools|search|joint|research)
    "${PYTHON:-python}" -m scripts.odsc_live "$1"
    ;;
  *)
    echo "Usage: $0 [mock|catalog|minimal|structured|tools|search|joint|research]" >&2
    exit 2
    ;;
esac
