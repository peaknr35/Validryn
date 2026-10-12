from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .backends import select_backend
from .capture import capture_baseline
from .errors import FTAError
from .retrieval import retrieve_evidence
from .service import create_run
from .stages import reconcile_lost_attempts

app = typer.Typer(help="Auditable failing-test agent foundation.")
run_app = typer.Typer(help="Create and inspect runs.")
attempt_app = typer.Typer(help="Work on one attempt of a run.")
app.add_typer(run_app, name="run")
app.add_typer(attempt_app, name="attempt")


def app_home() -> Path:
    configured = os.environ.get("FTA_HOME")
    return Path(configured).expanduser().resolve() if configured else Path(".fta").resolve()


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


@app.callback()
def recover_lost_attempts() -> None:
    """Before any command, close out attempts whose stage process was killed."""
    for attempt_id in reconcile_lost_attempts(app_home()):
        typer.echo(
            f"Recovered {attempt_id}: its process was lost mid-stage; marked interrupted.",
            err=True,
        )


@run_app.command("create")
def run_create(
    repo: Path = typer.Option(..., "--repo", exists=True, file_okay=False),
    test: str = typer.Option(..., "--test"),
    regression: str | None = typer.Option(
        None, "--regression", help="Pytest command run before and after patching."
    ),
) -> None:
    """Create run and attempt 1 without executing tests or invoking a model."""
    try:
        created = create_run(repo, test, app_home(), regression_command=regression)
    except FTAError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        json.dumps(
            {
                "run_id": created.run_id,
                "attempt_id": created.attempt_id,
                "base_commit": created.base_commit,
                "worktree_path": str(created.worktree_path),
                "manifest_path": str(created.manifest_path),
            },
            indent=2,
        )
    )


@attempt_app.command("capture")
def attempt_capture(
    run_id: str,
    attempt_id: str,
    backend: str = typer.Option("docker", "--backend", help="docker or trusted-local."),
    allow_unsafe_local_execution: bool = typer.Option(
        False,
        "--allow-unsafe-local-execution",
        help="Required for trusted-local: repository code runs unsandboxed as your user.",
    ),
    timeout: float = typer.Option(300, "--timeout", help="Seconds allowed per command."),
) -> None:
    """Run the target and regression commands on the unpatched base commit."""
    try:
        selected = select_backend(
            backend, allow_unsafe_local_execution=allow_unsafe_local_execution
        )
        capture = capture_baseline(app_home(), run_id, attempt_id, selected, timeout)
    except FTAError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    isolation = "sandboxed" if capture.sandboxed else "NOT SANDBOXED"
    regression = {
        "not_configured": "NOT CONFIGURED",
        "inconclusive": f"INCONCLUSIVE ({capture.regression.status})" if capture.regression else "",
        "captured": (
            f"{len(capture.regression.failing_node_ids)} failing before any patch"
            if capture.regression
            else ""
        ),
    }[capture.regression_status]
    typer.echo(f"Backend:                    {capture.backend} ({isolation})")
    typer.echo(f"Target command status:      {capture.target.status}")
    typer.echo(f"Target test executed:       {_yes_no(capture.target.intended_tests_executed)}")
    typer.echo(f"Target fails on baseline:   {_yes_no(capture.target_failure_confirmed)}")
    typer.echo(f"Regression baseline:        {regression}")
    typer.echo(f"Worktree unchanged:         {_yes_no(capture.worktree_unchanged)}")
    typer.echo(f"Attempt state:              {capture.outcome}")
    if capture.failure_reason:
        typer.echo(f"Reason:                     {capture.failure_reason}")
        raise typer.Exit(code=1)


@attempt_app.command("retrieve")
def attempt_retrieve(run_id: str, attempt_id: str) -> None:
    """Build the evidence bundle for a captured attempt. Runs no tests and no model."""
    try:
        bundle = retrieve_evidence(app_home(), run_id, attempt_id)
    except FTAError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Retrieval strategy:          {bundle.retrieval_strategy}")
    typer.echo(f"Target node:                 {', '.join(bundle.target_node_ids)}")
    typer.echo(f"Repository files inspected:  {len(bundle.files_inspected)}")
    typer.echo(f"Evidence items captured:     {len(bundle.evidence)}")
    typer.echo("Evidence bundle:             artifacts/evidence.json")
    typer.echo(f"Limits reached:              {_yes_no(bundle.truncated)}")
    typer.echo("Worktree unchanged:          YES")
    typer.echo("Attempt state:               retrieved")
    for warning in bundle.warnings:
        typer.echo(f"Warning:                     {warning}")


if __name__ == "__main__":
    app()
