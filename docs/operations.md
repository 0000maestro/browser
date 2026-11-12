# Operations & troubleshooting

Diagnose from the inside out. A visible noVNC page is not proof that Chrome is healthy.

## Health sequence

```bash
docker compose -f compose.yaml -f local.yaml ps
curl http://127.0.0.1:9222/json/version
docker compose -f compose.yaml -f local.yaml logs browser
```

Container health checks three distinct claims:

1. Chrome process exists.
2. CDP responds.
3. noVNC responds.

## Symptom: noVNC loads but the screen is black

Interpretation: the web/VNC path may be healthy while Chrome is dead.

Look for:

```text
Failed to move to new namespace
Zygote process exited prematurely
chrome entered FATAL state
```

On the explicit local overlay, the browser wrapper detects that known namespace failure and retries with `--no-sandbox`. Base/production does not.

On a Linux host, the namespace failure comes from Docker's default seccomp profile. Use `host.yaml`, which sets `seccomp=unconfined` and keeps the sandbox on. See [Security](security.md#host-overlay-seccomp-rule).

Also verify:

```bash
curl http://127.0.0.1:9222/json/version
```

If CDP refuses the connection, Chrome is not actually alive.

## Symptom: “profile appears to be in use”

For a managed profile, restart with the current image. Startup clears stale:

```text
SingletonLock
SingletonCookie
SingletonSocket
```

Do not “solve” this by sharing the profile with another running Chrome instance.

## Symptom: tabs restored but sites are logged out

If the profile originated from macOS/Windows and is now running in Linux, this is expected evidence of a cross-platform identity mismatch. See [Profiles & identity](profiles.md).

Create a fresh container-owned Linux profile and authenticate inside it.

## Inspect health output

For a direct test container:

```bash
docker inspect --format '{{.State.Health.Status}}' uklok-browser-smoke

docker inspect \
  --format '{{range .State.Health.Log}}{{.Output}}{{end}}' \
  uklok-browser-smoke
```

## Smoke contract

```bash
./tests/smoke.sh uklok-browser-smoke
```

It verifies:

- CDP reports Google Chrome;
- noVNC serves its client;
- Xvfb is alive;
- Chrome was not launched headless;
- the profile is writable.

CI also restarts the same container/profile and runs the smoke contract again to exercise profile persistence and stale-lock handling.
