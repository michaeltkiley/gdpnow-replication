#!/usr/bin/env bash
# Record the day's input values in the vintage archive and push them to the `data` branch (orphan branch, created on first use).
# Usage: scripts/archive_to_branch.sh [worktree dir (default vintage)] [remote (default origin)]. Run from the repository root.
set -euo pipefail
DIR="${1:-vintage}"
REMOTE="${2:-origin}"
HERE="$(cd "$(dirname "$0")" && pwd)"
git config user.name >/dev/null 2>&1 || git config user.name 'github-actions'
git config user.email >/dev/null 2>&1 || git config user.email 'actions@github.com'
if git fetch --depth=1 "$REMOTE" data; then
  git worktree add -B data "$DIR" FETCH_HEAD
else
  echo "no data branch yet: starting the archive"
  git worktree add --orphan -b data "$DIR"
fi
python "$HERE/12_archive.py" --dir "$DIR"
cd "$DIR"
git add -A
git diff --cached --quiet || git commit -q -m "Vintage archive $(date -u +%F)"
if git rev-parse -q --verify HEAD >/dev/null; then
  git push "$REMOTE" HEAD:data
else
  echo "nothing archived yet"
fi
