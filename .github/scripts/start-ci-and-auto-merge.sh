#!/usr/bin/env bash
# Starts CI on a pull request this workflow opened and enables auto merge.
#
# Usage: start-ci-and-auto-merge.sh <pull request number> <head sha>
#
# GitHub holds `pull_request` runs of pull requests opened with the GITHUB_TOKEN until someone
# approves them, and the pull request shows no checks meanwhile. This approves the CI run of the
# head commit, so the required checks run on the pull request itself and auto merge can follow.
set -euo pipefail

number="$1"
sha="$2"
repo="$GITHUB_REPOSITORY"

for _ in $(seq 1 30); do
  run="$(gh api "repos/$repo/actions/runs?event=pull_request&head_sha=$sha" \
    --jq '[.workflow_runs[] | select(.path == ".github/workflows/ci.yml")][0] | "\(.id) \(.conclusion)"')"
  if [ "$run" != "null null" ] && [ -n "$run" ]; then
    read -r id conclusion <<<"$run"
    if [ "$conclusion" = "action_required" ]; then
      gh api -X POST "repos/$repo/actions/runs/$id/approve" >/dev/null
      echo "approved CI run $id"
    else
      echo "CI run $id needs no approval"
    fi
    gh pr merge "$number" --auto --squash
    exit 0
  fi
  sleep 10
done

echo "no CI run appeared for $sha" >&2
exit 1
