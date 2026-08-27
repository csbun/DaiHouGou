# Device Compatibility PoC Runbook

Run this PoC on the target Debian-based server, not on a development laptop. Keep the server
and all Xiaomi devices on the same trusted LAN. Do not expose go2rtc port 1984 or RTSP port 8554
through the router.

## 1. Verify The Host

Require Docker Engine 23 or newer, Docker Compose v2, and at least 10 GB free disk space.

```bash
docker version
docker compose version
df -h .
```

## 2. Create Local Environment Configuration

```bash
cp .env.poc.example .env.poc
chmod 600 .env.poc
```

Fill the Xiaomi and optional Home Assistant secrets in `.env.poc` locally. Never commit this
file or paste its contents into an issue or report.

## 3. Start go2rtc And Configure Both Cameras

```bash
mkdir -p deploy/go2rtc/state
cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
docker compose -f compose.poc.yaml up -d go2rtc
```

From another computer, open an SSH tunnel and browse to `http://127.0.0.1:1984`:

```bash
ssh -L 1984:127.0.0.1:1984 SERVER_USER@SERVER_IP
```

In go2rtc, add the Xiaomi account and configure the two lowest usable streams as `xiaobai` and
`xiaobai_25k`. Try `subtype=sd` first. Confirm both names appear in `/api/streams`.

## 4. Record Device Inventory

```bash
mkdir -p config
cp config/poc-devices.example.json config/poc-devices.json
```

Fill the actual MIoT model, firmware, LAN IP, and active codec for both cameras, plus model and
firmware for the speaker. Do not add credentials, tokens, DIDs, or Xiaomi source URLs.

## 5. Run Unit Tests And Lint

```bash
docker build -f docker/poc.Dockerfile -t daihougou-poc:test .
docker run --rm --entrypoint pytest daihougou-poc:test -q
docker run --rm --entrypoint ruff daihougou-poc:test check src tests
docker compose -f compose.poc.yaml config --quiet
```

## 6. Run Camera 30-Minute And Recovery Tests

Start host sampling in a separate terminal:

```bash
scripts/capture-host-stats.sh artifacts/poc/host-stats-30m.log 30
```

Run each camera separately:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai --duration-seconds 1800
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai_25k --duration-seconds 1800
```

Restart go2rtc and test recovery of the configured primary camera:

```bash
docker compose -f compose.poc.yaml restart go2rtc
docker compose -f compose.poc.yaml --profile tools run --rm probe camera wait --stream xiaobai_25k --max-seconds 60
```

Both decode commands must complete, and recovery must complete within 60 seconds.

## 7. Configure The Temporary Home Assistant Route

```bash
mkdir -p deploy/homeassistant/state
cp deploy/homeassistant/configuration.yaml deploy/homeassistant/state/configuration.yaml
docker compose -f compose.poc.yaml --profile ha up -d homeassistant
```

Complete onboarding at `http://SERVER_IP:8123`, add the Xiaomi integration, and bind the L05C.
Create a dedicated non-administrator user and a Long-Lived Access Token for that user. Find the
working L05C service, entity, text field, and any extra data in Developer Tools, then update only
the matching `HA_*` values in `.env.poc`. Record a failed non-administrator action as a failure;
do not substitute an owner token.

## 8. Run And Annotate Both 30-Trial Speaker Routes

An adult must listen to every trial. API acceptance is not evidence that audio was heard.

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend direct --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id DIRECT_RUN_ID --count 30 --missed '2,7'
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend ha --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id HA_RUN_ID --count 30 --missed ''
```

Replace the example missed lists with the trials that were actually inaudible.

## 9. Stop Failed Speaker Routes

A route passes the first gate only with 30/30 API-accepted trials and at least 29/30 audible
trials. Do not run the extended test for a failed route. If both routes fail, preserve the
artifacts and stop the PoC.

## 10. Run And Annotate 100 Trials For Passing Routes

For each route that passed checkpoint 9:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend BACKEND --count 100 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id RUN_ID --count 100 --missed ''
```

The extended gate requires at least 99/100 API acceptances and 98/100 audible confirmations.

## 11. Run The Eight-Hour Primary-Camera Soak

Start a fresh host sampler in one terminal, then run the primary camera decode in another:

```bash
scripts/capture-host-stats.sh artifacts/poc/host-stats-8h.log 60
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai_25k --duration-seconds 28800
```

Do not declare success if any `MemAvailable` sample is below `768000 kB` or if the log contains
an OOM-killer event. Measure a lower-quality stream first; do not add swap merely to pass.

## 12. Generate And Review The Gate

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe report gate \
  --inventory /workspace/config/poc-devices.json \
  --host-stats /workspace/artifacts/poc/host-stats-8h.log
```

Review `artifacts/poc/gate.md`. Record either `CONTINUE_GO2RTC_HA`,
`CONTINUE_GO2RTC_DIRECT`, `REPLACE_CAMERA`, `FIX_SPEAKER_INTEGRATION`, or `UPGRADE_SERVER`.
Only a `CONTINUE_*` decision authorizes planning the MVP.

## 13. Stop Only PoC Containers

```bash
docker compose -f compose.poc.yaml --profile ha down
```

Preserve `artifacts/poc` for the decision record. Do not use `docker compose down -v`; volume or
state deletion makes diagnosis and reruns harder.

## Artifact Warning

JSONL secrets are redacted by field name, but PoC artifacts can still contain device names,
firmware versions, LAN IP addresses, service errors, and timing information. Treat the artifact
directory as private household data and do not publish it unchanged.
