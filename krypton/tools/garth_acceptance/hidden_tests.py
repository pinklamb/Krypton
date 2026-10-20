"""Hidden acceptance tests for the six garth change tickets (select with -k t1_ ... t6_). The agents never see this file."""

import json
from datetime import date, datetime, timezone
from typing import ClassVar

from pydantic.dataclasses import dataclass
from requests import Response, Session

from garth.data.body_battery.events import BodyBatteryData
from garth.http import Client
from garth.stats._base import Stats
from garth.stats.training_status.daily import DailyTrainingStatus
from garth.telemetry import REDACTED, sanitize, sanitize_headers
from garth.utils import camel_to_snake_dict, remove_dto_suffix, remove_dto_suffix_from_dict


# ------------------------------------------------------------------ task 1: sanitizer
def test_t1_query_params():
    assert sanitize("https://x/y?client_secret=abc&q=1") == "https://x/y?client_secret=[REDACTED]&q=1"
    assert sanitize("a=1&access_token=zzz") == "a=1&access_token=[REDACTED]"
    assert sanitize("password=hunter2") == "password=[REDACTED]"


def test_t1_headers():
    got = sanitize_headers({"X-Api-Key": "k", "Accept": "a", "Proxy-Authorization": "p", "Authorization": "t"})
    assert got == {"X-Api-Key": REDACTED, "Accept": "a", "Proxy-Authorization": REDACTED, "Authorization": REDACTED}


def test_t1_json_nested_dicts_and_lists():
    out = json.loads(sanitize('{"a": {"password": "x", "keep": 1}, "b": [{"client_secret": "s"}], "top": 2}'))
    assert out == {"a": {"password": REDACTED, "keep": 1}, "b": [{"client_secret": REDACTED}], "top": 2}


def test_t1_json_top_level_list_and_new_fields():
    assert json.loads(sanitize('[{"id_token": "t", "n": 2}]')) == [{"id_token": REDACTED, "n": 2}]
    assert json.loads(sanitize('{"client_secret": "s", "id_token": "t", "x": 1}')) == {"client_secret": REDACTED, "id_token": REDACTED, "x": 1}


def test_t1_plain_text_and_existing_fields_unchanged():
    assert sanitize("hello world") == "hello world"
    assert json.loads(sanitize('{"access_token": "a", "jti": "j", "ok": true}')) == {"access_token": REDACTED, "jti": REDACTED, "ok": True}
    assert sanitize("[1, 2, 3]") == "[1, 2, 3]"


# ------------------------------------------------------------------ task 2: body battery events
class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def connectapi(self, path, **kwargs):
        return self.payload


def event(**overrides):
    base = {"eventType": "sleep", "eventStartTimeGmt": "2024-01-01T10:00:00Z", "timezoneOffset": 0, "durationInMilliseconds": 3600000,
            "bodyBatteryImpact": 5, "feedbackType": "good", "shortFeedback": "nice"}
    base.update(overrides)
    return {"event": base}


def parse(*items):
    return BodyBatteryData.get("2024-01-01", client=FakeClient(list(items)))


def test_t2_end_time_is_start_plus_duration():
    ev = parse(event())[0].event
    assert ev.event_end_time_gmt == datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)
    assert ev.event_start_time_gmt == datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)


def test_t2_negative_duration_is_clamped_to_zero():
    ev = parse(event(durationInMilliseconds=-5))[0].event
    assert ev.duration_in_milliseconds == 0 and ev.event_end_time_gmt == ev.event_start_time_gmt


def test_t2_existing_fields_still_parsed():
    ev = parse(event(bodyBatteryImpact=-3, timezoneOffset=3600000))[0].event
    assert (ev.event_type, ev.body_battery_impact, ev.timezone_offset, ev.feedback_type, ev.short_feedback) == ("sleep", -3, 3600000, "good", "nice")


