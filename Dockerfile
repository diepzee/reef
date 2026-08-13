FROM oven/bun:1 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

FROM python:3.13-slim
# postgresql-client-18, not Debian's default. pg_dump refuses to dump from a
# server newer than itself ("aborting because of server version mismatch"),
# and Railway's managed Postgres is 18.4 while trixie ships 17.x -- so the
# stock package makes scripts/backup.py fail every run. Pin this to the major
# version Railway is on, and re-check it if that server is ever upgraded.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# The CLI workspace member must exist in the image: any plain `uv run`
# (service overrides, one-off commands) resolves the whole workspace, and a
# missing member aborts the run before Python even starts.
COPY clients/python ./clients/python
COPY --from=frontend /fe/dist ./frontend/dist
COPY scripts ./scripts
COPY site ./site
COPY piccolo_conf.py ./
# The Phase 1 auth spike ships in the same image so the gate can be tested
# by overriding the start command on a throwaway Railway service, rather
# than maintaining a second Dockerfile that could drift from this one.
COPY spike ./spike
RUN uv sync --frozen --no-dev
ENV PYTHONPATH=/app/src
# migrate.py wraps `piccolo migrations forwards` in an advisory lock, so two
# containers booting at once cannot run the same migration concurrently.
#
# The admin credential is needed for those first seconds only, so the server
# is exec'd through `env -u`: exec replaces PID 1, meaning no live process in
# the container retains RIF_MIGRATION_DATABASE_URL once boot completes -- not
# in os.environ, and not in any /proc/*/environ. Unsetting inside migrate.py
# would be weaker (the parent shell would keep it); this leaves nothing to
# read. The backup cron is a separate Railway service with its own variables,
# and the restore runbook pulls the credential from the Railway control
# plane, so neither is affected. RIF_BACKUP_DATABASE_URL is scrubbed too in
# case it is ever set here; `env -u` on an unset name is a no-op.
CMD ["sh", "-c", "uv run --frozen --no-dev python scripts/migrate.py && exec env -u RIF_MIGRATION_DATABASE_URL -u RIF_BACKUP_DATABASE_URL uv run --frozen --no-dev python -m rif.server"]
