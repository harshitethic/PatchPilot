# Bounded host Git output and process cleanup

PatchPilot's host Git helper runs **only fixed, validated Git subcommands**.
Model-selected test commands still run exclusively in disposable, constrained
Docker containers. Host Git is not an execution sandbox: its cloned files
may be adversarial.

Before this change, Python's subprocess.run collected Git's complete stdout
and stderr into memory. Git diffs, repository status, and errors may be
arbitrarily large. A subprocess timeout terminated its direct Git process
without explicitly supervising same-group descendants.

## Changed guarantees

- All allowed host Git commands use a bounded streaming reader. At most **4
  MiB** of output bytes are retained, with 64 KiB read chunks. A larger output
  is rejected with return code **122** and a generic error, **never** returned
  as a partial successful diff.
- A git diff preview exceeding this limit displays an explicit omission
  notice rather than an empty diff that might suggest there were no changes.
- Timed-out operations return code **124** and a generic explanation, without
  reflecting partial secret-bearing stdout/stderr. Other launcher failures
  return **125** and generic text.
- POSIX Git subprocesses run in a dedicated session. The parent forcibly
  terminates the process group after completion, failure or timeout and reaps
  the direct child. It does not execute Git through a shell.
- Sanitized environment variables, disabled hooks and protocol restrictions
  are preserved. Git's exit status and complete output below the bound
  remain available to existing callers, including staged credential checks.
  If a staged diff cannot be checked safely, the existing commit guard fails
  closed instead of committing.

## Reproduce

    cd backend
    python -m unittest discover -s tests -p test_host_git_output_limits.py -v
    python -m unittest discover -s tests -v

Tests create disposable local Git repositories, generate a diff larger than
the cap, check an ordinary small Git response, verify the oversized-diff
warning, and launch a disposable fake Git command that forks a child whose
delayed marker must not be written after its parent times out.

## Important limitations

The output limit is a **memory-capture bound**, not a hard CPU, input-file,
network, total-disk or Git process-tree memory quota. Git's descendants can
create independent process sessions; children deliberately detaching may
escape a simple process-group kill. Windows host Git now fails closed until
reliable Windows job-object-based descendant cleanup is available. This is
not a multi-tenant security sandbox; trusted local use and the existing Docker
isolation policy remain required.
