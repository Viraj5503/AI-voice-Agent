#!/usr/bin/env bash
# One-shot Entire bootstrap.  No API key required.
#
# Run from the repo root:   bash scripts/setup_entire.sh
#
# This installs the CLI (idempotent), enables Entire on this repo, and
# captures the first dispatch summary.  Re-run after every major commit
# (or wire it into your post-commit hook) so reasoning traces stay current.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v entire >/dev/null 2>&1; then
  echo "→ Installing Entire CLI…"
  curl -fsSL https://entire.io/install.sh | bash
  export PATH="$HOME/.entire/bin:$PATH"
fi

echo "→ entire enable in $(pwd)"
entire enable || {
  echo "If this errored with 'already enabled', that's fine — proceeding."
}

echo "→ entire dispatch (capturing initial reasoning trace)"
entire dispatch || {
  echo "Note: 'entire dispatch' may need an interactive editor session"
  echo "or a fresh commit since the last dispatch.  Try after your next commit."
}

echo
echo "Done.  Look for the generated markdown in your project (typically"
echo "the .entire/ directory or as a commit-message attachment)."
