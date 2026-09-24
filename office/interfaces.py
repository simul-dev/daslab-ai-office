"""Provider-neutral contracts. Domain orchestration depends only on these ports."""
from pathlib import Path
from threading import Event
from typing import Protocol


class Planner(Protocol):
    provider: str

    def plan(self, task: dict, context: dict) -> dict: ...


class Worker(Protocol):
    def probe(self) -> dict: ...

    def execute(self, run_dir: Path, prompt: str, timeout_seconds: int,
                cancel_event: Event) -> dict: ...


class Reviewer(Protocol):
    provider: str

    def review(self, run_dir: Path, execution: dict,
               criteria: list[str], project_id: str) -> dict: ...
