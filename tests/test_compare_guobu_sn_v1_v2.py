# -*- coding: utf-8 -*-
import pytest

from tools.compare_guobu_sn_v1_v2 import compare_result_sets


def entry(order_id, *, manual_flag, code="", observed="", sn_match=False):
    return {
        "task": {"channel_order_no": order_id},
        "row": {
            "id": order_id,
            "manual_flag": manual_flag,
            "manual_reason_code": code,
            "observed_sn": observed,
            "sn_match": sn_match,
        },
    }


def test_compare_joins_by_order_id_not_row_position():
    v1 = [
        entry("A", manual_flag="是", code="SN_MISMATCH", observed="OLD-A"),
        entry("B", manual_flag="否", observed="B-SN", sn_match=True),
    ]
    v2 = [
        entry("B", manual_flag="否", observed="B-SN", sn_match=True),
        entry("A", manual_flag="否", observed="A-SN", sn_match=True),
    ]

    rows = compare_result_sets(v1, v2)

    assert [row["订单号"] for row in rows] == ["A", "B"]
    assert rows[0]["结论是否变化"] == "是"
    assert rows[1]["结论是否变化"] == "否"


def test_compare_keeps_orders_missing_from_either_side():
    rows = compare_result_sets(
        [entry("V1_ONLY", manual_flag="是", code="SN_NOT_FOUND")],
        [entry("V2_ONLY", manual_flag="是", code="MODEL_UNCERTAIN")],
    )

    by_id = {row["订单号"]: row for row in rows}
    assert by_id["V1_ONLY"]["V2数据状态"] == "缺失"
    assert by_id["V2_ONLY"]["V1数据状态"] == "缺失"
    assert by_id["V1_ONLY"]["结论是否变化"] == "无法比较"


def test_compare_rejects_conflicting_task_and_row_order_ids():
    conflicting = entry("TASK-A", manual_flag="否", sn_match=True)
    conflicting["row"]["id"] = "ROW-B"

    with pytest.raises(ValueError, match="conflicting order IDs"):
        compare_result_sets([conflicting], [])


def test_compare_treats_v1_sn_only_wrapper_as_same_matching_sn_conclusion():
    v1 = entry(
        "A",
        manual_flag="是",
        code="SN_ONLY_MATCH_NOT_FULL_AUDIT",
        observed="ABC123",
        sn_match=True,
    )
    v2 = entry("A", manual_flag="否", observed="ABC123", sn_match=True)

    rows = compare_result_sets([v1], [v2])

    assert rows[0]["结论是否变化"] == "否"
