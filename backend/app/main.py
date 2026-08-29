from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_NAME = "PatchPilot"
ROOT = Path(__file__).resolve().parents[2]
WORKSPACES = ROOT / ".workspaces"
WORKSPACES.mkdir(exist_ok=True)

app = FastAPI(title=APP_NAME, version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


class IssueRequest(BaseModel):
    repo: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    issue_number: int = Field(..., ge=1)


def run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={**os.environ, "CI": "1"},
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + "\n[command timed out]"
        return 124, out


def safe_repo_name(url: str) -> str:
    clean = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", clean.removesuffix(".git"))[:80] or "repo"


def safe_branch_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._/-]+", "-", name.strip()).strip("/-." )
    value = re.sub(r"/{2,}", "/", value)
    return value[:80] or "patchpilot/task"


def clone_repo(repo_url: str) -> tuple[str, Path]:
    workspace_id = next(tempfile._get_candidate_names())
    workdir = WORKSPACES / workspace_id
    workdir.mkdir(parents=True, exist_ok=False)
    code, out = run(["git", "clone", "--depth", "1", repo_url, str(workdir / "repo")], ROOT, 180)
    if code != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, f"Git clone failed: {out[-2000:]}")
    return workspace_id, workdir / "repo"


def list_files(repo: Path, limit: int = 350) -> list[str]:
    ignored = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", "coverage", ".cache"}
    results: list[str] = []
    for p in repo.rglob("*"):
        if any(part in ignored for part in p.parts):
            continue
        if p.is_file():
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
    for rel in ordered[:100]:
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="ignore")
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
    staged: list[tuple[Path, str]] = []
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
        try:
            current = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False, f"Binary/non-text file is not editable: {rel}"
        if current.count(old) != 1:
            return False, f"Expected exactly one match for old text in {rel}, found {current.count(old)}"
        staged.append((path, current.replace(old, new, 1)))

    for path, content in staged:
        path.write_text(content, encoding="utf-8")
    return True, f"Applied {len(staged)} edit(s)"


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


def workspace_repo(workspace_id: str) -> Path:
    repo = WORKSPACES / workspace_id / "repo"
    if not repo.exists():
        raise HTTPException(404, "Workspace not found")
    return repo


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
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise HTTPException(400, "repo must look like owner/name")
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=github_headers())
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"GitHub issue request failed: {response.text[:1000]}")
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
    url = f"https://api.github.com/repos/{req.repo}/issues/{req.issue_number}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=github_headers())
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"GitHub issue request failed: {response.text[:1000]}")
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
        if not test_command:
            break
        test_code, test_output = run(["bash", "-lc", test_command], repo, 180)
        last_test_code, last_test_output = test_code, test_output
        history.append({"iteration": iteration, "action": "test", "returncode": test_code, "output": test_output[-5000:]})
        if test_code == 0 or iteration >= req.max_iterations:
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
        "tests_passed": bool(test_command) and last_test_code == 0,
        "test_output": last_test_output[-12000:],
        "diff": diff_text,
        "history": history,
        "git_status": status.strip(),
    }


@app.post("/api/execute")
async def execute(req: CommandRequest) -> dict[str, Any]:
    repo = workspace_repo(req.workspace_id)
    command = req.command.strip()
    blocked = ["rm -rf /", "mkfs", ":(){ :|:& };:", "shutdown", "reboot"]
    if any(token in command for token in blocked):
        raise HTTPException(400, "Command blocked by PatchPilot safety guard")
    code, output = run(["bash", "-lc", command], repo, 180)
    return {"returncode": code, "output": output[-12000:]}


@app.post("/api/workspace")
async def workspace(req: WorkspaceRequest) -> dict[str, Any]:
    repo = workspace_repo(req.workspace_id)
    return {"workspace_id": req.workspace_id, "files": list_files(repo), "diff": build_patch(repo)}


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
            "GitHub issue import",
        ],
    }
