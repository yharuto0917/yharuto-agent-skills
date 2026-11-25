#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(mktemp -d)}"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init -q
git checkout -b main -q 2>/dev/null || git branch -M main
git config user.name "Test Author"
git config user.email "test@example.com"

mkdir -p src tests

cat << 'EOF' > src/__init__.py
EOF

cat << 'EOF' > src/date_format.py
from datetime import date
from typing import Optional


def format_iso_date(value: Optional[date], default: str = "") -> str:
    raw = value.isoformat()
    return raw


def format_month_label(value: Optional[date]) -> str:
    return value.strftime("%Y-%m")
EOF

cat << 'EOF' > tests/__init__.py
EOF

cat << 'EOF' > tests/test_date_format.py
import unittest
from datetime import date

from src.date_format import format_iso_date, format_month_label


class TestDateFormat(unittest.TestCase):
    def test_format_iso_date_with_valid_date(self) -> None:
        self.assertEqual(format_iso_date(date(2026, 11, 16)), "2026-11-16")

    def test_format_month_label_with_valid_date(self) -> None:
        self.assertEqual(format_month_label(date(2026, 11, 16)), "2026-11")


if __name__ == "__main__":
    unittest.main()
EOF

git add .
git commit -q -m "chore: initial date formatting utilities"

git checkout -b fix/null-date-format -q

cat << 'EOF' > src/date_format.py
from datetime import date
from typing import Optional


def format_iso_date(value: Optional[date], default: str = "") -> str:
    if value is None:
        return default
    return value.isoformat()


def format_month_label(value: Optional[date]) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m")
EOF

cat << 'EOF' > tests/test_date_format.py
import unittest
from datetime import date

from src.date_format import format_iso_date, format_month_label


class TestDateFormat(unittest.TestCase):
    def test_format_iso_date_with_valid_date(self) -> None:
        self.assertEqual(format_iso_date(date(2026, 11, 16)), "2026-11-16")

    def test_format_iso_date_returns_default_for_none(self) -> None:
        self.assertEqual(format_iso_date(None), "")
        self.assertEqual(format_iso_date(None, default="N/A"), "N/A")

    def test_format_month_label_with_valid_date(self) -> None:
        self.assertEqual(format_month_label(date(2026, 11, 16)), "2026-11")

    def test_format_month_label_returns_empty_for_none(self) -> None:
        self.assertEqual(format_month_label(None), "")


if __name__ == "__main__":
    unittest.main()
EOF

git add .
git commit -q -m "fix(date): None入力時にISO日付と年月ラベルが空文字・既定値を返すよう修正"

echo "REPO_DIR=$TARGET_DIR"
echo "BRANCH=$(git branch --show-current)"
echo "BASE=main"
echo "TEST_CMD=python3 -m unittest discover -s tests"
