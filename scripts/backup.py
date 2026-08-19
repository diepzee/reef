"""Dump the database to R2. Container disk is ephemeral, so the dump must
leave the machine in the same run.

Deliberately does NOT read ``DATABASE_URL``. That is the application's
constrained ``rif_app`` role, and ``pg_dump`` issues ``COPY <table> TO
stdout``, which Postgres refuses outright on an RLS-protected table for any
role subject to the policy::

    pg_dump: error: query failed: ERROR:  query would be affected by
    row-level security policy for table "attachments"

The failure is loud rather than silent -- it does not produce an empty dump --
but pointing this at the app role would mean no backups at all, discovered
whenever someone next looked. Give it the admin credential instead.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime

import boto3

# A dump smaller than this is not plausible even for an empty schema, and
# almost certainly means pg_dump produced nothing useful.
MIN_PLAUSIBLE_BYTES = 2_000


def _database_url() -> str:
    """Return the connection string to dump from.

    :raises SystemExit: when no admin credential is configured
    :returns: a DSN for a role that is not subject to row-level security
    """
    for name in ("REEF_BACKUP_DATABASE_URL", "REEF_MIGRATION_DATABASE_URL"):
        if os.environ.get(name):
            return os.environ[name]
    sys.exit(
        "No backup credential. Set REEF_BACKUP_DATABASE_URL (or reuse "
        "REEF_MIGRATION_DATABASE_URL). DATABASE_URL is deliberately not used: "
        "it is the RLS-constrained app role and pg_dump fails against it."
    )


def main() -> None:
    """Dump the database and upload it, verifying the object landed."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = f"backups/rif-{stamp}.dump"

    dump = subprocess.run(
        ["pg_dump", "--format=custom", _database_url()],
        check=True,
        capture_output=True,
    ).stdout
    if len(dump) < MIN_PLAUSIBLE_BYTES:
        sys.exit(f"refusing to upload: pg_dump produced only {len(dump)} bytes")

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["REEF_S3_ENDPOINT"],
        aws_access_key_id=os.environ["REEF_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["REEF_S3_SECRET_KEY"],
        # R2 signs against the pseudo-region "auto"; left unset, boto3 resolves
        # a different one on a developer machine than in the container.
        region_name="auto",
    )
    bucket = os.environ["REEF_S3_BUCKET"]
    client.put_object(Bucket=bucket, Key=key, Body=dump)

    # Read the object back rather than trusting the write: a backup that was
    # never actually stored is the failure this whole script exists to avoid.
    stored = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if stored != len(dump):
        sys.exit(f"upload mismatch: sent {len(dump)} bytes, stored {stored}")
    print(f"uploaded {key} ({stored} bytes, verified)")


if __name__ == "__main__":
    main()
