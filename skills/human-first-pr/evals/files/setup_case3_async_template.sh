#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(mktemp -d)}"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init -q
git checkout -b main -q 2>/dev/null || git branch -M main
git config user.name "Test Author"
git config user.email "test@example.com"

mkdir -p .github api db/migrations domain routes tests value_objects workers

cat << 'EOF' > .github/pull_request_template.md
## 概要
<!-- 変更の目的と概要を記載してください -->

## 関連 Issue
Closes #

## 変更分類・破壊的変更
- [ ] 破壊的変更あり（後方互換性なし）
- [ ] DB マイグレーションあり（デプロイ前後に手順確認が必要）

## 確認チェックリスト
- [ ] ユニットテストを実行し通過を確認した
- [ ] マイグレーションのロールバック手順を確認した
EOF

cat << 'EOF' > db/migrations/001_init_orders.sql
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    total_yen INTEGER NOT NULL,
    status TEXT NOT NULL
);
EOF

cat << 'EOF' > value_objects/__init__.py
EOF

cat << 'EOF' > domain/__init__.py
EOF

cat << 'EOF' > workers/__init__.py
EOF

cat << 'EOF' > api/__init__.py
EOF

cat << 'EOF' > routes/__init__.py
EOF

cat << 'EOF' > tests/__init__.py
EOF

cat << 'EOF' > api/orders_handler.py
import sqlite3
from typing import Dict


def create_order_sync(conn: sqlite3.Connection, order_id: str, total_yen: int) -> Dict[str, object]:
    conn.execute(
        "INSERT INTO orders (id, total_yen, status) VALUES (?, ?, ?)",
        (order_id, total_yen, "created"),
    )
    conn.commit()
    return {"id": order_id, "total_yen": total_yen, "status": "created"}
EOF

git add .
git commit -q -m "chore: initial order database and API baseline"

git checkout -b feat/async-outbox -q

