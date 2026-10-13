from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.command_policy import parse_safe_command
from app.sandbox import run_test_command

APP_NAME = "PatchPilot"
ROOT = Path(__file__).resolve().parents[2]
WORKSPACES = ROOT / ".workspaces"
WORKSPACES.mkdir(exist_ok=True)

DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:5500", "http://localhost:5500",
    "http://127.0.0.1:5173", "http://localhost:5173",
)


def allowed_browser_origins() -> list[str]:
    """Accept explicitly configured browser origins, never a wildcard."""
    origins = list(DEFAULT_ALLOWED_ORIGINS)
    for value in os.getenv("PATCHPILOT_ALLOWED_ORIGINS", "").split(","):
        origin = value.strip().rstrip("/")
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
            host = parsed.hostname
            if (
                parsed.scheme not in {"http", "https"} or not host
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or origin == "*" or "*" in origin
                or (parsed.scheme == "http" and host not in {"127.0.0.1", "localhost"})
            ):
                raise ValueError("Invalid CORS origin")
        except ValueError as exc:
            raise ValueError(
                "PATCHPILOT_ALLOWED_ORIGINS must contain valid trusted HTTPS origins "
                "or local HTTP origins (localhost/127.0.0.1)"
            ) from exc
        if origin not in origins:
            origins.append(origin)
    return origins


app = FastAPI(title=APP_NAME, version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_browser_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    repo_url: str = Field(..., min_length=8)
    task: str = Field(..., min_length=3)
    provider: str = "ollama"
    model: str = "qwen2.5-coder:14b"
    max_iterations: int = Field(default=3, ge=1, le=5)
    branch_name: str | None = Field(default=None, max_length=80)


class WorkspaceRequest(BaseModel):
    workspace_id: str


class CommandRequest(BaseModel):
    workspace_id: str
    command: str = Field(..., min_length=1, max_length=1000)


class CommitRequest(BaseModel):
    workspace_id: str
    message: str = Field(..., min_length=1, max_length=200)
    author_name: str = Field(default="PatchPilot", min_length=1, max_length=100)
    author_email: str = Field(
        default="patchpilot@localhost",
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+$",
    )


class WorkspaceCleanupRequest(BaseModel):
    max_age_hours: int = Field(default=24, ge=1, le=24 * 30)
    dry_run: bool = True


class IssueRequest(BaseModel):
    repo: str = Field(..., min_length=3, max_length=160)
    issue_number: int = Field(..., ge=1)


ALLOWED_SUBPROCESS_TOOLS = frozenset({
    "git", "pytest", "ruff", "mypy", "pyright",
    "python", "python3", "npm", "pnpm", "yarn", "go", "cargo",
})
SENSITIVE_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "AUTH", "COOKIE")
SENSITIVE_ENV_NAMES = frozenset({
    "SSH_AUTH_SOCK", "GPG_AGENT_INFO", "KUBECONFIG", "AWS_PROFILE",
    "AWS_SHARED_CREDENTIALS_FILE", "GOOGLE_APPLICATION_CREDENTIALS",
})


def subprocess_environment() -> dict[str, str]:
    """Do not forward host API keys or credential helper sockets to repo tests.

    This is defense in depth, not a sandbox: the child still runs as the
    current OS user and may be able to read other accessible files.
    """
    clean = {
        key: value for key, value in os.environ.items()
        if key.upper() not in SENSITIVE_ENV_NAMES
        and not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    clean["CI"] = "1"
    return clean


def run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    if not cmd or cmd[0] not in ALLOWED_SUBPROCESS_TOOLS:
        return 126, "Blocked unapproved executable"
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=subprocess_environment(),
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output + "\n[command timed out]"


def validate_repository_url(value: str) -> str:
    """Limit Git clone to canonical HTTPS GitHub owner/repository URLs."""
    try:
        parts = urlsplit(value.strip())
        if (parts.scheme != "https" or parts.hostname not in {"github.com", "www.github.com"}
                or parts.port is not None or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("Only GitHub HTTPS repository URLs are supported.")
    except ValueError as exc:
        raise ValueError("Only GitHub HTTPS repository URLs are supported.") from exc

    segments = parts.path.strip("/").split("/")
    if len(segments) != 2:
        raise ValueError("Expected https://github.com/owner/repository")
    owner, name = segments
    if name.endswith(".git"):
        name = name[:-4]
    if any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
           for part in (owner, name)):
        raise ValueError("Invalid GitHub repository owner or name")
    return f"https://github.com/{owner}/{name}.git"


def safe_repo_name(url: str) -> str:
    clean = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", clean.removesuffix(".git"))[:80] or "repo"


def safe_branch_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._/-]+", "-", name.strip()).strip("/-." )
    value = re.sub(r"/{2,}", "/", value)
    return value[:80] or "patchpilot/task"


