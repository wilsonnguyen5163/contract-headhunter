#!/usr/bin/env bash
# Bring matching up to date after the contractors master workbook changes (new or edited
# profiles): rebuild contractors.csv, re-tag jobs if new skills were learned, re-embed,
# match and export.
#
# Usage:
#   bash scripts/update_contractors.sh                  # default workbook
#   bash scripts/update_contractors.sh path/to/book.xlsx
#   bash scripts/update_contractors.sh --dry-run        # show the steps; change nothing
#
# Steps:
#   1. build-contractors (the previous contractors.csv is backed up; skills are learned
#      from the profiles' core strengths into data/contractors/skills_learned.yaml).
#   2. Only if skills_learned.yaml changed: bash scripts/reset_reimport.sh -y, so jobs get
#      the new skill tags (jobs.csv is moved to data/backups/reset-<time>/, then re-imported
#      from data/inbox/ and the page cache in data/raw/).
#   3. embed-contractors, embed-jobs, match, export-trial-run.
# Do not run it while another import or match is running.
set -euo pipefail
cd "$(dirname "$0")/.."

workbook="data/contractors/contractor-master-list.xlsx"
learned="data/contractors/skills_learned.yaml"
dry_run=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    -h | --help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg (see --help)" >&2
      exit 2
      ;;
    *) workbook="$arg" ;;
  esac
done

if [ ! -f "$workbook" ]; then
  echo "No workbook at $workbook" >&2
  exit 1
fi

jobmatch() {
  docker compose run --rm app python -m jobmatch "$@"
}

if [ "$dry_run" -eq 1 ]; then
  echo "Would run:"
  echo "  1. jobmatch build-contractors $workbook"
  echo "  2. if $learned changes: bash scripts/reset_reimport.sh -y (re-tags jobs)"
  echo "  3. jobmatch embed-contractors, embed-jobs, match, export-trial-run"
  exit 0
fi

fingerprint() {
  if [ -f "$learned" ]; then sha256sum "$learned" | cut -d' ' -f1; else echo none; fi
}

before="$(fingerprint)"
echo "== 1. Rebuilding the contractor list"
jobmatch build-contractors "$workbook"

echo
if [ "$(fingerprint)" != "$before" ]; then
  echo "== 2. New skills learned: re-importing jobs so they get the new tags"
  bash scripts/reset_reimport.sh -y
else
  echo "== 2. No new skills learned: jobs keep their tags"
fi

echo
echo "== 3. Embedding, matching and exporting"
jobmatch embed-contractors
jobmatch embed-jobs
jobmatch match
jobmatch export-trial-run
