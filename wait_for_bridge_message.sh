#!/usr/bin/env bash
set -euo pipefail

bridge_url="${BRIDGE_URL:-http://192.168.50.1:58081}"
token_file="${BRIDGE_TOKEN_FILE:-${XDG_STATE_HOME:-${HOME}/.local/state}/agent-bridge/token}"
recipient="ubuntu"
after=0
timeout_seconds=300
poll_seconds=2

usage() {
  echo "Usage: $0 [--after ID] [--recipient ubuntu|windows] [--timeout SECONDS] [--poll SECONDS]"
}

while (($# > 0)); do
  case "$1" in
    --after)
      after="$2"
      shift 2
      ;;
    --recipient)
      recipient="$2"
      shift 2
      ;;
    --timeout)
      timeout_seconds="$2"
      shift 2
      ;;
    --poll)
      poll_seconds="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$recipient" != "ubuntu" && "$recipient" != "windows" ]]; then
  echo "recipient must be ubuntu or windows" >&2
  exit 2
fi
if ! [[ "$after" =~ ^[0-9]+$ && "$timeout_seconds" =~ ^[0-9]+$ && "$poll_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "after/timeout must be non-negative integers and poll must be positive" >&2
  exit 2
fi
if [[ ! -r "$token_file" ]]; then
  echo "Bridge token file is not readable: $token_file" >&2
  exit 2
fi

read -r bridge_token < "$token_file"
deadline=$((SECONDS + timeout_seconds))

while ((SECONDS <= deadline)); do
  response="$(
    curl -fsS \
      "${bridge_url}/v1/messages?recipient=${recipient}&after=${after}" \
      -H "Authorization: Bearer ${bridge_token}"
  )"
  if [[ "$(jq '.messages | length' <<<"$response")" -gt 0 ]]; then
    jq . <<<"$response"
    exit 0
  fi
  sleep "$poll_seconds"
done

echo "Timed out after ${timeout_seconds}s waiting for recipient=${recipient} after=${after}" >&2
exit 124