def clone_repo(repo_url: str) -> tuple[str, Path]:
    try:
        safe_url = validate_repository_url(repo_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    workspace_id = next(tempfile._get_candidate_names())
    workdir = WORKSPACES / workspace_id
    workdir.mkdir(parents=True, exist_ok=False)
    code, out = run(["git", "clone", "--depth", "1", "--", safe_url, str(workdir / "repo")], ROOT, 180)
    if code != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, f"Git clone failed: {out[-2000:]}")
    return workspace_id, workdir / "repo"


def list_files(repo: Path, limit: int = 350) -> list[str]:
    ignored = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", "coverage", ".cache"}
    results: list[str] = []
    for p in repo.rglob("*"):
        if any(part in ignored for part in p.relative_to(repo).parts):
            continue
        if p.is_symlink():
            continue
        if p.is_file() and p.resolve().is_relative_to(repo.resolve()):
            results.append(str(p.relative_to(repo)).replace("\\", "/"))
            if len(results) >= limit:
                break
    return sorted(results)


def read_repo_context(repo: Path, files: list[str], limit_chars: int = 65000) -> str:
    preferred_ext = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".json", ".yml", ".yaml", ".toml", ".md", ".sql"}
    preferred_names = {"package.json", "pyproject.toml", "requirements.txt", "README.md", "go.mod", "Cargo.toml"}
    ordered = sorted(files, key=lambda f: (Path(f).name not in preferred_names, Path(f).suffix not in preferred_ext, len(f)))
    chunks: list[str] = []
    total = 0
    root = repo.resolve()
    for rel in ordered[:100]:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            continue
        path = repo / rel
        # Reject symlinks at any level, including symlinked parent directories.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        if any(part.is_symlink() for part in path.parents if part != root and part.is_relative_to(root)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text) > 7000:
            text = text[:7000] + "\n...[truncated]"
        piece = f"\n### FILE: {rel}\n{text}\n"
        if total + len(piece) > limit_chars:
            break
        chunks.append(piece)
        total += len(piece)
    return "".join(chunks)


