# Browser Use — Phase A

Browser Use is the **agent capability layer** over the Chrome session that UKLOK Browser already owns.

It does not launch our browser, own our profile, or replace noVNC. It attaches to the existing Chrome DevTools Protocol (CDP) endpoint.

```text
human ──► noVNC ───────────────┐
                               ▼
                         UKLOK Chrome
                               ▲
agent ──► Browser Use CLI ──► CDP
```

## Zero → Hero

### 1. Start UKLOK Browser

```bash
docker compose \
  -f compose.yaml \
  -f local.yaml \
  up -d
```

Use `bind-profile.yaml` too when you want the host-backed Linux browser identity described in [Profiles & identity](profiles.md).

### 2. Install the pinned Browser Use capability

Prerequisite: [uv](https://docs.astral.sh/uv/getting-started/installation/).

On macOS with Homebrew:

```bash
brew install uv
```

Then:

```bash
./bin/browser-use-setup
```

The setup command:

1. creates a repo-local Python 3.12 `.venv` when needed;
2. installs the Browser Use version pinned in `requirements-browser-use.txt` into that shared environment;
3. asks that same pinned environment to materialize its upstream skill at `.agents/skills/browser-use/SKILL.md`;
4. validates tracked automation contracts;
5. leaves `.venv` and the generated upstream skill untracked.

No separate `browser-harness` dependency is installed by UKLOK. The pinned Browser Use package owns the compatible harness dependency.

### 3. Prove the whole path

```bash
./bin/browser-use-doctor
```

Success means all four boundaries passed:

```text
Browser Use CLI
      ↓
upstream Browser Use skill
      ↓
UKLOK Chrome CDP
      ↓
real Browser Use attachment to that existing Chrome
```

The doctor is intentionally non-mutating: it lists attached tabs rather than navigating.

## Use the CLI

`./bin/browser-use` is a thin adapter over `.venv/bin/browser-use`. Humans, agents, sandboxes and deterministic automations therefore share the same pinned package environment. The adapter preserves the upstream CLI and supplies the UKLOK connection default:

```text
BU_CDP_URL=http://127.0.0.1:9222
```

Example:

```bash
./bin/browser-use <<'PY'
print(list_tabs())
PY
```

Navigate when the task actually requires it:

```bash
./bin/browser-use <<'PY'
new_tab("https://example.com")
wait_for_load()
print(page_info())
PY
```

The wrapper also defaults `BH_TAB_MARKER=0` so Browser Use does not rewrite visible page titles with its tab marker.

### Override the CDP endpoint

For a non-default local host port:

```bash
BU_CDP_URL=http://127.0.0.1:18800 ./bin/browser-use
```

For a worker running as a sibling container on the same Compose network:

```bash
BU_CDP_URL=http://browser:9222 ./bin/browser-use
```

An explicit `BU_CDP_URL` is the ownership boundary: Browser Use attaches to the browser UKLOK already provisioned instead of selecting a separate local/cloud browser path.

## Agent skill

After setup, agents operating web pages should load:

```text
.agents/skills/browser-use/SKILL.md
```

That file is generated from the pinned Browser Use package; do not edit it as UKLOK doctrine.

Use the skills by concern:

| Need | Skill |
| --- | --- |
| Navigate, inspect, click, type, use CDP, manage tabs | generated Browser Use skill |
| Diagnose Chrome/Xvfb/noVNC/CDP/profile runtime failures | `browser-runtime` |
| Apply issue/branch/review/evidence rules | organization skills from the UKLOK entrypoint |

If Browser Use interaction gets stuck on a browser mechanic, follow the upstream skill's interaction-skill references before inventing a new UKLOK abstraction.

## MCP adapter

CLI + skill is the default path when the agent can run shell commands.

When the host specifically requires MCP, expose Browser Use's CLI MCP adapter through the same pinned wrapper:

```bash
./bin/browser-use --cli-mcp
```

With an explicit remote/container endpoint:

```bash
BU_CDP_URL=http://browser:9222 ./bin/browser-use --cli-mcp
```

The MCP adapter exposes Browser Use's programmable browser execution and screenshot surfaces while still controlling the same UKLOK Chrome session.

Do not add a second MCP/browser stack merely because MCP is available.

## From exploration to automation

Use direct Browser Use + its skill while discovering or repairing browser behavior. Once behavior is understood and repeated, encode it as a [contract-first browser automation](automations.md).

The normal automation path is deterministic and does not load an agent. A failed checkpoint emits a narrow `failure.json`; that artifact is the repair entrypoint for the `browser-automation` skill.

## Operational model

Treat one persistent UKLOK Chrome identity as **one interactive browser lane**.

Browser Use can keep many tabs in the same browser, but simultaneous agents that switch tabs and act at the same time can race. Serialize interactive operations on one browser identity; scale to separate browser identities only when true concurrency is required.

## Update the Browser Use version

The pin has one owner:

```text
requirements-browser-use.txt
```

Change it deliberately, then rerun:

```bash
./bin/browser-use-setup
./bin/browser-use-doctor
```

The generated skill is replaced from the newly pinned upstream package.

## Troubleshooting

### `uv` missing

Install uv, then rerun `./bin/browser-use-setup`.

### Skill missing

```bash
./bin/browser-use-setup
```

### CDP unavailable

```bash
curl http://127.0.0.1:9222/json/version
docker compose -f compose.yaml -f local.yaml ps
```

If Chrome/CDP itself is unhealthy, switch to [Operations](operations.md) and the `browser-runtime` skill. Do not debug the Browser Use layer before the browser substrate is healthy.

### Browser Use cannot attach but raw CDP works

Run:

```bash
./bin/browser-use --doctor
```

Then inspect the generated Browser Use skill for its current upstream recovery guidance.

## Phase A boundary

Included:

- pinned Browser Use CLI;
- upstream Browser Use skill;
- direct attachment to UKLOK Chrome through CDP;
- CLI and optional MCP adapter;
- repo-local shared `.venv`;
- setup + doctor + agent navigation;
- deterministic contract-first automation knowledge/repair layer.

Not included yet:

- Browser Use Cloud;
- autonomous Browser Use Agent runtime as UKLOK orchestration;
- Browser Use Cloud SDK;
- parallel browser broker;
- UKLOK authenticated domain skills such as Upwork workflows.

Those are later decisions earned by this spike's observed effectiveness.
