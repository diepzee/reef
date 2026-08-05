"""Dump the database and upload it to R2. Container disk is ephemeral;
the dump must leave the machine in the same run."""

import os
import subprocess
from datetime import UTC, datetime

import boto3


def main() -> None:
    """Stream pg_dump straight to the backups prefix in R2."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump = subprocess.run(
        ["pg_dump", "--format=custom", os.environ["DATABASE_URL"]],
        check=True, capture_output=True).stdout
    boto3.client(
        "s3", endpoint_url=os.environ["RIF_S3_ENDPOINT"],
        aws_access_key_id=os.environ["RIF_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["RIF_S3_SECRET_KEY"],
    ).put_object(Bucket=os.environ["RIF_S3_BUCKET"],
                 Key=f"backups/rif-{stamp}.dump", Body=dump)
    print(f"uploaded backups/rif-{stamp}.dump ({len(dump)} bytes)")


if __name__ == "__main__":
    main()
