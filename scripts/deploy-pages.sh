#!/usr/bin/env bash
#
# deploy-pages.sh — publish the built static site in output/ to the gh-pages branch
# and serve it from GitHub Pages.
#
# This encapsulates the previously hand-done publish steps so a deploy is one command.
# It is idempotent and safe to re-run: every run builds a fresh, self-contained copy of
# output/ in a temp dir and force-pushes it as a single commit to gh-pages, fully
# replacing whatever was there before.
#
# Why the static/ rename: GitHub Pages (Jekyll) ignores any directory whose name starts
# with an underscore, so PreTeXt's `_static/` would 404. We rename it to `static/` and
# rewrite the absolute `/_static/` references in every book HTML page to `/static/`.
#
# Prerequisites:
#   - output/ must already be built (e.g. `make build:all`).
#   - `gh` must be authenticated with the `workflow` scope against a PUBLIC repo
#     (GitHub Pages on the free tier requires a public repository).
#
# Usage: bash scripts/deploy-pages.sh   (or: make deploy)

set -euo pipefail

REPO_SLUG="Cruxsyn/classics-library"
BRANCH="gh-pages"
LIVE_URL="https://cruxsyn.github.io/classics-library/"

# Resolve the repository root from this script's location so the script works
# regardless of the caller's current directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${REPO_ROOT}/output"

# --- preflight ---------------------------------------------------------------
command -v git >/dev/null 2>&1 || { echo "error: git is not installed" >&2; exit 1; }
command -v gh  >/dev/null 2>&1 || { echo "error: gh (GitHub CLI) is not installed" >&2; exit 1; }

if [ ! -d "${OUTPUT_DIR}" ] || [ -z "$(ls -A "${OUTPUT_DIR}" 2>/dev/null)" ]; then
  echo "error: ${OUTPUT_DIR} is missing or empty — build it first (e.g. 'make build:all')." >&2
  exit 1
fi

TOKEN="$(gh auth token 2>/dev/null || true)"
if [ -z "${TOKEN}" ]; then
  echo "error: could not obtain a GitHub token from 'gh auth token'." >&2
  echo "       Run 'gh auth login' and ensure the token has the 'workflow' scope." >&2
  exit 1
fi

# --- staging dir -------------------------------------------------------------
WORKDIR="$(mktemp -d)"
# Always return to the repo root before removing the temp dir, even on error,
# so we never try to operate from a deleted working directory.
cleanup() {
  cd "${REPO_ROOT}" 2>/dev/null || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "==> Staging output/ in ${WORKDIR}"
# Copy the contents of output/ (including dotfiles) into the staging dir.
cp -a "${OUTPUT_DIR}/." "${WORKDIR}/"

cd "${WORKDIR}"

# --- rename _static -> static + rewrite refs ---------------------------------
if [ -d _static ]; then
  echo "==> Renaming _static/ -> static/ (GitHub Pages skips underscore-prefixed dirs)"
  rm -rf static
  mv _static static
fi

echo "==> Rewriting /_static/ -> /static/ in book HTML"
find . -name '*.html' -type f -print0 | xargs -0 -r sed -i 's#/_static/#/static/#g'

# --- scrub junk + disable Jekyll ---------------------------------------------
echo "==> Deleting AppleDouble junk files"
find . -name '._*' -type f -delete

echo "==> Writing .nojekyll"
touch .nojekyll

# --- publish to gh-pages -----------------------------------------------------
echo "==> Initializing ${BRANCH} and committing"
git init -q -b "${BRANCH}"
git config user.email "noreply@anthropic.com"
git config user.name  "deploy"
git add -A
git commit -q -m "Deploy site to GitHub Pages" --allow-empty

echo "==> Force-pushing to ${BRANCH} on ${REPO_SLUG}"
# pack.threads=1 works around a git 2.43 pack-objects bug on large trees.
# The token is interpolated into the push URL only; it is not echoed.
git -c pack.threads=1 push -f \
  "https://x-access-token:${TOKEN}@github.com/${REPO_SLUG}.git" \
  "${BRANCH}"

echo "==> Triggering a GitHub Pages build"
gh api -X POST "repos/${REPO_SLUG}/pages/builds" >/dev/null

# Return to the repo root before the temp dir is removed (also handled by trap).
cd "${REPO_ROOT}"

echo ""
echo "Deployed. Live URL: ${LIVE_URL}"
