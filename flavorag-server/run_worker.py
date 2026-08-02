"""Standalone worker entry point.

Usage:
    uv run python run_worker.py --all
    uv run python run_worker.py --workers ingestion,evaluation,retention

Runs the selected background workers without the FastAPI server, enabling
independent scaling of API and worker processes.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import signal
import sys

from app.config.logging_config import configure_root_logger, get_logger
from app.worker.registry import WORKER_REGISTRY, resolve_workers

_log = get_logger("flavorag.worker")


async def _start_worker(name: str):
    spec = WORKER_REGISTRY[name]
    module = importlib.import_module(spec.module_path)
    cls = getattr(module, spec.class_name)
    instance = cls()
    _log.info("worker_starting", worker=name, cls=spec.class_name)
    await instance.start()
    return instance


async def main() -> None:
    parser = argparse.ArgumentParser(description="flavor-rag standalone worker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all workers")
    group.add_argument(
        "--workers",
        type=str,
        help="Comma-separated worker names",
    )
    args = parser.parse_args()

    configure_root_logger()

    if args.all:
        names = resolve_workers(["--all"])
    else:
        names = resolve_workers([w.strip() for w in args.workers.split(",")])

    _log.info("worker_process_starting", workers=names)

    instances = []
    for name in names:
        try:
            inst = await _start_worker(name)
            instances.append((name, inst))
        except Exception as exc:
            _log.error("worker_start_failed", worker=name, error=str(exc))

    # Block until interrupted
    stop_event = asyncio.Event()

    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    _log.info("worker_process_ready", count=len(instances))
    await stop_event.wait()

    _log.info("worker_process_stopping")
    for name, inst in instances:
        try:
            if hasattr(inst, "stop"):
                await inst.stop()
        except Exception as exc:
            _log.warning("worker_stop_failed", worker=name, error=str(exc))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
