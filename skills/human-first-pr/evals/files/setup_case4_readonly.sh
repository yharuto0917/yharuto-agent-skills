#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(mktemp -d)}"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init -q
git checkout -b main -q 2>/dev/null || git branch -M main
git config user.name "Test Author"
git config user.email "test@example.com"

mkdir -p src/session src/middleware tests

cat << 'EOF' > src/__init__.py
EOF

cat << 'EOF' > src/session/__init__.py
EOF

cat << 'EOF' > src/session/ttl_policy.py
IDLE_TTL_SECONDS = 1800


def compute_next_expiry(now_ts: int) -> int:
    return now_ts + IDLE_TTL_SECONDS
EOF

cat << 'EOF' > src/session/store.py
import os
from typing import Dict


class SessionStore:
    def __init__(self) -> None:
        self._dsn = os.environ.get("SESSION_DB_DSN", "")
        self._records: Dict[str, int] = {}

    def get_expiry(self, session_id: str) -> int:
        return self._records.get(session_id, 0)
EOF

cat << 'EOF' > src/middleware/__init__.py
EOF

cat << 'EOF' > src/middleware/session_refresh.py
from src.session.store import SessionStore
from src.session.ttl_policy import compute_next_expiry


def refresh_session(store: SessionStore, session_id: str, now_ts: int) -> int:
    if store.get_expiry(session_id) <= now_ts:
        return 401
    compute_next_expiry(now_ts)
    return 200
EOF

cat << 'EOF' > tests/__init__.py
EOF

cat << 'EOF' > tests/test_session_ttl.py
import os
import unittest

from src.session.ttl_policy import compute_next_expiry


class TestSessionTTL(unittest.TestCase):
    def setUp(self) -> None:
        if not os.environ.get("SESSION_DB_DSN"):
            raise RuntimeError("SESSION_DB_DSN is required for session integration tests")

    def test_compute_next_expiry(self) -> None:
        self.assertEqual(compute_next_expiry(1000), 2800)


if __name__ == "__main__":
    unittest.main()
EOF

git add .
git commit -q -m "chore: initial session TTL handling"

git checkout -b feat/session-ttl -q

cat << 'EOF' > src/session/ttl_policy.py
IDLE_TTL_SECONDS = 1800
MAX_SESSION_TTL_SECONDS = 86400


def compute_sliding_expiry(created_at: int, now_ts: int) -> int:
    hard_cap = created_at + MAX_SESSION_TTL_SECONDS
    sliding = now_ts + IDLE_TTL_SECONDS
    return min(hard_cap, sliding)


def is_session_expired(created_at: int, idle_expires_at: int, now_ts: int) -> bool:
    if now_ts >= created_at + MAX_SESSION_TTL_SECONDS:
        return True
    return now_ts >= idle_expires_at
EOF

cat << 'EOF' > src/session/store.py
import os
from dataclasses import dataclass
from typing import Dict, Optional

from src.session.ttl_policy import compute_sliding_expiry, is_session_expired


@dataclass
class SessionRecord:
    session_id: str
    created_at: int
    expires_at: int


class SessionStore:
    def __init__(self, dsn: Optional[str] = None) -> None:
        resolved = dsn if dsn is not None else os.environ.get("SESSION_DB_DSN", "")
        if not resolved:
            raise RuntimeError("SESSION_DB_DSN must be configured")
        self._dsn = resolved
        self._records: Dict[str, SessionRecord] = {}

    def save(self, session_id: str, created_at: int, now_ts: int) -> SessionRecord:
        record = SessionRecord(
            session_id=session_id,
            created_at=created_at,
            expires_at=compute_sliding_expiry(created_at, now_ts),
        )
        self._records[session_id] = record
        return record

    def touch(self, session_id: str, now_ts: int) -> Optional[SessionRecord]:
        record = self._records.get(session_id)
        if record is None or is_session_expired(record.created_at, record.expires_at, now_ts):
            self._records.pop(session_id, None)
            return None
        record.expires_at = compute_sliding_expiry(record.created_at, now_ts)
        return record
EOF

cat << 'EOF' > src/middleware/session_refresh.py
from typing import Dict

from src.session.store import SessionStore


def refresh_session(store: SessionStore, session_id: str, now_ts: int) -> Dict[str, object]:
    updated = store.touch(session_id, now_ts)
    if updated is None:
        return {"status": 401, "error": "session_expired"}
    return {"status": 200, "expires_at": updated.expires_at}
EOF

cat << 'EOF' > tests/test_session_ttl.py
import os
import unittest

from src.middleware.session_refresh import refresh_session
from src.session.store import SessionStore


class TestSessionTTL(unittest.TestCase):
    def setUp(self) -> None:
        if not os.environ.get("SESSION_DB_DSN"):
            raise RuntimeError("SESSION_DB_DSN is required for session integration tests")
        self.store = SessionStore()

    def test_sliding_ttl_stops_at_hard_cap(self) -> None:
        self.store.save("sess-1", created_at=0, now_ts=0)
        res = refresh_session(self.store, "sess-1", now_ts=86000)
        self.assertEqual(res, {"status": 200, "expires_at": 86400})

    def test_expired_session_returns_401(self) -> None:
        self.store.save("sess-2", created_at=0, now_ts=0)
        res = refresh_session(self.store, "sess-2", now_ts=86401)
        self.assertEqual(res, {"status": 401, "error": "session_expired"})


if __name__ == "__main__":
    unittest.main()
EOF

git add .
git commit -q -m "feat(session): enforce 24h max session lifetime alongside idle sliding TTL"

echo "REPO_DIR=$TARGET_DIR"
echo "BRANCH=$(git branch --show-current)"
echo "BASE=main"
echo "TEST_CMD=python3 -m unittest discover -s tests (expected to fail without SESSION_DB_DSN)"