async def ask_model(provider: str, model: str, system: str, user: str) -> str:
    provider = provider.lower()
    if provider == "ollama":
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        url = f"{base.rstrip('/')}/api/chat"
        payload = {"model": model, "stream": False, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"Content-Type": "application/json"}
    else:
        if provider == "openrouter":
            base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            api_key = os.getenv("OPENROUTER_API_KEY", "")
        else:
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise HTTPException(400, "Provider API key is not configured")
        url = f"{base.rstrip('/')}/chat/completions"
        payload = {"model": model, "temperature": 0.1, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = os.getenv("APP_URL", "http://localhost:5173")

    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise HTTPException(502, f"Model request failed: {response.text[:1500]}")
        data = response.json()
    return data.get("message", {}).get("content") or data.get("choices", [{}])[0].get("message", {}).get("content", "")


PLANNER = """You are PatchPilot, an open-source AI software engineer.
Inspect the supplied repository context and task. Do not invent files, APIs, or behavior.
Return ONLY valid JSON with keys: summary, plan, touched_files, test_command.
plan is an array of concrete implementation steps. touched_files contains only repository-relative paths.
test_command should be the smallest realistic test command, or null.
"""

CODER = """You are PatchPilot, a careful AI software engineer.
Return ONLY valid JSON. Do not use markdown fences.
Schema:
{"edits":[{"path":"repo-relative/path","old":"exact existing text","new":"replacement text"}]}
Rules:
- Only change files needed for the task.
- `path` must be repository-relative.
- `old` must be copied exactly from the supplied repository context.
- Each old string must be an exact, unique substring of the current file.
- Keep edits small; do not rewrite whole files unless necessary.
- Never include secrets, credentials, binaries, or destructive commands.
- If no change is needed, return {"edits":[]}.
"""

REPAIRER = """You are PatchPilot repairing a failed code change.
Return ONLY valid JSON. Do not use markdown fences.
Schema:
{"edits":[{"path":"repo-relative/path","old":"exact existing text","new":"replacement text"}]}
Rules:
- `old` must match the CURRENT repository contents exactly.
- Make the smallest edits required to fix the task/test failure.
- Paths are repository-relative.
- Never include markdown or explanation.
"""


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start:end+1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def apply_edits(repo: Path, payload: dict[str, Any]) -> tuple[bool, str]:
    edits = payload.get("edits")
    if not isinstance(edits, list):
        return False, "Model response missing edits array"
    # Validate every edit before writing any file. Sequential changes to one
    # file use the previously staged contents, not the original on-disk text.
    staged: dict[Path, str] = {}
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return False, f"Edit {i+1} is not an object"
        rel = edit.get("path")
        old = edit.get("old")
        new = edit.get("new")
        if not isinstance(rel, str) or not rel.strip():
            return False, f"Edit {i+1} has invalid path"
        path = (repo / rel).resolve()
        try:
            path.relative_to(repo.resolve())
        except ValueError:
            return False, f"Edit {i+1} escapes repository: {rel}"
        if path.name == ".patchpilot.patch":
            return False, "Agent cannot modify PatchPilot control files"
        if not isinstance(old, str) or not isinstance(new, str):
            return False, f"Edit {i+1} requires string old/new values"
        if not path.exists() or not path.is_file():
            return False, f"File not found: {rel}"
        if path in staged:
            current = staged[path]
        else:
            try:
                current = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return False, f"Binary/non-text file is not editable: {rel}"
        if current.count(old) != 1:
            return False, f"Expected exactly one match for old text in {rel}, found {current.count(old)}"
        staged[path] = current.replace(old, new, 1)

    for path, content in staged.items():
        path.write_text(content, encoding="utf-8")
    return True, f"Applied {len(edits)} edit(s)"


def build_patch(repo: Path) -> str:
    code, output = run(["git", "diff", "--no-ext-diff", "--binary"], repo, 60)
    return output if code == 0 else ""


def extract_model_edits(raw: str) -> dict[str, Any]:
    payload = parse_json_object(raw)
    if "edits" not in payload:
        return {}
    return payload


def model_error_preview(raw: str) -> str:
    return (raw or "").strip()[-3000:]


WORKSPACE_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]{6,64}")


def workspace_dir(workspace_id: str) -> Path:
    """Resolve only a direct, non-symlink workspace directory."""
    if not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise HTTPException(400, "Invalid workspace id")
    root = WORKSPACES.resolve()
    workdir = root / workspace_id
    if workdir.is_symlink():
        raise HTTPException(400, "Workspace symlinks are not allowed")
    if not workdir.is_dir() or workdir.resolve() != workdir:
        raise HTTPException(404, "Workspace not found")
    repo = workdir / "repo"
    if repo.is_symlink() or not repo.is_dir() or not repo.resolve().is_relative_to(workdir):
        raise HTTPException(404, "Workspace repository not found")
    return workdir


def workspace_repo(workspace_id: str) -> Path:
    return workspace_dir(workspace_id) / "repo"


def _valid_workspaces() -> list[tuple[str, Path]]:
    root = WORKSPACES.resolve()
    if not root.exists():
        return []
    workdirs: list[tuple[str, Path]] = []
    for entry in root.iterdir():
        if entry.is_symlink() or not WORKSPACE_ID_PATTERN.fullmatch(entry.name):
            continue
        try:
            workdirs.append((entry.name, workspace_dir(entry.name)))
        except (HTTPException, OSError):
            continue
    return workdirs


