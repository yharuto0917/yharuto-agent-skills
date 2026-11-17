#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(mktemp -d)}"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init -q
git checkout -b main -q 2>/dev/null || git branch -M main
git config user.name "Test Author"
git config user.email "test@example.com"

mkdir -p .github/workflows docs

cat << 'EOF' > CLAUDE.md
# Project Conventions

- 技術ドキュメント・レビューコメント・PR解説では絵文字（🚀✨🎯🧠👀⏭️ 等）を一切使用しないこと。
- 優先度・種別を示す場合はテキスト表記（`[SCOPE]`, `[TARGET]`, `[MUST]`, `[SKIM]`, `[SKIP]`）を用いること。
EOF

cat << 'EOF' > .github/workflows/ci.yml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python3 -m unittest discover -s tests
EOF

cat << 'EOF' > docs/architecture.md
# Architecture Overview

## Components

- API Gateway handles HTTP routing.
- Worker processes background jobs.
EOF

git add .
git commit -q -m "chore: initial CI workflow, architecture docs, and CLAUDE.md conventions"

git checkout -b docs/ci-and-arch-overview -q

cat << 'EOF' > .github/workflows/ci.yml
name: CI

on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python3 -m unittest discover -s tests
EOF

cat << 'EOF' > docs/architecture.md
# Architecture Overview

## Components

- API Gateway handles request validation, rate limits, and HTTP routing.
- Worker processes background jobs from the transactional outbox queue.

## Request and Outbox Flow

1. Client sends an order request to the API Gateway.
2. Service writes the domain record and outbox event in one SQLite transaction.
3. Dispatcher polls due events (`status = 'pending'` and `next_retry_at <= now`).
4. Failed deliveries increment `attempts` and apply exponential backoff up to 300 seconds.
5. Events reaching `max_attempts` move to `outbox_dead_letters` with the last error message.

## CI Matrix and Concurrency Policy

- Pull requests run unit tests across Python 3.11 and 3.12 on `ubuntu-latest`.
- `concurrency.cancel-in-progress: true` cancels superseded commits on the same branch.
- Workflows must not grant write permissions unless explicitly required by release jobs.

## Local Verification

Run `python3 -m unittest discover -s tests` before opening a pull request.
For CI workflow changes, verify YAML syntax with Python or `actionlint` when available.
Do not use emojis in technical docs or PR briefings; follow the `[SCOPE]` / `[MUST]` / `[SKIM]` / `[SKIP]` markers in `CLAUDE.md`.
EOF

git add .
git commit -q -m "docs(ci): document outbox flow and add CI concurrency cancel plus Python 3.12 matrix"

echo "REPO_DIR=$TARGET_DIR"
echo "BRANCH=$(git branch --show-current)"
echo "BASE=main"
echo "NOTE=CLAUDE.md prohibits emojis; use text-only markers"
