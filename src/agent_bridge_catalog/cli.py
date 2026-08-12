from __future__ import annotations

import argparse

import uvicorn

from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-bridge-catalog")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=58081)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    Settings.from_environment().state_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "agent_bridge_catalog.app:app",
        host=args.bind,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
