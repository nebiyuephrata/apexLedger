"""
scripts/run_pipeline.py — Process one application through selected agents.
Usage:
  python scripts/run_pipeline.py --application APEX-0007 --phase credit
  python scripts/run_pipeline.py --application APEX-0007 --phase all
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from ledger import upcasters
from ledger.agents.runtime import (
    build_llm_client,
    build_registry_client,
    run_compliance_agent,
    run_credit_analysis_agent,
    run_fraud_detection_agent,
)
from ledger.event_store import EventStore


def _default_model() -> str:
    if os.environ.get("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    if os.environ.get("LLM_PROVIDER", "").lower() == "gemini":
        return "gemini-1.5-pro"
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", required=True)
    parser.add_argument("--phase", default="all", choices=["all", "document", "credit", "fraud", "compliance", "decision"])
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", "postgresql://localhost/apex_ledger"))
    parser.add_argument("--model", default=_default_model())
    args = parser.parse_args()

    if args.phase == "document":
        raise SystemExit("Document phase is held until the external extraction API is wired in.")
    if args.phase == "decision":
        raise SystemExit("Decision orchestrator phase is not wired into the runner yet.")

    store = EventStore(args.db_url, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    await store.connect()
    registry_pool, registry = await build_registry_client(args.db_url)
    client = build_llm_client()

    try:
        phase_map = {
            "credit": [("credit", run_credit_analysis_agent)],
            "fraud": [("fraud", run_fraud_detection_agent)],
            "compliance": [("compliance", run_compliance_agent)],
            "all": [
                ("credit", run_credit_analysis_agent),
                ("fraud", run_fraud_detection_agent),
                ("compliance", run_compliance_agent),
            ],
        }

        for label, runner in phase_map[args.phase]:
            result = await runner(
                store=store,
                registry=registry,
                application_id=args.application,
                model=args.model,
                client=client,
            )
            print(
                f"[{label}] application={args.application} "
                f"session_id={result['session_id']} session_stream={result['session_stream']}"
            )
    finally:
        await registry_pool.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