cat << 'EOF' > db/migrations/002_create_outbox_events.sql
CREATE TABLE IF NOT EXISTS outbox_events (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_retry_at INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending_schedule
    ON outbox_events (status, next_retry_at, created_at);

CREATE TABLE IF NOT EXISTS outbox_dead_letters (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    failed_at INTEGER NOT NULL
);
EOF

cat << 'EOF' > value_objects/outbox.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    FAILED = "failed"


@dataclass(frozen=True)
class OutboxRecord:
    id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload_json: str
    status: OutboxStatus
    attempts: int
    max_attempts: int
    next_retry_at: int
    last_error: Optional[str]
    created_at: int
    updated_at: int
EOF

cat << 'EOF' > value_objects/order_events.py
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderCreatedEvent:
    order_id: str
    total_yen: int
    currency: str = "JPY"

    def to_payload_json(self) -> str:
        return json.dumps(
            {
                "order_id": self.order_id,
                "total_yen": self.total_yen,
                "currency": self.currency,
            },
            sort_keys=True,
        )
EOF

cat << 'EOF' > domain/outbox_repository.py
import sqlite3
from typing import List

from value_objects.outbox import OutboxRecord, OutboxStatus


class OutboxRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def enqueue(self, record: OutboxRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload_json,
                status, attempts, max_attempts, next_retry_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.aggregate_type,
                record.aggregate_id,
                record.event_type,
                record.payload_json,
                record.status.value,
                record.attempts,
                record.max_attempts,
                record.next_retry_at,
                record.last_error,
                record.created_at,
                record.updated_at,
            ),
        )

    def fetch_due_events(self, now_ts: int, limit: int = 20) -> List[OutboxRecord]:
        rows = self._conn.execute(
            """
            SELECT id, aggregate_type, aggregate_id, event_type, payload_json,
                   status, attempts, max_attempts, next_retry_at, last_error, created_at, updated_at
            FROM outbox_events
            WHERE status = ? AND next_retry_at <= ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (OutboxStatus.PENDING.value, now_ts, limit),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def mark_dispatched(self, event_id: str, now_ts: int) -> None:
        self._conn.execute(
            "UPDATE outbox_events SET status = ?, updated_at = ? WHERE id = ?",
            (OutboxStatus.DISPATCHED.value, now_ts, event_id),
        )

    def schedule_retry(self, event_id: str, attempts: int, next_retry_at: int, error: str, now_ts: int) -> None:
        self._conn.execute(
            """
            UPDATE outbox_events
            SET status = ?, attempts = ?, next_retry_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (OutboxStatus.PENDING.value, attempts, next_retry_at, error, now_ts, event_id),
        )

    def move_to_dead_letter(self, record: OutboxRecord, reason: str, now_ts: int) -> None:
        self._conn.execute(
            "UPDATE outbox_events SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (OutboxStatus.FAILED.value, reason, now_ts, record.id),
        )
        self._conn.execute(
            """
            INSERT INTO outbox_dead_letters (event_id, event_type, payload_json, failure_reason, failed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record.id, record.event_type, record.payload_json, reason, now_ts),
        )

    @staticmethod
    def _row_to_record(row: tuple) -> OutboxRecord:
        return OutboxRecord(
            id=row[0],
            aggregate_type=row[1],
            aggregate_id=row[2],
            event_type=row[3],
            payload_json=row[4],
            status=OutboxStatus(row[5]),
            attempts=row[6],
            max_attempts=row[7],
            next_retry_at=row[8],
            last_error=row[9],
            created_at=row[10],
            updated_at=row[11],
        )
EOF

cat << 'EOF' > domain/order_publisher.py
import sqlite3
from typing import Dict

from domain.outbox_repository import OutboxRepository
from value_objects.order_events import OrderCreatedEvent
from value_objects.outbox import OutboxRecord, OutboxStatus


def create_order_with_outbox(
    conn: sqlite3.Connection,
    order_id: str,
    total_yen: int,
    now_ts: int,
) -> Dict[str, object]:
    repo = OutboxRepository(conn)
    event = OrderCreatedEvent(order_id=order_id, total_yen=total_yen)
    record = OutboxRecord(
        id=f"evt-{order_id}",
        aggregate_type="order",
        aggregate_id=order_id,
        event_type="order.created",
        payload_json=event.to_payload_json(),
        status=OutboxStatus.PENDING,
        attempts=0,
        max_attempts=5,
        next_retry_at=now_ts,
        last_error=None,
        created_at=now_ts,
        updated_at=now_ts,
    )
    with conn:
        conn.execute(
            "INSERT INTO orders (id, total_yen, status) VALUES (?, ?, ?)",
            (order_id, total_yen, "pending_async"),
        )
        repo.enqueue(record)
    return {"id": order_id, "total_yen": total_yen, "status": "pending_async"}
EOF

cat << 'EOF' > workers/backoff.py
def compute_next_retry_ts(now_ts: int, attempts: int, base_delay_sec: int = 5, max_delay_sec: int = 300) -> int:
    if attempts <= 0:
        return now_ts + base_delay_sec
    delay = min(max_delay_sec, base_delay_sec * (2 ** (attempts - 1)))
    return now_ts + delay
EOF

cat << 'EOF' > workers/outbox_dispatcher.py
import sqlite3
from typing import Awaitable, Callable, Dict

from domain.outbox_repository import OutboxRepository
from value_objects.outbox import OutboxRecord
from workers.backoff import compute_next_retry_ts

TransportFn = Callable[[OutboxRecord], Awaitable[None]]


async def dispatch_due_batch(
    conn: sqlite3.Connection,
    transport: TransportFn,
    now_ts: int,
    limit: int = 20,
) -> Dict[str, int]:
    repo = OutboxRepository(conn)
    records = repo.fetch_due_events(now_ts=now_ts, limit=limit)
    stats = {"dispatched": 0, "retried": 0, "dead_lettered": 0}
    for record in records:
        try:
            await transport(record)
        except Exception as exc:
            next_attempts = record.attempts + 1
            with conn:
                if next_attempts >= record.max_attempts:
                    repo.move_to_dead_letter(record, str(exc), now_ts)
                    stats["dead_lettered"] += 1
                else:
                    retry_at = compute_next_retry_ts(now_ts, next_attempts)
                    repo.schedule_retry(record.id, next_attempts, retry_at, str(exc), now_ts)
                    stats["retried"] += 1
            continue
        with conn:
            repo.mark_dispatched(record.id, now_ts)
        stats["dispatched"] += 1
    return stats
EOF

cat << 'EOF' > api/orders_handler.py
import sqlite3
import time
from typing import Dict, Optional

from domain.order_publisher import create_order_with_outbox


def handle_create_order(
    conn: sqlite3.Connection,
    order_id: str,
    total_yen: int,
    now_ts: Optional[int] = None,
) -> Dict[str, object]:
    if total_yen <= 0:
        raise ValueError("total_yen must be positive")
    timestamp = int(time.time()) if now_ts is None else now_ts
    return create_order_with_outbox(conn=conn, order_id=order_id, total_yen=total_yen, now_ts=timestamp)
EOF

cat << 'EOF' > routes/orders_routes.py
import sqlite3
from typing import Any, Callable, Dict

from api.orders_handler import handle_create_order


def register_order_routes(conn: sqlite3.Connection) -> Dict[str, Callable[..., Dict[str, Any]]]:
    def post_orders(payload: Dict[str, Any]) -> Dict[str, Any]:
        return handle_create_order(
            conn=conn,
            order_id=str(payload["order_id"]),
            total_yen=int(payload["total_yen"]),
        )

    return {"POST /orders": post_orders}
EOF

cat << 'EOF' > tests/test_outbox_repository.py
import sqlite3
import unittest
from pathlib import Path

from domain.outbox_repository import OutboxRepository
from value_objects.outbox import OutboxRecord, OutboxStatus


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for sql_file in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    return conn


class TestOutboxRepository(unittest.TestCase):
    def test_enqueue_and_fetch_due(self) -> None:
        conn = make_db()
        repo = OutboxRepository(conn)
        rec = OutboxRecord(
            id="evt-1",
            aggregate_type="order",
            aggregate_id="ord-1",
            event_type="order.created",
            payload_json='{"order_id":"ord-1"}',
            status=OutboxStatus.PENDING,
            attempts=0,
            max_attempts=3,
            next_retry_at=100,
            last_error=None,
            created_at=100,
            updated_at=100,
        )
        repo.enqueue(rec)
        self.assertEqual(len(repo.fetch_due_events(now_ts=100)), 1)
        self.assertEqual(len(repo.fetch_due_events(now_ts=99)), 0)
EOF

cat << 'EOF' > tests/test_outbox_dispatcher.py
import asyncio
import unittest

from domain.order_publisher import create_order_with_outbox
from tests.test_outbox_repository import make_db
from value_objects.outbox import OutboxRecord
from workers.outbox_dispatcher import dispatch_due_batch


class TestOutboxDispatcher(unittest.TestCase):
    def test_retry_then_dead_letter(self) -> None:
        conn = make_db()
        create_order_with_outbox(conn, order_id="ord-99", total_yen=1200, now_ts=100)

        async def failing_transport(_: OutboxRecord) -> None:
            raise RuntimeError("webhook timeout")

        first = asyncio.run(dispatch_due_batch(conn, failing_transport, now_ts=100))
        self.assertEqual(first, {"dispatched": 0, "retried": 1, "dead_lettered": 0})
        conn.execute("UPDATE outbox_events SET attempts = 4, next_retry_at = 100 WHERE id = 'evt-ord-99'")
        second = asyncio.run(dispatch_due_batch(conn, failing_transport, now_ts=200))
        self.assertEqual(second, {"dispatched": 0, "retried": 0, "dead_lettered": 1})
EOF

cat << 'EOF' > tests/test_orders_handler.py
import unittest

from routes.orders_routes import register_order_routes
from tests.test_outbox_repository import make_db


class TestOrdersHandler(unittest.TestCase):
    def test_post_orders_creates_order_and_outbox_atomically(self) -> None:
        conn = make_db()
        routes = register_order_routes(conn)
        result = routes["POST /orders"]({"order_id": "ord-55", "total_yen": 2500})
        self.assertEqual(result["status"], "pending_async")
        row = conn.execute("SELECT status FROM outbox_events WHERE aggregate_id = 'ord-55'").fetchone()
        self.assertEqual(row, ("pending",))
EOF

git add .
git commit -q -m "feat(outbox): introduce transactional outbox schema and async retry dispatcher"

echo "REPO_DIR=$TARGET_DIR"
echo "BRANCH=$(git branch --show-current)"
echo "BASE=main"
echo "TEST_CMD=python3 -m unittest discover -s tests"