def _workspace_git_state(repo: Path) -> tuple[bool, str, int]:
    # Ignored files can contain user secrets or generated artifacts. Treat them
    # as protected data, so bulk cleanup never silently deletes them.
    status_code, status = run(
        ["git", "status", "--porcelain", "--ignored=matching", "--untracked-files=all"],
        repo,
        30,
    )
    if status_code != 0:
        # Never delete a repository when Git could not determine its state.
        raise HTTPException(409, "Cannot verify workspace changes")
    branch_code, branch = run(["git", "branch", "--show-current"], repo, 30)
    return bool(status.strip()), branch.strip() if branch_code == 0 else "", len(
        [line for line in status.splitlines() if line.strip()]
    )


def list_workspace_summaries(limit: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wid, workdir in _valid_workspaces():
        repo = workdir / "repo"
        try:
            dirty, branch, count = _workspace_git_state(repo)
            rows.append({
                "workspace_id": wid,
                "branch": branch,
                "has_changes": dirty,
                "changed_files": count,
                "updated_at": datetime.fromtimestamp(
                    workdir.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        except (HTTPException, OSError):
            continue
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows[:limit]


def delete_workspace(workspace_id: str, *, force: bool = False) -> None:
    workdir = workspace_dir(workspace_id)
    dirty, _, _ = _workspace_git_state(workdir / "repo")
    if dirty and not force:
        raise HTTPException(409, "Workspace has uncommitted changes; supply force=true to delete")
    # The directory is a direct non-symlink child of the trusted workspace root.
    shutil.rmtree(workdir)


def cleanup_stale_workspaces(
    max_age_hours: int, *, dry_run: bool = True, now: datetime | None = None
) -> dict[str, list[str]]:
    if not 1 <= max_age_hours <= 24 * 30:
        raise ValueError("max_age_hours must be between 1 and 720")
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - max_age_hours * 3600
    result: dict[str, list[str]] = {"candidates": [], "deleted": [], "skipped_dirty": []}
    for wid, workdir in _valid_workspaces():
        try:
            if workdir.stat().st_mtime >= cutoff:
                continue
            dirty, _, _ = _workspace_git_state(workdir / "repo")
            if dirty:
                result["skipped_dirty"].append(wid)
                continue
            result["candidates"].append(wid)
            if not dry_run:
                delete_workspace(wid)
                result["deleted"].append(wid)
        except (HTTPException, OSError):
            # An invalid git state or disappeared directory must not be deleted.
            continue
    for names in result.values():
        names.sort()
    return result


def commit_workspace(
    repo: Path,
    message: str,
    author_name: str,
    author_email: str,
) -> dict[str, str]:
    message = message.strip()
    author_name = author_name.strip()
    author_email = author_email.strip()
    if not message:
        raise HTTPException(400, "Commit message cannot be blank")
    if not author_name:
        raise HTTPException(400, "Commit author name cannot be blank")

    code, status = run(["git", "status", "--porcelain"], repo, 30)
    if code != 0:
        raise HTTPException(500, f"Could not inspect workspace status: {status[-1500:]}")
    if not status.strip():
        raise HTTPException(400, "No workspace changes to commit")

    code, output = run(["git", "add", "--all"], repo, 60)
    if code != 0:
        raise HTTPException(500, f"Could not stage workspace changes: {output[-1500:]}")

    staged_code, staged_output = run(
        ["git", "diff", "--cached", "--quiet"],
        repo,
        30,
    )
    if staged_code == 0:
        raise HTTPException(400, "No workspace changes to commit")
    if staged_code != 1:
        raise HTTPException(
            500,
            f"Could not inspect staged changes: {staged_output[-1500:]}",
        )

    commit_code, commit_output = run(
        [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        ],
        repo,
        120,
    )
    if commit_code != 0:
        raise HTTPException(500, f"Git commit failed: {commit_output[-2000:]}")

    sha_code, commit_sha = run(["git", "rev-parse", "HEAD"], repo, 30)
    branch_code, branch = run(["git", "branch", "--show-current"], repo, 30)
    if sha_code != 0 or branch_code != 0:
        raise HTTPException(500, "Commit succeeded but Git metadata could not be read")

    return {
        "commit_sha": commit_sha.strip(),
        "branch": branch.strip(),
        "message": message,
        "author_name": author_name,
        "author_email": author_email,
    }


def github_issue_url(repo: str, issue_number: int) -> httpx.URL:
    """Build API URLs with a fixed scheme/host and strictly bounded path data."""
    if not isinstance(repo, str) or len(repo) > 160 or repo.count("/") != 1:
        raise HTTPException(400, "repo must look like owner/name")
    owner, name = repo.split("/", 1)
    for part in (owner, name):
        if (
            not 1 <= len(part) <= 80
            or part in {".", ".."}
            or not all(char.isascii() and (char.isalnum() or char in "._-") for char in part)
        ):
            raise HTTPException(400, "Invalid GitHub repository name")
    if not isinstance(issue_number, int) or not 1 <= issue_number <= 2**31 - 1:
        raise HTTPException(400, "Invalid issue number")
    return httpx.URL(
        scheme="https",
        host="api.github.com",
        path=f"/repos/{owner}/{name}/issues/{issue_number}",
    )


def github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(400, "GITHUB_TOKEN is not configured")
    return {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": APP_NAME, "version": "0.4.0"}


@app.get("/api/github/issue")
async def get_issue(repo: str, issue_number: int) -> dict[str, Any]:
    url = github_issue_url(repo, issue_number)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=github_headers())
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "GitHub issue request failed")
    issue = response.json()
    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "body": issue.get("body") or "",
        "state": issue.get("state"),
        "html_url": issue.get("html_url"),
        "labels": [label.get("name") for label in issue.get("labels", [])],
        "user": (issue.get("user") or {}).get("login"),
    }


@app.post("/api/import-issue")
async def import_issue(req: IssueRequest) -> dict[str, Any]:
    url = github_issue_url(req.repo, req.issue_number)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=github_headers())
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "GitHub issue request failed")
    issue = response.json()
    title = issue.get("title", "Untitled issue")
    body = issue.get("body") or "No issue description provided."
    task = f"GitHub Issue #{req.issue_number}: {title}\n\n{body}".strip()
    return {"repo": req.repo, "issue_number": req.issue_number, "title": title, "task": task, "url": issue.get("html_url")}


