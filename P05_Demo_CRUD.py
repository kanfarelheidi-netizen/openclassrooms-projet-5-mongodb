"""Demonstrate CRUD with synthetic data in a separate temporary collection."""

import json
from uuid import uuid4

from P05_Migration import mongo_collection


def run_crud(collection):
    identifier = "p05_demo_" + uuid4().hex
    collection.insert_one({"_id": identifier, "label": "synthetic_example", "status": "created"})
    try:
        read_ok = collection.find_one({"_id": identifier})["status"] == "created"
        updated = collection.update_one({"_id": identifier}, {"$set": {"status": "updated"}})
        update_ok = updated.modified_count == 1 and collection.find_one({"_id": identifier})["status"] == "updated"
    finally:
        deleted = collection.delete_one({"_id": identifier})
    delete_ok = deleted.deleted_count == 1 and collection.find_one({"_id": identifier}) is None
    result = {"create": True, "read": read_ok, "update": update_ok, "delete": delete_ok}
    if not all(result.values()):
        raise RuntimeError("synthetic CRUD demonstration failed")
    return result


def main():
    client, _ = mongo_collection()
    try:
        from os import environ
        collection = client[environ.get("P05_MONGO_DB", "p05_medical")]["demo_crud"]
        print(json.dumps(run_crud(collection)))
    finally:
        client.close()


if __name__ == "__main__":
    main()
