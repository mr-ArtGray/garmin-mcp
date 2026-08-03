from __future__ import annotations

from garmin_mcp.trim import DEFAULT_MAX_LIST, condense


def test_sample_series_are_dropped_with_a_count() -> None:
    """The point of trimming: a night of per-minute samples must not reach the model."""
    payload = {
        "dailySleepDTO": {"sleepScore": 82, "deepSleepSeconds": 4200},
        "sleepLevels": [{"startGMT": i} for i in range(480)],
        "sleepMovement": [{"level": 1} for _ in range(900)],
    }
    out = condense(payload)

    assert out["dailySleepDTO"] == {"sleepScore": 82, "deepSleepSeconds": 4200}
    assert "480 samples" in out["sleepLevels"]["_omitted"]
    assert "900 samples" in out["sleepMovement"]["_omitted"]


def test_omission_marker_says_how_to_get_the_data_back() -> None:
    out = condense({"heartRateValues": [[1, 60]] * 100})
    assert "detail='full'" in out["heartRateValues"]["_omitted"]


def test_empty_series_stays_an_empty_list() -> None:
    """An absent metric should read as 'no data', not as an omission."""
    assert condense({"stressValuesArray": []}) == {"stressValuesArray": []}


def test_long_lists_are_truncated_with_a_remainder_marker() -> None:
    out = condense({"activities": [{"id": i} for i in range(25)]})
    activities = out["activities"]

    assert len(activities) == DEFAULT_MAX_LIST + 1
    assert activities[0] == {"id": 0}
    assert activities[DEFAULT_MAX_LIST - 1] == {"id": DEFAULT_MAX_LIST - 1}
    assert "17 more items" in activities[-1]["_omitted"]


def test_short_lists_pass_through_untouched() -> None:
    payload = {"days": [{"steps": 100}, {"steps": 200}]}
    assert condense(payload) == payload


def test_nested_structures_are_reduced_at_every_level() -> None:
    payload = {"a": {"b": {"c": {"heartRateValues": [1] * 50, "keep": "yes"}}}}
    out = condense(payload)
    inner = out["a"]["b"]["c"]

    assert inner["keep"] == "yes"
    assert "50 samples" in inner["heartRateValues"]["_omitted"]


def test_scalars_and_none_survive() -> None:
    payload = {"score": 82, "ok": True, "note": None, "pace": 5.5, "name": "Run"}
    assert condense(payload) == payload


def test_unknown_payload_shapes_are_handled() -> None:
    """The escape-hatch tool can return anything, so trimming must not assume a schema."""
    assert condense([1, 2, 3]) == [1, 2, 3]
    assert condense("plain") == "plain"
    assert condense(None) is None
    assert len(condense(list(range(100)))) == DEFAULT_MAX_LIST + 1


def test_deep_nesting_does_not_recurse_without_bound() -> None:
    payload: dict = {}
    node = payload
    for _ in range(60):
        node["next"] = {}
        node = node["next"]
    condense(payload)  # must return rather than blow the stack


def test_max_list_is_configurable() -> None:
    out = condense({"xs": list(range(10))}, max_list=2)
    assert out["xs"][:2] == [0, 1]
    assert "8 more items" in out["xs"][-1]["_omitted"]
