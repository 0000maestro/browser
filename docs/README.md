# Documentation

Start with the task you are trying to complete. You should not need to read these pages in order.

| Need | Document | What it answers |
| --- | --- | --- |
| Start | [Quick start](quickstart.md) | Which Compose files do I run? |
| Agent control | [Browser Use — Phase A](browser-use.md) | How does an agent operate the existing Chrome with minimal setup? |
| Automate | [Browser automations](automations.md) | How do learned browser behaviors become deterministic, checkpointed automations? |
| Mental model | [Architecture](architecture.md) | Which process owns which surface? |
| Browser identity | [Profiles & identity](profiles.md) | Where do cookies/logins live and what can be reused? |
| Diagnose | [Operations](operations.md) | What do I inspect when Chrome/noVNC/CDP fails? |
| Boundaries | [Security](security.md) | What must never be exposed or silently weakened? |
| Deliver | [Releases](releases.md) | What is published, when, and how is it versioned? |

## Configuration surfaces

The Compose files are intentionally layered:

```text
compose.yaml         canonical service; Docker-network only
local.yaml           local build + loopback host ports + local sandbox fallback
bind-profile.yaml    host-backed, container-owned Linux profile
```

Combine only the behavior you need.

## Source map

| Concern | Owner |
| --- | --- |
| Image packages/runtime dependencies | `Dockerfile` |
| Process supervision | `supervisord.conf` |
| Startup/runtime scripts | `scripts/` |
| Canonical deployment | `compose.yaml` |
| Local development behavior | `local.yaml` |
| Host-backed profile | `bind-profile.yaml` |
| Runtime acceptance | `tests/smoke.sh` |
| PR validation | `.github/workflows/ci.yml` |
| Package publication | `.github/workflows/publish.yml` |
| Agent entrypoint | `AGENTS.md` |
| Repo-specific runtime skill | `.agents/skills/browser-runtime/SKILL.md` |
| Browser Use dependency pin | `requirements-browser-use.txt` |
| Browser Use adapter/setup/doctor | `bin/browser-use*` |
| Automation contracts/modules/runtime | `automations/` + `bin/browser-automation` |
| Automation repair skill | `.agents/skills/browser-automation/SKILL.md` |
| Generated upstream operation skill | `.agents/skills/browser-use/SKILL.md` (local, gitignored) |
