# PatchPilot 🚀

> **An open-source AI developer agent that turns repository tasks into reviewable code changes.**

**Built by [HarshitEthic](https://github.com/harshitethic)** · Open source · MIT licensed

PatchPilot is a local-first developer tool for giving an AI agent a real Git repository and a concrete engineering task. It analyzes the codebase, creates an implementation plan, generates structured file edits, applies them in an isolated workspace, runs detected tests, and exposes the resulting Git diff for review.

It is designed to sit **between an issue and a pull request** — with the developer kept in control of the final change.

---

## ✨ What PatchPilot does

```text
┌────────────────────┐
│ GitHub repository  │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Task / Issue       │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Analyze repository │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Implementation plan│
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Generate file edits│
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Apply changes      │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Run tests          │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Repair if needed   │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Review Git diff    │
└────────────────────┘
```

### Current capabilities

- 🧠 Repository-aware task planning
- ✍️ Structured code/file editing instead of trusting raw model-generated diffs
- 🧪 Automatic test-command detection and execution
- 🔁 Limited repair loop when tests fail
- 📦 Isolated per-run workspaces
- 🔍 Reviewable Git diffs
- 🏠 Local-first LLM support with Ollama
- 🔌 OpenAI/OpenRouter-compatible provider support
- ⚡ FastAPI backend + lightweight web UI
- 🐳 Docker support for the backend

---

## 🎯 Why PatchPilot?

Most AI coding demos stop at **"generate some code."** PatchPilot is built around the engineering loop that happens after the generation:

**understand → change → test → repair → review**

The goal is not to replace a developer. The goal is to remove repetitive repository work while keeping the generated change visible and reviewable.

---

## 🧑‍💻 Use cases

### 1. Fix a GitHub issue

Give PatchPilot a repository and a bug description:

```text
Fix the authentication bug when an expired session token is submitted.
```

PatchPilot can inspect the project, determine likely files, propose the implementation, apply changes, and run the available tests.

### 2. Add a small feature

```text
Add a /health endpoint that returns the service status as JSON.
```

Useful for small, well-scoped changes where you still want a human-readable diff before merging.

### 3. Refactor existing code

```text
Extract the duplicated validation logic into a reusable helper.
```

PatchPilot can make the change inside a temporary workspace and show the resulting diff.

### 4. Generate tests

```text
Add unit tests for the password validation edge cases.
```

### 5. Explore an unfamiliar repository

Use the planning stage as a fast first pass over an unfamiliar codebase before making the change yourself.

---

## 🏗️ Architecture

```text
                     ┌──────────────────────┐
                     │   PatchPilot Web UI  │
                     └──────────┬───────────┘
                                │ HTTP
                                ↓
                     ┌──────────────────────┐
                     │     FastAPI API      │
                     └──────────┬───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ↓                 ↓                 ↓
       ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
       │ Git / Repo  │   │ LLM Provider│   │ Test Runner │
       │ Workspace   │   │ Ollama/API  │   │ + git diff  │
       └─────────────┘   └─────────────┘   └─────────────┘
```

### Tech stack

- **Backend:** Python, FastAPI
- **Agent model:** Ollama / OpenAI-compatible APIs
- **Default model:** `qwen2.5-coder:14b`
- **Repository operations:** Git
- **Frontend:** HTML, CSS, vanilla JavaScript
- **Deployment:** Docker / Docker Compose

---

## ⚡ Quick start — macOS / Apple Silicon

PatchPilot works especially well as a local tool with Ollama.

### 1. Clone the project

```bash
git clone https://github.com/harshitethic/PatchPilot.git
cd PatchPilot
```

### 2. Start Ollama

Install Ollama from the official installer, then make sure the model is available:

```bash
ollama pull qwen2.5-coder:14b
ollama list
```

You do **not** need to run `ollama serve` if the Ollama application/server is already running.

### 3. Set up the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Verify:

```bash
curl http://127.0.0.1:8000/api/health
```

### 4. Start the frontend

Open another terminal:

```bash
cd frontend
python3 -m http.server 5500
```

Then open:

```text
http://localhost:5500
```

---

## 🧪 Example

Repository:

```text
https://github.com/octocat/Hello-World.git
```

Task:

```text
Add a README section called "PatchPilot Test" explaining that this repository is being used to test an AI coding agent.
```

A successful run should produce:

- an implementation plan
- the likely files to change
- a test result
- the applied file changes
- a final Git diff

---


## 📸 Screenshots

### PatchPilot dashboard

The local-first agent UI: provide a GitHub repository, describe the task, choose the model/provider, and run the agent.

![PatchPilot dashboard](docs/screenshots/01-agent-dashboard.png)

### Agent run result

PatchPilot returns the implementation plan, touched files, validation output, and the resulting Git diff for review.

![PatchPilot run result](docs/screenshots/02-run-result.png)

### FastAPI backend

The backend runs locally and exposes the PatchPilot API on `127.0.0.1:8000`.

![PatchPilot backend](docs/screenshots/03-backend-api.png)

### Local frontend

The frontend can be served locally with a lightweight Python HTTP server.

![PatchPilot frontend](docs/screenshots/04-local-frontend.png)

## 🔐 Security model

PatchPilot is an **MVP and local developer tool**. Do not expose the current backend directly to the public internet.

The agent can clone repositories and execute detected project commands inside a workspace. Before using PatchPilot in an untrusted or multi-user environment, add:

- container or VM isolation
- CPU, memory, disk, and execution-time limits
- network isolation
- authenticated API access
- command and path allowlists
- secret isolation
- explicit human approval before write/push/PR actions

The project roadmap intentionally includes a stronger sandbox for this reason.

---

## 🗺️ Roadmap

### v0.x — Agent core

- [x] Repository cloning
- [x] Task analysis
- [x] Implementation planning
- [x] Structured file edits
- [x] Test execution
- [x] Repair loop
- [x] Reviewable Git diff

### Next

- [ ] GitHub App authentication
- [ ] Import GitHub issues directly
- [ ] Create isolated branches automatically
- [ ] Commit changes from the agent
- [ ] Open pull requests automatically
- [ ] Streaming agent events
- [ ] File-aware tool calling
- [ ] Strong sandboxing with Docker / microVMs
- [ ] Human approval gates
- [ ] Persistent runs and history
- [ ] CLI: `patchpilot run "Fix the auth bug"`
- [ ] Multi-agent code review

---

## 🤝 Contributing

PatchPilot is open source and contributions are welcome.

Good places to start:

- improve repository analysis
- add provider adapters
- improve test detection
- strengthen sandboxing
- build the GitHub App / PR workflow
- add language-specific repository tooling
- improve the frontend and developer experience

See the repository issues and roadmap for areas to work on.

### Development setup

```bash
git checkout -b feature/your-change
# make your changes
# run tests / checks
git diff
git commit -m "feat: your change"
```

Please keep pull requests focused and explain the behavior you changed.

---

## 📄 License

PatchPilot is released under the **MIT License**.

---

## 👤 Built by HarshitEthic

PatchPilot is built and maintained by **HarshitEthic** as an open-source developer tooling project.

GitHub: **https://github.com/harshitethic**

> Build tools developers actually want to use. Ship the source.