def test_t2_bad_items_skipped_and_missing_event_kept():
    bad = {"event": {"eventType": "sleep"}}  # no start time
    no_event = {"activityName": "walk"}
    got = parse(bad, event(), no_event)
    assert len(got) == 2 and got[0].event is not None and got[1].event is None and got[1].activity_name == "walk"


def test_t2_non_list_response_returns_empty():
    assert BodyBatteryData.get("2024-01-01", client=FakeClient({"oops": 1})) == []


# ------------------------------------------------------------------ task 3: http client
def make_client():
    client = Client(session=Session())
    calls = []

    def fake(method, url, headers=None, timeout=None, **kwargs):
        calls.append(dict(headers or {}))
        response = Response()
        response.status_code = 200
        response._content = b"{}"
        response.url = url
        return response

    client.sess.request = fake
    return client, calls


def test_t3_request_headers_do_not_leak_between_calls():
    client, calls = make_client()
    client.request("GET", "connect", "/a")
    client.request("GET", "connect", "/b", referrer=True)
    client.request("GET", "connect", "/c")
    assert "referer" in calls[1] and "referer" not in calls[2]


def test_t3_default_headers_applied_and_overridable():
    client, calls = make_client()
    client.configure(default_headers={"X-Test": "1", "X-Other": "a"})
    client.request("GET", "connect", "/a")
    client.request("GET", "connect", "/b", headers={"X-Other": "b"})
    assert calls[0]["X-Test"] == "1" and calls[0]["X-Other"] == "a"
    assert calls[1]["X-Test"] == "1" and calls[1]["X-Other"] == "b"


def test_t3_default_headers_not_mutated_and_survive_reconfigure():
    client, calls = make_client()
    client.configure(default_headers={"A": "1"})
    client.request("GET", "connect", "/a")
    client.request("GET", "connect", "/b", referrer=True)
    client.configure(timeout=5)
    client.request("GET", "connect", "/c")
    assert client.default_headers == {"A": "1"}
    assert calls[2]["A"] == "1" and "referer" not in calls[2]


def test_t3_callers_headers_dict_is_not_mutated():
    client, _ = make_client()
    mine = {"X-Mine": "1"}
    client.request("GET", "connect", "/a")
    client.request("GET", "connect", "/b", referrer=True, headers=mine)
    assert mine == {"X-Mine": "1"}


# ------------------------------------------------------------------ task 4: key conversion helpers
def test_t4_lists_nested_in_lists_are_converted():
    assert camel_to_snake_dict({"outerKey": [[{"innerKey": 1}], [2]]}) == {"outer_key": [[{"inner_key": 1}], [2]]}
    assert remove_dto_suffix_from_dict({"summary_dto": [[{"x_dto": 1}], 3]}) == {"summary": [[{"x": 1}], 3]}


def test_t4_non_string_keys_are_kept():
    assert camel_to_snake_dict({1: "a", "aB": 2}) == {1: "a", "a_b": 2}
    assert remove_dto_suffix_from_dict({7: {"k_dto": 1}}) == {7: {"k": 1}}


def test_t4_camel_case_dto_suffix_is_removed():
    assert remove_dto_suffix("summaryDTO") == "summary"
    assert remove_dto_suffix("summary_dto") == "summary"
    assert remove_dto_suffix("dto") == "dto"
    assert remove_dto_suffix("") == ""


def test_t4_inputs_are_not_mutated_and_plain_values_pass_through():
    source = {"aB": {"cD": [1, {"eF": 2}]}, "gH": None}
    before = json.dumps(source, sort_keys=True)
    assert camel_to_snake_dict(source) == {"a_b": {"c_d": [1, {"e_f": 2}]}, "g_h": None}
    assert json.dumps(source, sort_keys=True) == before


# ------------------------------------------------------------------ task 5: daily training status parsing
def training_response(**devices):
    return {"mostRecentTrainingStatus": {"payload": {"latestTrainingStatusData": devices}}}


