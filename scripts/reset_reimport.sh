#!/usr/bin/env bash
# Rebuild jobs.csv from scratch, e.g. after changing normalization rules or the jobs schema.
#
# Usage:
#   bash scripts/reset_reimport.sh               # asks before resetting
#   bash scripts/reset_reimport.sh -y            # no question
#   bash scripts/reset_reimport.sh --dry-run     # show what would happen; change nothing
#   bash scripts/reset_reimport.sh --refetch     # also drop the page cache, so every link is fetched again
#
# What it does:
#   1. Moves jobs.csv, runs.csv and their schema files (plus data/raw/ with --refetch) into
#      data/backups/reset-<UTC time>/. Nothing is deleted; remove old reset folders yourself.
#   2. Removes leftover lock files.
#   3. Re-imports: links from data/inbox/links/ (pages already fetched come from data/raw/,
#      no network) and paste files from data/inbox/manual/.
# Never touches data/inbox/, data/samples/, contractors.csv or contractor_facets.csv.
# Do not run it while another import is running.
set -euo pipefail
cd "$(dirname "$0")/.."

yes=0
dry_run=0
refetch=0
for arg in "$@"; do
  case "$arg" in
    -y | --yes) yes=1 ;;
    --dry-run) dry_run=1 ;;
    --refetch) refetch=1 ;;
    -h | --help)
      sed -n '2,17p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (see --help)" >&2
      exit 2
      ;;
  esac
done

generated=(data/jobs.csv data/jobs.schema.json data/runs.csv data/runs.schema.json)
if [ "$refetch" -eq 1 ]; then
  generated+=(data/raw)
fi
present=()
for path in "${generated[@]}"; do
  if [ -e "$path" ]; then
    present+=("$path")
  fi
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="data/backups/reset-$stamp"

if [ "$dry_run" -eq 1 ]; then
  if [ "${#present[@]}" -gt 0 ]; then
    echo "Would move to $dest:"
    printf '  %s\n' "${present[@]}"
  else
    echo "Nothing to reset."
  fi
  echo "Then would run: bash scripts/fetch_and_import.sh"
  exit 0
fi

if [ "${#present[@]}" -gt 0 ]; then
  echo "Moving to $dest:"
  printf '  %s\n' "${present[@]}"
  if [ "$yes" -eq 0 ]; then
    read -r -p "Continue? [y/N] " answer
    case "$answer" in
      y | Y | yes | YES) ;;
      *)
        echo "Cancelled; nothing changed."
        exit 1
        ;;
    esac
  fi
  mkdir -p "$dest"
  mv "${present[@]}" "$dest"/
else
  echo "Nothing to reset."
fi

rm -f data/jobs.csv.lock data/runs.csv.lock data/.run.lock

echo
bash scripts/fetch_and_import.sh
