"""A scan run inside a child process, importable by name so multiprocessing's `spawn` can find it."""

from __future__ import annotations


def scan_in_child(queue, filename: str, source: bytes) -> None:
    """Import the engine in this (fresh) process and put the result's JSON on the queue."""
    from krypton.engine.run import run_local

    queue.put(run_local({filename: source}).model_dump_json())


def scan_one(item: tuple[str, bytes]) -> str:
    """The same scan as a pool task: (filename, source) in, result JSON out."""
    from krypton.engine.run import run_local

    return run_local({item[0]: item[1]}).model_dump_json()
