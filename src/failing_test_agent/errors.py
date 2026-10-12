class FTAError(Exception):
    """Base application error."""


class RepositoryError(FTAError):
    """Repository precondition failed."""


class DirtyRepositoryError(RepositoryError):
    """Repository has uncommitted or untracked changes."""


class WorktreeCreationError(FTAError):
    """Detached worktree could not be created."""


class CommandValidationError(FTAError):
    """The configured test command is outside the V1 contract."""
