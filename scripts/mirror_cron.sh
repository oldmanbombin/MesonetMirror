#!/bin/bash
# mirror_cron.sh
#
# Runs the full Mesonet mirror cycle: seed from the existing
# mesonet-data branch (so a single station's transient fetch failure
# doesn't cause a temporary gap), run the fetch script, commit
# whatever changed, force-push as one fresh commit. This replaces
# what the old GitHub Actions workflow's individual YAML steps used
# to do - fetch_mesonet.py itself is UNCHANGED, only the orchestration
# around it moved from GitHub's scheduler onto this VM's own crontab.
#
# Expects to be run from the directory this script lives in (cron
# should invoke it with a full path and this script cd's into its own
# directory itself, so that's handled either way).
#
# One-time setup needed before this works, see README further down:
#   - GITHUB_TOKEN below needs a real GitHub Personal Access Token
#     with "Contents: write" permission on oldmanbombin/MesonetMirror
#   - python3 and git need to be installed on the VM

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." # this script lives in scripts/, but every path below (data/, scripts/fetch_mesonet.py) is written relative to the repo root - go up one level to actually land there

REPO="oldmanbombin/MesonetMirror"
BRANCH="mesonet-data"

# Fill this in once, or set it as an actual environment variable in
# your shell/crontab instead of hardcoding it here - either works,
# hardcoding is simpler to get started with.
GITHUB_TOKEN="${GITHUB_TOKEN:-PUT_YOUR_TOKEN_HERE}"

if [ "$GITHUB_TOKEN" = "PUT_YOUR_TOKEN_HERE" ]; then
    echo "ERROR: GITHUB_TOKEN not set - edit this script or export it before running."
    exit 1
fi

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') - starting mirror cycle"

rm -rf data
mkdir -p data

# Seed from the previous snapshot, if one exists - same reasoning as
# the old GitHub Actions version: a single station's transient fetch
# failure this run shouldn't cause a temporary gap in what's live.
if git clone --depth 1 --branch "$BRANCH" "https://github.com/${REPO}.git" prev 2>/dev/null; then
    cp -r prev/. data/
    rm -rf data/.git prev
    echo "Seeded from existing $BRANCH branch."
else
    echo "No existing $BRANCH branch found (or clone failed) - starting fresh."
fi

python3 scripts/fetch_mesonet.py

cd data
rm -rf .git
git init -q
git checkout -q -b "$BRANCH"
git config user.name "mesonet-mirror-vm"
git config user.email "mesonet-mirror-vm@localhost"
git add -A
git commit -q -m "Mesonet data snapshot $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO}.git"
git push -q --force origin "$BRANCH"
cd ..

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') - done, pushed to $BRANCH"
