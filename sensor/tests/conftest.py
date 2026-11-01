"""Test-only operator identity — set before any neo_sensor module reads its configuration."""

import os

os.environ.setdefault("NEO_TELEGRAM_OPERATOR_ID", "200000001")
os.environ.setdefault("NEO_OPERATOR_EMAILS", "operator@example.com")
os.environ.setdefault("NEO_ASSISTANT_NAME", "Neo")
