#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONUNBUFFERED=1

echo
echo "Running backend-only rubric demo..."
echo

uv run pytest tests/test_demo_showcase.py::test_demo_week_standard -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_concurrency_pressure -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_temporal_compliance_query -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_upcasting_immutability -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_gas_town_recovery -q -s
