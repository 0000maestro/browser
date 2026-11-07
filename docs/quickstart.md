# Quick start

## Local development

```bash
cp .env.example .env

docker compose \
  -f compose.yaml \
  -f local.yaml \
  up --build -d
```

Open:

- noVNC: <http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale>
- CDP metadata: <http://127.0.0.1:9222/json/version>

Check health:

```bash
docker compose -f compose.yaml -f local.yaml ps
curl http://127.0.0.1:9222/json/version
```

## Local development with a host-visible profile

Use an **empty directory owned by this container browser**:

```bash
mkdir -p "$HOME/.uklok-browser/chrome-profile"

docker compose \
  -f compose.yaml \
  -f local.yaml \
  -f bind-profile.yaml \
  up --build -d
```

Log in once through noVNC. Then verify persistence:

```bash
docker compose \
  -f compose.yaml \
  -f local.yaml \
  -f bind-profile.yaml \
  down

docker compose \
  -f compose.yaml \
  -f local.yaml \
  -f bind-profile.yaml \
  up -d
```

Do not add `--volumes` if you want to preserve the named profile volume.

## Canonical container-network deployment

`compose.yaml` publishes no host ports:

```bash
docker compose up -d
```

Sibling containers can use:

```text
http://browser:9222
http://browser:6080
```

Set `BROWSER_IMAGE` to an immutable `sha-*` or full semantic-version tag for deployments that require reproducibility.

## Remote human access

Use the local overlay on the remote host and tunnel the loopback ports:

```bash
ssh -N \
  -L 6080:127.0.0.1:6080 \
  -L 9222:127.0.0.1:9222 \
  user@browser-host
```

Never expose CDP or noVNC directly to the public Internet.

## Next: agent control

With the local browser healthy:

```bash
./bin/browser-use-setup
./bin/browser-use-doctor
```

Then follow [Browser Use — Phase A](browser-use.md). Browser Use attaches to the existing CDP endpoint; the Chrome/profile/noVNC lifecycle stays owned by UKLOK Browser.

To prove the deterministic automation layer too:

```bash
./bin/browser-automation run example_read_page
```

See [Browser automations](automations.md) before creating site-specific workflows.
