"""Make and restore an encrypted application-level snapshot of admissions."""

import argparse
import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

from bson import BSON
from cryptography.fernet import Fernet, InvalidToken
from pymongo import ReplaceOne

from P05_Migration import (
    EXPECTED_ROWS,
    EXPECTED_SHA256,
    DEFAULT_SOURCE,
    DataContractError,
    mongo_collection,
    preflight,
    verify,
)


PROJECT_DIR = Path(__file__).resolve().parent
KEY_FILE = PROJECT_DIR / "secrets" / "backup_key.txt"
BACKUP_DIR = PROJECT_DIR / "backups"


def make_key(path=KEY_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(Fernet.generate_key() + b"\n")
    return True


def cipher(path=KEY_FILE):
    return Fernet(path.read_bytes().strip())


def pack_documents(documents):
    """Keep raw BSON only in memory before authenticated encryption."""
    chunks = [b"P05B1"]
    metadata = json.dumps({"source_sha256": EXPECTED_SHA256, "rows": len(documents)}).encode("ascii")
    chunks.extend((struct.pack(">I", len(metadata)), metadata))
    for source_row, document in enumerate(documents, start=1):
        if document["source_row"] != source_row or document["source_sha256"] != EXPECTED_SHA256:
            raise DataContractError(f"backup row contract failed at position {source_row}")
        encoded = BSON.encode(document)
        chunks.extend((struct.pack(">I", len(encoded)), encoded))
    if len(documents) != EXPECTED_ROWS:
        raise DataContractError(f"backup has {len(documents)} rows, expected {EXPECTED_ROWS}")
    return b"".join(chunks)


def unpack_documents(payload):
    if not payload.startswith(b"P05B1"):
        raise DataContractError("unrecognized backup format")
    offset = 5

    def take():
        nonlocal offset
        if len(payload) - offset < 4:
            raise DataContractError("truncated backup")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        offset += 4
        if len(payload) - offset < length:
            raise DataContractError("truncated backup item")
        result = payload[offset:offset + length]
        offset += length
        return result

    metadata = json.loads(take())
    if metadata != {"source_sha256": EXPECTED_SHA256, "rows": EXPECTED_ROWS}:
        raise DataContractError("backup metadata differs from the approved source")
    documents = []
    for row in range(1, EXPECTED_ROWS + 1):
        document = BSON(take()).decode()
        if document.get("source_row") != row or document.get("source_sha256") != EXPECTED_SHA256:
            raise DataContractError(f"backup content row contract failed at position {row}")
        documents.append(document)
    if offset != len(payload):
        raise DataContractError("unexpected bytes after backup records")
    return documents


def backup_collection(collection, destination, key_path=KEY_FILE):
    if destination.exists():
        raise FileExistsError("refusing to replace an existing backup")
    verify(collection, DEFAULT_SOURCE, EXPECTED_SHA256)
    documents = list(collection.find({"source_sha256": EXPECTED_SHA256}).sort("source_row", 1))
    encrypted = cipher(key_path).encrypt(pack_documents(documents))
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encrypted)
    return {"rows": len(documents), "backup_sha256": hashlib.sha256(encrypted).hexdigest()}


def restore_collection(collection, source, key_path=KEY_FILE, batch_size=500):
    try:
        payload = cipher(key_path).decrypt(source.read_bytes())
    except InvalidToken as exc:
        raise DataContractError("backup authentication failed; wrong key or altered archive") from exc
    documents = unpack_documents(payload)
    for start in range(0, len(documents), batch_size):
        operations = [
            ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            for document in documents[start:start + batch_size]
        ]
        collection.bulk_write(operations, ordered=True)
    return verify(collection, DEFAULT_SOURCE, EXPECTED_SHA256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keygen", "backup", "restore"), required=True)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    if args.mode == "keygen":
        print(f"backup key ready; newly created: {int(make_key())}")
        return
    preflight(DEFAULT_SOURCE)
    client, collection = mongo_collection()
    try:
        if args.mode == "backup":
            filename = f"p05_admissions_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.fernet"
            destination = args.file or BACKUP_DIR / filename
            result = backup_collection(collection, destination)
            print(f"encrypted backup created: {result['rows']} rows; SHA-256 {result['backup_sha256']}")
        else:
            if args.file is None:
                parser.error("--file is required for restore")
            print(f"restored and verified: {restore_collection(collection, args.file)} rows")
    finally:
        client.close()


if __name__ == "__main__":
    main()
