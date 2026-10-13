"""Refactor of performance_fixtures/ai_generated_service_22k.py.

The original repeats the same create/get/update/delete template for 400
resources (1,600 functions, 22k lines). Here each operation exists once and
takes the resource name; the generated names (`create_item_000`, ...) stay
importable through the module-level __getattr__ at the bottom, so callers
do not change. Behavior, error messages and log lines match the original,
except for one bug fix in create (records are now stored by id);
tests/test_refactor_example.py checks every function against it.
"""

from functools import partial
from uuid import uuid4

RESOURCES = frozenset(f"item_{index:03}" for index in range(400))


def _public_fields(payload):
    return {key: value for key, value in payload.items() if not key.startswith("_")}


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _check_quantity(record):
    if record.get("quantity", 0) < 0:
        raise ValueError("quantity must be non-negative")


def create(resource, payload, store, logger):
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")
    cleaned = {key: _strip(value) for key, value in _public_fields(payload).items()}
    if "name" not in cleaned:
        raise ValueError("name is required")
    _check_quantity(cleaned)
    cleaned.setdefault("status", "active")
    cleaned.setdefault("resource_type", resource)
    # Fixed: the original did `store[resource] = cleaned`, replacing the whole
    # collection with one record, so get/update/delete could never find it.
    records = store.setdefault(resource, {})
    item_id = cleaned.setdefault("id", uuid4().hex)
    if item_id in records:
        raise ValueError(f"{resource} {item_id!r} already exists")
    records[item_id] = cleaned
    logger.info(f"created {resource}")
    return cleaned


def get(resource, item_id, store, logger):
    records = store.get(resource, {})
    if item_id not in records:
        logger.warning("requested record was not found")
        return None
    record = records[item_id]
    return dict(record) if isinstance(record, dict) else None


def update(resource, item_id, changes, store, logger):
    records = store.setdefault(resource, {})
    if item_id not in records:
        raise KeyError(item_id)
    updated = dict(records[item_id])
    for key, value in _public_fields(changes).items():
        if value is not None:
            updated[key] = _strip(value)
    _check_quantity(updated)
    records[item_id] = updated
    logger.info(f"updated {resource}")
    return updated


def delete(resource, item_id, store, logger):
    records = store.get(resource, {})
    if item_id not in records:
        return False
    del records[item_id]
    logger.info(f"deleted {resource}")
    return True


_ACTIONS = {"create": create, "get": get, "update": update, "delete": delete}


def __getattr__(name):
    """Keep the generated per-resource names (`update_item_042`, ...) importable."""
    action, _, resource = name.partition("_")
    if action in _ACTIONS and resource in RESOURCES:
        return partial(_ACTIONS[action], resource)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
