"""Create local MongoDB passwords without printing or replacing existing secrets."""

import os
import secrets
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent / "secrets"
FILES = (
    "mongo_root_password.txt",
    "mongo_ingest_password.txt",
    "mongo_reader_password.txt",
)


def main():
    DIRECTORY.mkdir(exist_ok=True)
    created = 0
    for filename in FILES:
        path = DIRECTORY / filename
        if path.exists():
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(secrets.token_urlsafe(48) + "\n")
        created += 1
    print(f"Local secret files ready: {len(FILES)}; newly created: {created}")


if __name__ == "__main__":
    main()
