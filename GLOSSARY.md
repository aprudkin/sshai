# sshai

sshai retains bounded local evidence of authorized, non-interactive command
execution. This glossary names concepts used to interpret that evidence.

## Language

**Artifact finalization**:
The completion of a local evidence record for an observed command result,
including retained output and metadata, its bounded passport or delta
presentation, and best-effort audit recording. It is not authorization to execute
a command or proof that a remote command completed.
_Avoid_: Execution completion, result publication.

**Shell-state lifecycle**:
The preparation of saved working-directory and selected environment values for
replay, and the retention of eligible observed updates for later invocations.
It is separate from command execution, the inherited process environment, and
artifact finalization.
_Avoid_: Execution lifecycle, environment isolation.