@app.post("/api/run")
async def run_agent(req: RunRequest) -> dict[str, Any]:
    workspace_id, repo = clone_repo(req.repo_url)
    files = list_files(repo)
    context = read_repo_context(repo, files)
    _, base_branch = run(["git", "branch", "--show-current"], repo)
    base_branch = base_branch.strip() or "main"

    requested_branch = safe_branch_name(req.branch_name or f"patchpilot/{safe_repo_name(req.repo_url)}-{next(tempfile._get_candidate_names())[:8]}")
    code, branch_output = run(["git", "switch", "-c", requested_branch], repo, 30)
    if code != 0:
        raise HTTPException(400, f"Could not create isolated branch: {branch_output[-1500:]}")

    plan_raw = await ask_model(req.provider, req.model, PLANNER, f"Repository: {req.repo_url}\nTask: {req.task}\n\nFiles:\n{chr(10).join(files)}\n\nContext:\n{context}")
    plan = parse_json_object(plan_raw)
    test_command = plan.get("test_command")
    test_argv: list[str] | None = None
    if test_command is not None:
        if not isinstance(test_command, str):
            raise HTTPException(502, "Planner returned a non-string test command")
        try:
            test_argv = parse_safe_command(test_command)
        except ValueError as exc:
            raise HTTPException(502, f"Planner returned an unsafe test command: {exc}") from exc

    coder_input = f"TASK:\n{req.task}\n\nIMPLEMENTATION PLAN:\n{json.dumps(plan, indent=2)}\n\nCURRENT REPOSITORY CONTEXT:\n{context}"
    raw_edits = await ask_model(req.provider, req.model, CODER, coder_input)
    edits = extract_model_edits(raw_edits)
    applied, edit_output = apply_edits(repo, edits)
    if not applied:
        raise HTTPException(502, f"Agent produced invalid edits: {edit_output}. Model response: {model_error_preview(raw_edits)}")

    history = [{"iteration": 1, "action": "apply", "result": "success", "details": edit_output}]
    last_test_output = ""
    last_test_code = 0

    for iteration in range(1, req.max_iterations + 1):
        if not test_argv:
            break
        test_code, test_output = run_test_command(test_argv, repo, 180)
        last_test_code, last_test_output = test_code, test_output
        history.append({"iteration": iteration, "action": "test", "returncode": test_code, "output": test_output[-5000:]})
        if test_code == 0 or test_code in {124, 125, 126} or iteration >= req.max_iterations:
            break

        current_context = read_repo_context(repo, list_files(repo))
        current_diff = build_patch(repo)
        repair_input = f"TASK:\n{req.task}\n\nCURRENT DIFF:\n{current_diff}\n\nTEST OUTPUT:\n{test_output[-12000:]}\n\nCURRENT REPOSITORY CONTEXT:\n{current_context}"
        repair_raw = await ask_model(req.provider, req.model, REPAIRER, repair_input)
        repair_edits = extract_model_edits(repair_raw)
        ok, repair_output = apply_edits(repo, repair_edits)
        history.append({"iteration": iteration, "action": "repair", "result": "success" if ok else "failed", "output": repair_output[-3000:]})
        if not ok:
            break

    diff_text = build_patch(repo)
    _, status = run(["git", "status", "--short", "--branch"], repo, 30)
    return {
        "workspace_id": workspace_id,
        "repo_name": safe_repo_name(req.repo_url),
        "base_branch": base_branch,
        "branch": requested_branch,
        "summary": plan.get("summary", ""),
        "plan": plan.get("plan", []),
        "touched_files": plan.get("touched_files", []),
        "test_command": test_command,
        "tests_passed": bool(test_argv) and last_test_code == 0,
        "test_output": last_test_output[-12000:],
        "test_execution_backend": "docker" if test_argv else None,
        "diff": diff_text,
        "history": history,
        "git_status": status.strip(),
    }


