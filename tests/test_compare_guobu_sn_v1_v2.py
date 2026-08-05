# -*- coding: utf-8 -*-
import csv

import openpyxl
import pytest

from tools.compare_guobu_sn_v1_v2 import compare_result_sets, write_readable_outputs


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


def test_write_readable_outputs_uses_fixed_user_facing_format(tmp_path):
    rows = compare_result_sets(
        [
            entry(
                " 481172630855936232652806 ",
                manual_flag="是",
                code="SN_ONLY_MATCH_NOT_FULL_AUDIT",
                observed="OLD-SN",
                sn_match=True,
            ),
            entry("481173289704609837875201", manual_flag="是", code="SN_MISMATCH", observed="OLD-ZN"),
        ],
        [
            entry("481172630855936232652806", manual_flag="否", observed="NEW-SN", sn_match=True),
            entry("481173289704609837875201", manual_flag="是", code="MODEL_UNCERTAIN", observed="NEW-2N"),
        ],
    )
    dataset = [
        {"channel_order_no": "481172630855936232652806", "system_sn": "SYS-SN"},
        {"channel_order_no": "481173289704609837875201", "system_sn": "SYS-2N"},
    ]

    csv_path, xlsx_path = write_readable_outputs(rows, tmp_path / "sn_compare_readable", dataset)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        readable_rows = list(csv.DictReader(handle))

    assert list(readable_rows[0].keys()) == [
        "渠道订单号",
        "结论是否一致",
        "新版SN",
        "旧版SN",
        "系统SN",
        "新版结果",
        "旧版结果",
        "新版原因码",
        "旧版原因码",
    ]
    assert readable_rows[0] == {
        "渠道订单号": "481172630855936232652806",
        "结论是否一致": "一致",
        "新版SN": "NEW-SN",
        "旧版SN": "OLD-SN",
        "系统SN": "SYS-SN",
        "新版结果": "通过",
        "旧版结果": "通过",
        "新版原因码": "PASS（SN一致，通过）",
        "旧版原因码": "SN_ONLY_MATCH_NOT_FULL_AUDIT（SN一致，SN单项通过；非完整审核结论）",
    }
    assert readable_rows[1]["新版结果"] == "不通过/转人工"
    assert readable_rows[1]["旧版结果"] == "不通过/转人工"
    assert readable_rows[1]["新版原因码"] == "MODEL_UNCERTAIN（模型不确定，转人工）"

    workbook = openpyxl.load_workbook(xlsx_path)
    sheet = workbook["SN对比回执"]
    assert sheet["A2"].value == "481172630855936232652806"
    assert sheet["A2"].data_type == "s"
    assert sheet["A2"].number_format == "@"
    assert sheet["E2"].value == "SYS-SN"


def test_write_readable_outputs_falls_back_when_xlsx_target_is_locked(tmp_path, monkeypatch):
    rows = compare_result_sets(
        [entry("481172630855936232652806", manual_flag="否", observed="OLD-SN", sn_match=True)],
        [entry("481172630855936232652806", manual_flag="否", observed="NEW-SN", sn_match=True)],
    )
    dataset = [{"channel_order_no": "481172630855936232652806", "system_sn": "SYS-SN"}]
    from openpyxl.workbook.workbook import Workbook

    original_save = Workbook.save

    def save_with_locked_target(self, filename):
        if str(filename).endswith("sn_compare_readable.xlsx"):
            raise PermissionError("locked by viewer")
        return original_save(self, filename)

    monkeypatch.setattr(Workbook, "save", save_with_locked_target)

    _csv_path, xlsx_path = write_readable_outputs(rows, tmp_path / "sn_compare_readable", dataset)

    assert xlsx_path.name == "sn_compare_readable_unlocked.xlsx"
    assert xlsx_path.exists()
