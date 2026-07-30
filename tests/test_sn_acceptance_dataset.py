# -*- coding: utf-8 -*-
import csv
import json

import pytest

from tools.build_sn_acceptance_dataset import (
    DATASET_ALLOWED_FIELDS,
    REPORT_HEADERS,
    build_dataset,
    write_report_template,
)


def task(
    *,
    order_no="ORDER-1",
    system_sn="SN123456",
    source_flow_status="未通过",
    activation_images=None,
):
    if activation_images is None:
        activation_images = [
            {
                "image_id": "img_003",
                "title": "SN码采集 / 激活照片",
                "source_url": "https://example.test/sn.jpg",
                "local_path": "data/sample/images/ORDER-1/img_003.jpg",
            }
        ]
    return {
        "channel_order_no": order_no,
        "fields": {
            "system_sn": system_sn,
            "source_flow_status": source_flow_status,
            "product_type": "手机",
            "cate_code_name": "普通3C",
        },
        "image_groups": {
            "商品照片": [{"image_id": "img_001", "source_url": "https://example.test/product.jpg"}],
            "SN码采集 / 激活照片": activation_images,
        },
    }


def write_task(tmp_path, name, payload):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    path = tasks_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tasks_dir


def test_dataset_records_only_raw_sn_acceptance_fields(tmp_path):
    tasks_dir = write_task(tmp_path, "ORDER-1", task())

    result = build_dataset(tasks_dir)

    assert result.records == [
        {
            "channel_order_no": "ORDER-1",
            "system_sn": "SN123456",
            "source_flow_status": "未通过",
            "activation_sn_images": [
                {
                    "image_id": "img_003",
                    "source_url": "https://example.test/sn.jpg",
                    "local_path": "data/sample/images/ORDER-1/img_003.jpg",
                }
            ],
        }
    ]
    assert set(result.records[0]) == DATASET_ALLOWED_FIELDS
    forbidden = {"product_type", "cate_code_name", "old_sn_result", "new_sn_result", "diff_reason"}
    assert not (forbidden & set(result.records[0]))


@pytest.mark.parametrize(
    ("payload", "expected_issue"),
    [
        (task(system_sn=""), "MISSING_SYSTEM_SN"),
        (task(source_flow_status=""), "MISSING_SOURCE_FLOW_STATUS"),
        (task(activation_images=[]), "MISSING_ACTIVATION_SN_IMAGE"),
    ],
)
def test_dataset_skips_records_missing_required_raw_fields(tmp_path, payload, expected_issue):
    tasks_dir = write_task(tmp_path, "ORDER-1", payload)

    result = build_dataset(tasks_dir)

    assert result.records == []
    assert result.issues[0]["code"] == expected_issue
    assert result.issues[0]["channel_order_no"] == "ORDER-1"


def test_report_template_keeps_derived_fields_out_of_dataset_schema(tmp_path):
    report_path = tmp_path / "sn_compare_report_template.csv"

    write_report_template(report_path)

    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        headers = next(csv.reader(handle))
    assert headers == REPORT_HEADERS
    assert "old_sn_result" in headers
    assert "new_sn_result" in headers
    assert "diff_reason" in headers
    assert not (set(headers) & {"system_sn", "source_flow_status", "activation_sn_images"})
