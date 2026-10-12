from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .errors import FTAError
from .service import create_run

app = typer.Typer(help="Auditable failing-test agent foundation.")
run_app = typer.Typer(help="Create and inspect runs.")
app.add_typer(run_app, name="run")


def app_home() -> Path:
    configured = os.environ.get("FTA_HOME")
    return Path(configured).expanduser().resolve() if configured else Path(".fta").resolve()


@run_app.command("create")
def run_create(
    repo: Path = typer.Option(..., "--repo", exists=True, file_okay=False),
    test: str = typer.Option(..., "--test"),
) -> None:
    """Create run and attempt 1 without executing tests or invoking a model."""
    try:
        created = create_run(repo, test, app_home())
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


if __name__ == "__main__":
    app()