@app.post("/api/execute")
async def execute(req: CommandRequest) -> dict[str, Any]:
    repo = workspace_repo(req.workspace_id)
    try:
        argv = parse_safe_command(req.command)
    except ValueError as exc:
        raise HTTPException(400, f"Command blocked by PatchPilot safety policy: {exc}") from exc
    code, output = run_test_command(argv, repo, 180)
    return {"returncode": code, "output": output[-12000:], "execution_backend": "docker"}


@app.post("/api/workspace")
async def workspace(req: WorkspaceRequest) -> dict[str, Any]:
    repo = workspace_repo(req.workspace_id)
    return {"workspace_id": req.workspace_id, "files": list_files(repo), "diff": build_patch(repo)}


@app.post("/api/commit")
async def commit(req: CommitRequest) -> dict[str, str]:
    repo = workspace_repo(req.workspace_id)
    return commit_workspace(
        repo,
        req.message,
        req.author_name,
        req.author_email,
    )


@app.get("/api/workspaces")
async def workspaces() -> dict[str, Any]:
    rows = list_workspace_summaries()
    return {"workspaces": rows, "count": len(rows)}


@app.delete("/api/workspaces/{workspace_id}")
async def remove_workspace(workspace_id: str, force: bool = False) -> dict[str, Any]:
    delete_workspace(workspace_id, force=force)
    return {"workspace_id": workspace_id, "deleted": True}


@app.post("/api/workspaces/cleanup")
async def cleanup_workspaces(req: WorkspaceCleanupRequest) -> dict[str, Any]:
    outcome = cleanup_stale_workspaces(req.max_age_hours, dry_run=req.dry_run)
    return {**outcome, "dry_run": req.dry_run, "max_age_hours": req.max_age_hours}


@app.get("/api/openapi-summary")
async def openapi_summary() -> dict[str, Any]:
    return {
        "service": APP_NAME,
        "version": "0.4.0",
        "features": [
            "repository cloning",
            "task planning",
            "structured edits",
            "test execution",
            "repair loop",
            "isolated branches",
            "workspace commits",
            "workspace inventory and safe cleanup",
            "GitHub issue import",
        ],
    }