def test_t5_every_device_is_returned_and_flattened():
    response = training_response(
        d1={"trainingStatus": 4, "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 10}},
        d2={"trainingStatus": 5, "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 20}},
    )
    assert DailyTrainingStatus._parse_response(response) == [
        {"trainingStatus": 4, "dailyTrainingLoadAcute": 10},
        {"trainingStatus": 5, "dailyTrainingLoadAcute": 20},
    ]


def test_t5_response_is_not_mutated():
    response = training_response(d1={"trainingStatus": 4, "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 10}})
    before = json.dumps(response, sort_keys=True)
    DailyTrainingStatus._parse_response(response)
    assert json.dumps(response, sort_keys=True) == before


def test_t5_non_dict_devices_are_skipped_and_a_missing_load_block_is_fine():
    response = training_response(d0="oops", d1={"trainingStatus": 4}, d2=None)
    assert DailyTrainingStatus._parse_response(response) == [{"trainingStatus": 4}]


def test_t5_malformed_responses_give_an_empty_list():
    for bad in ({}, [], None, "x", {"mostRecentTrainingStatus": []}, {"mostRecentTrainingStatus": {"payload": 3}}, training_response()):
        assert DailyTrainingStatus._parse_response(bad) == []


# ------------------------------------------------------------------ task 6: Stats.list paging
@dataclass
class FakeStat(Stats):
    value: int | None = None
    _path: ClassVar[str] = "/daily/{start}/{end}"
    _page_size: ClassVar[int] = 3


class StatsClient:
    def __init__(self, items):
        self.items, self.paths = items, []

    def connectapi(self, path, **kwargs):
        self.paths.append(path)
        start, end = (date.fromisoformat(part) for part in path.split("/")[-2:])
        return [item for item in self.items if start <= date.fromisoformat(item["calendarDate"]) <= end]


def day_items(*days):
    return [{"calendarDate": f"2024-01-{d:02d}", "value": d} for d in days]


def test_t6_an_empty_newest_page_still_returns_the_older_ones():
    client = StatsClient(day_items(1, 2, 3, 4))
    got = FakeStat.list("2024-01-07", 7, client=client)
    assert [s.calendar_date.day for s in got] == [1, 2, 3, 4]


def test_t6_pages_are_requested_once_each_and_come_back_oldest_first():
    client = StatsClient(day_items(1, 2, 3, 4, 5, 6, 7))
    got = FakeStat.list("2024-01-07", 7, client=client)
    assert [s.calendar_date.day for s in got] == [1, 2, 3, 4, 5, 6, 7]
    assert len(client.paths) == 3


def test_t6_duplicate_dates_are_returned_once_keeping_the_last():
    items = day_items(1, 2) + [{"calendarDate": "2024-01-02", "value": 99}]
    got = FakeStat.list("2024-01-02", 2, client=StatsClient(items))
    assert [(s.calendar_date.day, s.value) for s in got] == [(1, 1), (2, 99)]


def test_t6_a_period_below_one_is_an_error():
    for bad in (0, -3):
        try:
            FakeStat.list("2024-01-07", bad, client=StatsClient([]))
        except ValueError:
            continue
        raise AssertionError("expected ValueError for period %s" % bad)


# ------------------------------------------------------------------ tickets 7-11: one feature (the Body Battery event query) revised five times
def item(activity_id=None, name=None, **event_fields):
    entry = event(**event_fields)
    if activity_id is not None:
        entry["activityId"] = activity_id
    if name is not None:
        entry["activityName"] = name
    return entry


def query(*items, **kwargs):
    return BodyBatteryData.get("2024-01-01", client=FakeClient(list(items)), **kwargs)


def kinds(result):
    return [r.event.event_type if r.event else None for r in result]


def starts(result):
    return [r.event.event_start_time_gmt.hour if r.event else None for r in result]


def at(hour):
    return f"2024-01-01T{hour:02d}:00:00Z"


NO_EVENT = {"activityName": "walk"}


def test_t7_defaults_are_unchanged():
    items = [item(eventType="nap"), NO_EVENT, item(eventType="sleep")]
    assert kinds(query(*items)) == ["nap", None, "sleep"]


def test_t7_event_types_filter_keeps_order():
    got = query(item(eventType="sleep"), item(eventType="nap"), item(eventType="activity"), event_types=["sleep", "nap"])
    assert kinds(got) == ["sleep", "nap"]
    assert kinds(query(item(eventType="sleep"), item(eventType="nap"), event_types=("nap",))) == ["nap"]
    assert query(item(eventType="sleep"), event_types=[]) == []


def test_t7_min_impact_uses_the_absolute_value():
    got = query(item(bodyBatteryImpact=5), item(bodyBatteryImpact=-20), item(bodyBatteryImpact=2), item(bodyBatteryImpact=50), min_impact=10)
    assert [r.event.body_battery_impact for r in got] == [-20, 50]


def test_t7_filters_combine_and_drop_items_without_an_event():
    items = [item(eventType="sleep", bodyBatteryImpact=30), item(eventType="sleep", bodyBatteryImpact=1), item(eventType="nap", bodyBatteryImpact=40), NO_EVENT]
    assert len(query(*items)) == 4
    assert [r.event.body_battery_impact for r in query(*items, event_types=["sleep"], min_impact=10)] == [30]
    assert None not in kinds(query(*items, event_types=["sleep", "nap"]))
    assert None not in kinds(query(*items, min_impact=0))


def test_t8_order_asc_and_desc_with_eventless_items_last():
    items = [item(eventStartTimeGmt=at(13)), NO_EVENT, item(eventStartTimeGmt=at(9)), item(eventStartTimeGmt=at(11))]
    assert starts(query(*items, order="asc")) == [9, 11, 13, None]
    assert starts(query(*items, order="desc")) == [13, 11, 9, None]
    assert starts(query(*items, order=None)) == [13, None, 9, 11]


def test_t8_the_sort_is_stable():
    items = [item(name="a", eventStartTimeGmt=at(10)), item(name="b", eventStartTimeGmt=at(10)), item(name="c", eventStartTimeGmt=at(8))]
    assert [r.activity_name for r in query(*items, order="asc")] == ["c", "a", "b"]
    assert [r.activity_name for r in query(*items, order="desc")] == ["a", "b", "c"]


def test_t8_limit_applies_after_filtering_and_ordering():
    items = [item(eventType="sleep", eventStartTimeGmt=at(h)) for h in (12, 10, 11)] + [item(eventType="nap", eventStartTimeGmt=at(1))]
    assert starts(query(*items, event_types=["sleep"], order="asc", limit=2)) == [10, 11]
    assert query(*items, limit=0) == []
    assert len(query(*items, limit=99)) == 4


def test_t8_bad_order_or_limit_raises_value_error():
    for kwargs in ({"order": "up"}, {"limit": -1}):
        try:
            query(item(), **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


def test_t9_dedupe_keeps_the_first_of_each_activity_id():
    items = [item(activity_id=1, name="a"), item(activity_id=1, name="b"), item(activity_id=2, name="c"), item(name="d"), item(name="e")]
    assert [r.activity_name for r in query(*items, dedupe=True)] == ["a", "c", "d", "e"]
    assert len(query(*items)) == 5 and len(query(*items, dedupe=False)) == 5


def test_t9_items_without_an_event_are_deduped_too():
    items = [dict(NO_EVENT, activityId=7), dict(NO_EVENT, activityId=7, activityName="again")]
    assert len(query(*items, dedupe=True)) == 1


def test_t9_order_of_operations_is_filter_then_dedupe_then_order_then_limit():
    nap_then_sleep = [item(activity_id=1, name="nap", eventType="nap"), item(activity_id=1, name="sleep", eventType="sleep")]
    assert [r.activity_name for r in query(*nap_then_sleep, event_types=["sleep"], dedupe=True)] == ["sleep"]
    items = [item(activity_id=1, name="a"), item(activity_id=1, name="b"), item(activity_id=2, name="c")]
    assert [r.activity_name for r in query(*items, dedupe=True, limit=2)] == ["a", "c"]


def summary(*items, **kwargs):
    return BodyBatteryData.summary("2024-01-01", client=FakeClient(list(items)), **kwargs)


def test_t10_summary_counts_and_totals():
    items = [item(eventType="sleep", bodyBatteryImpact=5, durationInMilliseconds=3600000), item(eventType="sleep", bodyBatteryImpact=-3, durationInMilliseconds=1000),
             item(eventType="nap", bodyBatteryImpact=10, durationInMilliseconds=500), NO_EVENT]
    got = summary(*items)
    assert got["count"] == 4 and got["total_impact"] == 12 and got["total_duration_ms"] == 3601500
    assert got["by_type"] == {"sleep": 2, "nap": 1}


def test_t10_summary_takes_the_same_options_as_get():
    items = [item(activity_id=1, eventType="sleep", bodyBatteryImpact=5), item(activity_id=1, eventType="sleep", bodyBatteryImpact=6), item(activity_id=2, eventType="nap", bodyBatteryImpact=9)]
    assert summary(*items, event_types=["sleep"])["count"] == 2
    assert summary(*items, dedupe=True)["total_impact"] == 14
    assert summary(*items, limit=1)["count"] == 1 and summary(*items, min_impact=8)["by_type"] == {"nap": 1}


def test_t10_empty_and_non_list_responses_give_an_empty_summary():
    for payload in ([], {"oops": 1}):
        got = BodyBatteryData.summary("2024-01-01", client=FakeClient(payload))
        assert (got["count"], got["total_impact"], got["total_duration_ms"], got["by_type"]) == (0, 0, 0, {})


def test_t11_min_duration_filter_in_get_and_summary():
    items = [item(durationInMilliseconds=100), item(durationInMilliseconds=5000), item(durationInMilliseconds=9000), NO_EVENT]
    assert [r.event.duration_in_milliseconds for r in query(*items, min_duration_ms=5000)] == [5000, 9000]
    assert summary(*items, min_duration_ms=5000)["count"] == 2
    assert query(*items, min_duration_ms=5000, event_types=["nap"]) == []


def test_t11_average_impact_by_type_is_rounded_and_ignores_eventless_items():
    items = [item(eventType="sleep", bodyBatteryImpact=5), item(eventType="sleep", bodyBatteryImpact=-3), item(eventType="nap", bodyBatteryImpact=10), item(eventType="nap", bodyBatteryImpact=1),
             item(eventType="nap", bodyBatteryImpact=1), NO_EVENT]
    assert summary(*items)["avg_impact_by_type"] == {"sleep": 1.0, "nap": 4.0}
    assert summary(*items[:3:2])["avg_impact_by_type"] == {"sleep": 5.0, "nap": 10.0}
    assert BodyBatteryData.summary("2024-01-01", client=FakeClient([]))["avg_impact_by_type"] == {}


# ------------------------------------------------------------------ tickets 12-25: the same feature, revised fourteen more times
def tkw(*items, **kwargs):
    return query(*items, **kwargs)


def hours(result):
    return starts(result)


def item_a(activity_type, **kwargs):
    entry = item(**kwargs)
    entry["activityType"] = activity_type
    return entry


def test_t12_max_duration_filter():
    items = [item(durationInMilliseconds=100), item(durationInMilliseconds=5000), item(durationInMilliseconds=9000), NO_EVENT]
    assert [r.event.duration_in_milliseconds for r in query(*items, max_duration_ms=5000)] == [100, 5000]
    assert [r.event.duration_in_milliseconds for r in query(*items, min_duration_ms=200, max_duration_ms=9000)] == [5000, 9000]
    assert summary(*items, max_duration_ms=5000)["count"] == 2


def test_t13_max_impact_filter_uses_the_absolute_value():
    items = [item(bodyBatteryImpact=5), item(bodyBatteryImpact=-20), item(bodyBatteryImpact=2), item(bodyBatteryImpact=50), NO_EVENT]
    assert [r.event.body_battery_impact for r in query(*items, max_impact=10)] == [5, 2]
    assert [r.event.body_battery_impact for r in query(*items, min_impact=3, max_impact=25)] == [5, -20]
    assert summary(*items, max_impact=10)["count"] == 2


def test_t14_exclude_types():
    items = [item(eventType="sleep"), item(eventType="nap"), item(eventType="activity"), NO_EVENT]
    assert kinds(query(*items, exclude_types=["nap"])) == ["sleep", "activity"]
    assert kinds(query(*items, exclude_types=("nap", "sleep"))) == ["activity"]
    assert kinds(query(*items, exclude_types=[])) == ["sleep", "nap", "activity"]
    assert kinds(query(*items, event_types=["sleep", "nap"], exclude_types=["nap"])) == ["sleep"]
    assert summary(*items, exclude_types=["nap"])["by_type"] == {"sleep": 1, "activity": 1}


def test_t15_time_window_accepts_strings_and_datetimes():
    from datetime import datetime, timezone
    items = [item(eventStartTimeGmt=at(8)), item(eventStartTimeGmt=at(10)), item(eventStartTimeGmt=at(12)), NO_EVENT]
    assert starts(query(*items, since="2024-01-01T09:00:00Z")) == [10, 12]
    assert starts(query(*items, until="2024-01-01T10:00:00Z")) == [8, 10]
    assert starts(query(*items, since="2024-01-01T08:30:00Z", until="2024-01-01T11:00:00Z")) == [10]
    assert starts(query(*items, since=datetime(2024, 1, 1, 12, tzinfo=timezone.utc))) == [12]
    assert starts(query(*items, until=datetime(2024, 1, 1, 8))) == [8]
    assert summary(*items, since="2024-01-01T09:00:00Z")["count"] == 2


def test_t15_a_bad_time_string_raises_value_error():
    try:
        query(item(), since="not a time")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_t16_feedback_types_filter():
    items = [item(feedbackType="good"), item(feedbackType="bad"), item(feedbackType="ok"), NO_EVENT]
    assert [r.event.feedback_type for r in query(*items, feedback_types=["good", "ok"])] == ["good", "ok"]
    assert query(*items, feedback_types=[]) == []
    assert summary(*items, feedback_types=("bad",))["count"] == 1


def test_t17_activity_types_filter():
    items = [item_a("running", name="a"), item_a("cycling", name="b"), item(name="c"), dict(NO_EVENT, activityType="running")]
    assert [r.activity_name for r in query(*items, activity_types=["running"])] == ["a"]
    assert [r.activity_name for r in query(*items, activity_types=("running", "cycling"))] == ["a", "b"]
    assert len(query(*items)) == 4
    assert summary(*items, activity_types=["cycling"])["count"] == 1


def test_t18_order_by_impact_and_duration():
    items = [item(bodyBatteryImpact=5, durationInMilliseconds=300), item(bodyBatteryImpact=-20, durationInMilliseconds=100), NO_EVENT, item(bodyBatteryImpact=50, durationInMilliseconds=200)]
    assert [r.event.body_battery_impact if r.event else None for r in query(*items, order="asc", order_by="impact")] == [-20, 5, 50, None]
    assert [r.event.body_battery_impact if r.event else None for r in query(*items, order="desc", order_by="impact")] == [50, 5, -20, None]
    assert [r.event.duration_in_milliseconds if r.event else None for r in query(*items, order="asc", order_by="duration")] == [100, 200, 300, None]
    assert [r.event.duration_in_milliseconds if r.event else None for r in query(*items, order="desc", order_by="duration")] == [300, 200, 100, None]


def test_t18_order_by_defaults_to_start_and_rejects_other_values():
    items = [item(eventStartTimeGmt=at(13)), item(eventStartTimeGmt=at(9))]
    assert starts(query(*items, order="asc")) == [9, 13]
    assert starts(query(*items, order="asc", order_by="start")) == [9, 13]
    for kwargs in ({"order_by": "color"}, {"order": "asc", "order_by": "nope"}):
        try:
            query(*items, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


def test_t19_offset_skips_after_ordering_and_before_limit():
    items = [item(eventStartTimeGmt=at(h)) for h in (11, 8, 12, 9, 10)]
    assert starts(query(*items, order="asc", offset=2)) == [10, 11, 12]
    assert starts(query(*items, order="asc", offset=1, limit=2)) == [9, 10]
    assert starts(query(*items, order="desc", offset=3)) == [9, 8]
    assert query(*items, offset=9) == []
    assert len(query(*items, offset=0)) == 5
    assert summary(*items, offset=2, limit=2)["count"] == 2


def test_t19_a_negative_offset_raises_value_error():
    try:
        query(item(), offset=-1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_t20_average_duration_by_type_is_rounded():
    items = [item(eventType="sleep", durationInMilliseconds=1000), item(eventType="sleep", durationInMilliseconds=1001), item(eventType="nap", durationInMilliseconds=1),
             item(eventType="nap", durationInMilliseconds=1), item(eventType="nap", durationInMilliseconds=2), NO_EVENT]
    assert summary(*items)["avg_duration_ms_by_type"] == {"sleep": 1000.5, "nap": 1.33}
    assert BodyBatteryData.summary("2024-01-01", client=FakeClient([]))["avg_duration_ms_by_type"] == {}


def test_t21_impact_range_in_the_summary():
    items = [item(bodyBatteryImpact=5), item(bodyBatteryImpact=-3), item(bodyBatteryImpact=10), NO_EVENT]
    got = summary(*items)
    assert got["max_impact"] == 10 and got["min_impact"] == -3
    empty = summary(NO_EVENT)
    assert empty["max_impact"] is None and empty["min_impact"] is None


def test_t22_events_by_hour():
    items = [item(eventStartTimeGmt=at(8)), item(eventStartTimeGmt="2024-01-01T08:45:00Z"), item(eventStartTimeGmt=at(13)), NO_EVENT]
    assert summary(*items)["by_hour"] == {8: 2, 13: 1}
    assert BodyBatteryData.summary("2024-01-01", client=FakeClient([]))["by_hour"] == {}


def test_t23_busiest_type_breaks_ties_alphabetically():
    items = [item(eventType="sleep"), item(eventType="nap"), item(eventType="nap"), item(eventType="sleep"), item(eventType="activity")]
    assert summary(*items)["busiest_type"] == "nap"
    assert summary(*items[:3])["busiest_type"] == "nap"
    assert summary(item(eventType="b"), item(eventType="a"))["busiest_type"] == "a"
    assert summary(NO_EVENT)["busiest_type"] is None


def test_t24_include_eventless_bypasses_filters():
    items = [item(eventType="sleep"), item(eventType="nap"), NO_EVENT, dict(NO_EVENT, activityName="again")]
    assert len(query(*items, event_types=["sleep"])) == 1
    assert kinds(query(*items, event_types=["sleep"], include_eventless=True)) == ["sleep", None, None]
    assert len(query(*items, include_eventless=True)) == 4
    assert summary(*items, event_types=["sleep"], include_eventless=True)["count"] == 3
    assert len(query(*items, min_impact=0, include_eventless=True, limit=2)) == 2


def test_t25_dedupe_by_start_time():
    items = [item(activity_id=1, name="a", eventStartTimeGmt=at(8)), item(activity_id=2, name="b", eventStartTimeGmt=at(8)), item(activity_id=2, name="c", eventStartTimeGmt=at(9)), NO_EVENT, NO_EVENT]
    assert [r.activity_name for r in query(*items, dedupe=True, dedupe_by="start_time")] == ["a", "c", "walk", "walk"]
    assert [r.activity_name for r in query(*items, dedupe=True)] == ["a", "b", "walk", "walk"]
    assert len(query(*items, dedupe_by="start_time")) == 5
    assert summary(*items, dedupe=True, dedupe_by="start_time")["count"] == 4


def test_t25_dedupe_by_rejects_other_values_even_without_dedupe():
    try:
        query(item(), dedupe_by="colour")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
