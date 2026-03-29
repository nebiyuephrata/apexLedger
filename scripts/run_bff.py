from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "ledger.api.server:app",
        host=os.environ.get("LEDGER_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("LEDGER_API_PORT", "8010")),
        reload=os.environ.get("LEDGER_API_RELOAD", "false").lower() == "true",
    )
