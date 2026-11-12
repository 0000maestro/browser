# Security boundaries

## Treat both control surfaces as privileged

CDP and noVNC can operate an authenticated browser. Access to either can imply access to cookies, pages, account actions, and session authority.

The base deployment therefore publishes **no host ports**.

## Network rule

Preferred order:

1. Docker sibling network
2. host loopback
3. SSH / WireGuard / Tailscale tunnel

Do not bind CDP or noVNC directly to a public interface.

The host overlay (`host.yaml`) binds both ports to `127.0.0.1` only. The host's tunnel is the only ingress.

## Sandbox rule

Production/base behavior:

```text
CHROME_DISABLE_SANDBOX=0
CHROME_ALLOW_SANDBOX_FALLBACK=0
```

The local overlay can arm a narrow fallback because Docker Desktop under Apple-Silicon amd64 emulation has been observed to reject Chrome's namespace setup.

Local behavior:

1. start Chrome with sandbox;
2. if startup specifically reports `Failed to move to new namespace`;
3. retry with `--no-sandbox`.

Disable even that local fallback with:

```bash
LOCAL_CHROME_ALLOW_SANDBOX_FALLBACK=0 \
docker compose -f compose.yaml -f local.yaml up --build -d
```

Any `--no-sandbox` execution weakens Chrome isolation.

## Host overlay seccomp rule

`host.yaml` sets `seccomp=unconfined` on the browser container. Docker's default seccomp profile blocks the user-namespace calls that Chrome's sandbox needs. The failure shows as `Failed to move to new namespace` and a black noVNC screen. Unconfined seccomp removes that filter for this container only.

This weakens container isolation, not Chrome's. Chrome's sandbox stays enabled, and the host overlay pins `CHROME_ALLOW_SANDBOX_FALLBACK=0`. Do not solve the namespace failure with `--no-sandbox`. Replacing `unconfined` with a narrower custom profile is the next hardening step and is not done yet.

## VNC password

`VNC_PASSWORD` is optional. Legacy VNC password authentication is not the primary security boundary and must not replace network isolation.

## Profile boundary

A profile contains durable browser authority.

- keep `/data/chrome` scoped to the workload that owns it;
- never concurrently mount the same profile into two Chrome instances;
- never assume a macOS/Windows profile is authentication-portable to Linux;
- back up host-bound profiles before experimental migrations.
