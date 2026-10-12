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


class BackendUnavailableError(FTAError):
    """The selected execution backend cannot run commands on this machine."""


class UnsafeExecutionConsentError(FTAError):
    """Trusted-local execution was requested without explicit consent."""
