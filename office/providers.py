"""Register new AI providers here without changing the office workflow."""
from .interfaces import Planner, Reviewer, Worker
from .planner import CodePlanner
from .worker import CodexWorker
from .validation import validate_run


class CodeReviewer:
    provider = "code"

    def review(self, run_dir, execution, criteria, project_id):
        return validate_run(run_dir, execution, criteria, project_id)


PLANNERS = {"code": CodePlanner}
WORKERS = {"codex": CodexWorker}
REVIEWERS = {"code": CodeReviewer}


def build_providers(config: dict) -> tuple[Planner, Worker, Reviewer]:
    try:
        return (PLANNERS[config["planner_provider"]](),
                WORKERS[config["worker_provider"]](),
                REVIEWERS[config["reviewer_provider"]]())
    except KeyError as exc:
        raise ValueError(f"등록되지 않은 provider: {exc}") from exc
