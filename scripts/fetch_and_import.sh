#!/usr/bin/env bash
# Fetch every new posting link in data/inbox/links/*.txt and import it into data/jobs.csv.
#
# Usage:
#   bash scripts/fetch_and_import.sh              # new links only (fetched before = read from data/raw/)
#   bash scripts/fetch_and_import.sh --dry-run    # fetch and show the result; write nothing
#   bash scripts/fetch_and_import.sh --refresh    # fetch every link again
#   bash scripts/fetch_and_import.sh path/to/links.txt
#
# Also imports hand-pasted postings in data/inbox/manual/ (for Indeed, or pages that
# cannot be fetched), unless --links-only is given.
set -euo pipefail
cd "$(dirname "$0")/.."

links_args=()
inbox="data/inbox/links"
manual=1
for arg in "$@"; do
  case "$arg" in
    --dry-run | --refresh) links_args+=("$arg") ;;
    --links-only) manual=0 ;;
    -h | --help)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg (see --help)" >&2
      exit 2
      ;;
    *) inbox="$arg" ;;
  esac
done

status=0
docker compose run --rm app python -m jobmatch ingest-links "$inbox" \
  ${links_args[@]+"${links_args[@]}"} || status=$?

if [ "$manual" -eq 1 ] && [ -d data/inbox/manual ]; then
  echo
  manual_args=()
  for arg in ${links_args[@]+"${links_args[@]}"}; do
    if [ "$arg" = "--dry-run" ]; then manual_args+=("--dry-run"); fi
  done
  docker compose run --rm app python -m jobmatch ingest-manual data/inbox/manual \
    ${manual_args[@]+"${manual_args[@]}"} || status=$?
fi
exit "$status"
