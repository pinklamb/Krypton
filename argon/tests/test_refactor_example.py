"""The refactored AI service must behave exactly like the 22k-line original."""

import copy
import importlib.util
from pathlib import Path

import pytest


TESTS = Path(__file__).parent
RESOURCES = [f"item_{index:03}" for index in range(400)]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ORIGINAL = _load("ai_original", TESTS / "performance_fixtures" / "ai_generated_service_22k.py")
REFACTORED = _load(
    "ai_refactored", TESTS / "refactor_examples" / "ai_generated_service_refactored.py"
)


class RecordingLogger:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(("info", message))

    def warning(self, message):
        self.lines.append(("warning", message))


def _store_with(resource, records):
    return {resource: copy.deepcopy(records)}


# (action, store builder, arguments before store/logger). `R` stands for the resource.
SCENARIOS = [
    ("create", lambda r: {}, [["not", "a", "dict"]]),
    ("create", lambda r: {}, [{"quantity": 1}]),
    ("create", lambda r: {}, [{"name": "x", "quantity": -1}]),
    ("get", lambda r: {}, ["id-1"]),
    ("get", lambda r: _store_with(r, {"id-1": {"name": "a"}}), ["id-1"]),
    ("get", lambda r: _store_with(r, {"id-1": "not a dict"}), ["id-1"]),
    ("update", lambda r: {}, ["missing", {"name": "x"}]),
    (
        "update",
        lambda r: _store_with(r, {"id-1": {"name": "a", "quantity": 2}}),
        ["id-1", {"name": "  b ", "_x": 1, "quantity": None, "tags": ["t"]}],
    ),
    ("update", lambda r: _store_with(r, {"id-1": {"name": "a"}}), ["id-1", {"quantity": -3}]),
    ("update", lambda r: _store_with(r, {"id-1": {"quantity": -1}}), ["id-1", {}]),
    ("delete", lambda r: {}, ["id-1"]),
    ("delete", lambda r: _store_with(r, {"id-1": {}, "id-2": {}}), ["id-1"]),
]


def _outcome(module, action, resource, build_store, args):
    store, logger = build_store(resource), RecordingLogger()
    function = getattr(module, f"{action}_{resource}")
    try:
        result = ("returned", function(*copy.deepcopy(args), store, logger))
    except Exception as error:  # compare failures too: type and message
        result = ("raised", type(error).__name__, str(error))
    return result, store, logger.lines


@pytest.mark.parametrize("action, build_store, args", SCENARIOS)
def test_refactor_matches_original_for_every_resource(action, build_store, args):
    for resource in RESOURCES:
        expected = _outcome(ORIGINAL, action, resource, build_store, args)
        actual = _outcome(REFACTORED, action, resource, build_store, args)
        assert actual == expected, (action, resource)


SUCCESSFUL_CREATES = [
    {"name": " Widget ", "_secret": 1, "note": None, "quantity": 0},
    {"name": "y", "status": "draft", "resource_type": "z"},
]


@pytest.mark.parametrize("payload", SUCCESSFUL_CREATES)
def test_create_matches_original_apart_from_the_storage_fix(payload):
    for resource in RESOURCES:
        old_result, _, old_log = _outcome(ORIGINAL, "create", resource, lambda r: {}, [payload])
        new_result, store, new_log = _outcome(REFACTORED, "create", resource, lambda r: {}, [payload])
        returned = dict(new_result[1])
        item_id = returned.pop("id")

        assert ("returned", returned) == old_result
        assert new_log == old_log
        assert store == {resource: {item_id: new_result[1]}}


def test_created_records_round_trip():
    store, logger = {}, RecordingLogger()

    first = REFACTORED.create_item_005({"name": " a ", "quantity": 1}, store, logger)
    second = REFACTORED.create_item_005({"name": "b"}, store, logger)

    assert first["id"] != second["id"]
    assert REFACTORED.get_item_005(first["id"], store, logger) == first
    REFACTORED.update_item_005(first["id"], {"quantity": 4}, store, logger)
    assert REFACTORED.get_item_005(first["id"], store, logger)["quantity"] == 4
    assert REFACTORED.delete_item_005(second["id"], store, logger) is True
    assert list(store["item_005"]) == [first["id"]]


def test_create_uses_supplied_id_and_rejects_duplicates():
    store, logger = {}, RecordingLogger()

    REFACTORED.create_item_001({"name": "a", "id": "sku-1"}, store, logger)

    assert store["item_001"]["sku-1"]["name"] == "a"
    with pytest.raises(ValueError, match="already exists"):
        REFACTORED.create_item_001({"name": "b", "id": "sku-1"}, store, logger)
    assert store["item_001"]["sku-1"]["name"] == "a"


def test_original_create_bug_is_gone():
    # In the original, a created record can never be read back by any id.
    store, logger = {}, RecordingLogger()
    ORIGINAL.create_item_002({"name": "a"}, store, logger)
    assert store["item_002"] == {"name": "a", "status": "active", "resource_type": "item_002"}

    created = REFACTORED.create_item_002({"name": "a"}, store := {}, logger)
    assert REFACTORED.get_item_002(created["id"], store, logger) == created


def test_get_returns_a_copy():
    store = _store_with("item_007", {"id-1": {"name": "a"}})

    record = REFACTORED.get_item_007("id-1", store, RecordingLogger())
    record["name"] = "changed"

    assert store["item_007"]["id-1"]["name"] == "a"


@pytest.mark.parametrize("name", ["create_item_400", "archive_item_001", "get_item_1"])
def test_names_the_original_lacks_still_fail(name):
    assert not hasattr(ORIGINAL, name)
    with pytest.raises(AttributeError):
        getattr(REFACTORED, name)
