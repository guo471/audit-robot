# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import http.client
import hashlib
import base64
import json
import mimetypes
import os
import re
import sys
import tempfile
import time
import traceback
import threading
import urllib.request
import urllib.error
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.category_classifier import classify_audit_category
from tools.photo_authenticity_mainline import (
    EXPECTED_LOCAL_TREE_SHA256,
    PhotoAuthenticityConfig,
    ImageObservation,
    LocalTreeNonRealRescue,
    PhotoAuthenticitySchemaError,
    apply_photo_authenticity_gate,
    validate_image_observations,
)
from tools.guobu_sn_policy_v2 import (
    SnCategory as SnV2Category,
    build_model_payload as build_sn_v2_payload,
    build_sn_prompt as build_sn_v2_prompt,
    classify_sn_category as classify_sn_v2_category,
    decide_sn as decide_sn_v2,
)
from tools.guobu_sn_barcode import BarcodeScanner, apply_barcode_second_check


def resolve_sn_policy_version(value: Any = None) -> str:
    selected = str(value if value is not None else os.environ.get("SN_POLICY_VERSION", "v1")).strip().lower()
    if selected not in {"v1", "v2"}:
        raise ValueError("SN_POLICY_VERSION must be v1 or v2")
    return selected


def resolve_sn_barcode_mode(value: Any = None) -> str:
    selected = str(value if value is not None else os.environ.get("SN_BARCODE_MODE", "enforce")).strip().lower()
    if selected not in {"off", "shadow", "enforce"}:
        raise ValueError("SN_BARCODE_MODE must be off, shadow, or enforce")
    return selected


SN_PROMPT = """你是国补审核的 SN 专项识别员。只输出严格 JSON 对象。
你只能查看输入中的 SN码采集 / 激活照片 / 序列号照片分组，不能使用商品照片或拆封照片里的 SN 作为通过依据。
任务：独立逐字读取激活照片分组中可见的 SN/Serial Number。
必须分别读取设备屏幕、机身铭牌和包装标签中的全部 SN 候选，不得把多个来源合并成一个值。
每个候选必须标注 source=DEVICE_SCREEN、DEVICE_BODY 或 PACKAGE_LABEL，并标注 field_type=SN 或 SERIAL。
sn_candidates 中不要列出 IMEI；IMEI 只由后续合规阶段判断是否构成屏幕身份信息。
如果设备屏幕存在清晰 SN，observed_sn 必须使用屏幕 SN；只有屏幕没有 SN 时，才可以使用机身或包装 SN。
屏幕 SN 与包装 SN 不一致时必须保留两个候选，不得声称二者一致，也不得用包装 SN 覆盖屏幕 SN。
不要猜测、纠正或补全图片 SN；O 和 0、Q 和 0、I 和 1、L 和 1、S 和 5、B 和 8 必须按看到的字符原样输出。代码会在下一步结合系统 SN 做确定性比对和有限视觉容错。
SN 等价文字包括：SN、S/N、SN码、序列号、Serial No.、条形码下方数字、包装标签 S/N、屏幕关于本机/设备信息页序列号。
比对时忽略大小写、空格、横杠、下划线、斜杠、冒号、点号、逗号、括号等标点；但字符本身不能错。
电脑、笔记本属于数码 3C，应优先查看 BIOS/系统信息页、设备信息页、包装标签和条码下方文字。
手机、平板等数码 3C 不强制要求屏幕显示完整 SN；屏幕只显示 1码 或 2码 时，也要优先逐字读取同一激活/SN照片组里的盒子/包装 S/N。
亮屏不等于合格；如果既没有屏幕序列号，也没有清晰包装 S/N，则返回 SN_NOT_FOUND。
如果明确看到完整 SN，返回 observed_sn 和 normalized_observed_sn；不要因为不掌握系统 SN 而返回空。
如果看不清完整 SN，返回 sn_match=false 并使用 SN_NOT_FOUND，不要猜。
如果激活照片是拍摄另一台手机/电脑屏幕里保存的激活图、相册图、截图或图片查看器，里面的 SN 不能作为自动通过依据，返回 sn_match=false 和 MODEL_UNCERTAIN 或 SN_MISMATCH。
首轮输入不会给出完整 system_sn；sn_match 只表示是否读到完整可用 SN，不表示已经和系统 SN 比对。
输出字段：sn_match, observed_sn, normalized_observed_sn, sn_candidates, manual_reason_code, manual_reason, confidence。
sn_candidates 是数组，每项包含 source, field_type, raw_text, normalized_text, readable, confidence。可额外输出 uncertain_positions 或 visual_ambiguity_notes。
"""


SN_SIMILAR_CHAR_REVIEW_PROMPT = (
    PROJECT_ROOT / "prompts" / "sn_similar_char_review.txt"
).read_text(encoding="utf-8").strip()
SN_SIMILAR_CHAR_REVIEW_V2_PROMPT = (
    PROJECT_ROOT / "prompts" / "sn_similar_char_review_v2.txt"
).read_text(encoding="utf-8").strip()
SN_CHAR_REVIEW_MODES = {"off", "on", "v2"}


def resolve_sn_char_review_mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.environ.get("SN_CHAR_REVIEW_MODE", "off")).strip().lower()
    if mode not in SN_CHAR_REVIEW_MODES:
        raise ValueError(f"invalid SN character review mode: {mode}")
    return mode


def build_sn_prompt(mode: str | None = None) -> str:
    resolved_mode = resolve_sn_char_review_mode(mode)
    if resolved_mode == "off":
        return SN_PROMPT
    fragment = (
        SN_SIMILAR_CHAR_REVIEW_PROMPT
        if resolved_mode == "on"
        else SN_SIMILAR_CHAR_REVIEW_V2_PROMPT
    )
    return SN_PROMPT + "\n\n" + fragment


SN_LABEL_AUTH_REVIEW_PROMPT_PATH = PROJECT_ROOT / "prompts" / "sn_label_authenticity_review.txt"
_SN_LABEL_AUTH_REVIEW_PROMPT_CACHE: str | None = None
SN_LABEL_AUTH_REVIEW_MODES = {"off", "on"}
PHOTO_AUTH_EDGE_MAPPING_PROMPT_PATH = PROJECT_ROOT / "prompts" / "photo_auth_edge_mapping_review.txt"
_PHOTO_AUTH_EDGE_MAPPING_PROMPT_CACHE: str | None = None
PHOTO_AUTH_EDGE_MAPPING_MODES = {"off", "on"}
PHOTO_AUTH_EDGE_DIAGNOSTIC_PREFIX = "edge_candidate__"
DIGITAL_ACTIVATION_EVIDENCE_PROMPT_PATH = PROJECT_ROOT / "prompts" / "digital_activation_evidence_review.txt"
_DIGITAL_ACTIVATION_EVIDENCE_PROMPT_CACHE: str | None = None
DIGITAL_ACTIVATION_EVIDENCE_MODES = {"off", "on"}


def resolve_sn_label_auth_review_mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.environ.get("SN_LABEL_AUTH_REVIEW_MODE", "off")).strip().lower()
    if mode not in SN_LABEL_AUTH_REVIEW_MODES:
        raise ValueError(f"invalid SN label authenticity review mode: {mode}")
    return mode


def read_sn_label_auth_review_prompt() -> str:
    global _SN_LABEL_AUTH_REVIEW_PROMPT_CACHE
    if _SN_LABEL_AUTH_REVIEW_PROMPT_CACHE is None:
        _SN_LABEL_AUTH_REVIEW_PROMPT_CACHE = SN_LABEL_AUTH_REVIEW_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _SN_LABEL_AUTH_REVIEW_PROMPT_CACHE


def resolve_photo_auth_edge_mapping_mode(value: str | None = None) -> str:
    mode = str(
        value if value is not None else os.environ.get("PHOTO_AUTH_EDGE_MAPPING_MODE", "off")
    ).strip().lower()
    if mode not in PHOTO_AUTH_EDGE_MAPPING_MODES:
        raise ValueError(f"invalid photo authenticity edge mapping mode: {mode}")
    return mode


def read_photo_auth_edge_mapping_prompt() -> str:
    global _PHOTO_AUTH_EDGE_MAPPING_PROMPT_CACHE
    if _PHOTO_AUTH_EDGE_MAPPING_PROMPT_CACHE is None:
        _PHOTO_AUTH_EDGE_MAPPING_PROMPT_CACHE = PHOTO_AUTH_EDGE_MAPPING_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()
    return _PHOTO_AUTH_EDGE_MAPPING_PROMPT_CACHE


def scan_photo_auth_edge_candidates(images: list[dict[str, Any]]) -> dict[str, Any]:
    from tools.black_edge_shadow_detector import scan_image

    scans: dict[str, Any] = {}
    for image in images:
        image_id = str(image.get("image_id") or "").strip()
        local_path = str(image.get("local_path") or "").strip()
        if not image_id or not local_path:
            continue
        path = Path(local_path)
        if not path.is_file():
            continue
        try:
            scans[image_id] = scan_image(path)
        except Exception:
            continue
    return scans


def annotate_photo_auth_edge_candidates(source: Path, destination: Path, scan: Any) -> Path:
    from tools.black_edge_shadow_detector import annotate_strong_candidates

    return annotate_strong_candidates(source, destination, scan)


def _strong_edge_candidate_payload(image_id: str, diagnostic_image_id: str, scan: Any) -> list[dict[str, Any]]:
    candidates = []
    for side, evidence in scan.sides.items():
        if str(getattr(evidence, "status", "")) != "strong_candidate":
            continue
        candidates.append({
            "candidate_id": f"{image_id}:{side}",
            "image_id": image_id,
            "diagnostic_image_id": diagnostic_image_id,
            "side": side,
            "tangent_start_fraction": round(float(getattr(evidence, "tangent_start_fraction", 0.0)), 4),
            "tangent_end_fraction": round(float(getattr(evidence, "tangent_end_fraction", 0.0)), 4),
            "boundary_depth_fraction": round(float(getattr(evidence, "boundary_depth_fraction", 0.0)), 4),
            "marker": "magenta_line_at_detected_inner_boundary",
        })
    return candidates


def prepare_photo_auth_edge_mapping_inputs(
    images: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    mode: str | None = None,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if resolve_photo_auth_edge_mapping_mode(mode) == "off":
        return images, payload

    try:
        scans = scan_photo_auth_edge_candidates(images)
    except Exception:
        return images, payload

    from tools.black_edge_shadow_detector import ANNOTATION_VERSION, DETECTOR_VERSION

    candidates: list[dict[str, Any]] = []
    replacements: dict[str, dict[str, Any]] = {}
    for image in images:
        image_id = str(image.get("image_id") or "").strip()
        source_text = str(image.get("local_path") or "").strip()
        scan = scans.get(image_id)
        if not image_id or not source_text or scan is None or getattr(scan, "status", "") != "strong_candidate":
            continue
        source = Path(source_text)
        if not source.is_file():
            continue
        diagnostic_image_id = f"{PHOTO_AUTH_EDGE_DIAGNOSTIC_PREFIX}{image_id}"
        image_candidates = _strong_edge_candidate_payload(image_id, diagnostic_image_id, scan)
        if not image_candidates:
            continue
        try:
            original_digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
            geometry_digest = hashlib.sha256(
                json.dumps(image_candidates, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
            destination = Path(output_dir) / "photo_auth_edge_mapping" / (
                f"{image_id}-{original_digest}-{DETECTOR_VERSION}-{ANNOTATION_VERSION}-{geometry_digest}.png"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            annotate_photo_auth_edge_candidates(source, destination, scan)
        except Exception:
            continue
        diagnostic = dict(image)
        diagnostic.pop("source_url", None)
        diagnostic.pop("url", None)
        diagnostic["title"] = f"EDGE_CANDIDATE_DIAGNOSTIC:{image_id}"
        diagnostic["local_path"] = str(destination)
        diagnostic["_detail"] = "low"
        replacements[image_id] = diagnostic
        candidates.extend(image_candidates)

    if not candidates:
        return images, payload
    # Keep one model image per original image_id. Candidate images use the
    # annotated local copy, so the diagnostic copy cannot drift other fields
    # through duplicate image coverage.
    prepared_images = [
        replacements.get(str(image.get("image_id") or ""), image)
        for image in images
    ]
    prepared_payload = dict(payload)
    diagnostic_positions = {
        str(image.get("image_id") or ""): index
        for index, image in enumerate(prepared_images, start=1)
        if str(image.get("image_id") or "") in replacements
    }
    for candidate in candidates:
        candidate["diagnostic_image_position"] = diagnostic_positions[candidate["image_id"]]
    prepared_payload["photo_auth_edge_candidates"] = candidates
    return prepared_images, prepared_payload


def _edge_evidence_is_unbound_or_image_edge(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    regions = item.get("regions")
    if not isinstance(regions, list) or not regions:
        return True
    return "image_edge" in {str(region) for region in regions}


def _filter_unconfirmed_edge_evidence(
    observation: dict[str, Any],
    unconfirmed_sides: set[str],
) -> dict[str, Any]:
    mapped = dict(observation)
    changed = False
    edges = dict(mapped.get("edges") or {})
    for side in unconfirmed_sides:
        if edges.get(side) == "carrier_boundary":
            edges[side] = "scene_continues"
            changed = True
    if edges != mapped.get("edges"):
        mapped["edges"] = edges

    strong = list(mapped.get("strong_evidence") or [])
    filtered_strong = [
        item for item in strong
        if not (
            isinstance(item, dict)
            and item.get("code") == "EXTERNAL_PHOTO_CARRIER"
            and _edge_evidence_is_unbound_or_image_edge(item)
        )
    ]
    if filtered_strong != strong:
        mapped["strong_evidence"] = filtered_strong
        changed = True

    weak = list(mapped.get("weak_evidence") or [])
    filtered_weak = [
        item for item in weak
        if not (
            isinstance(item, dict)
            and item.get("code") == "EDGE_CUTOFF"
            and _edge_evidence_is_unbound_or_image_edge(item)
        )
    ]
    if filtered_weak != weak:
        mapped["weak_evidence"] = filtered_weak
        changed = True
    return mapped if changed else observation


def apply_photo_auth_edge_candidate_reviews(
    compliance: dict[str, Any], candidate_payload: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if resolve_photo_auth_edge_mapping_mode() == "off" or not candidate_payload:
        return compliance
    reviews = compliance.get("photo_auth_edge_candidate_reviews")
    observations = compliance.get("photo_authenticity_by_image")
    if not isinstance(observations, list):
        return compliance

    candidates = {
        str(item.get("candidate_id") or ""): item
        for item in candidate_payload
        if isinstance(item, dict) and item.get("candidate_id") and item.get("image_id") and item.get("side")
    }
    diagnostic_ids = {
        str(item.get("diagnostic_image_id") or "")
        for item in candidate_payload
        if isinstance(item, dict) and item.get("diagnostic_image_id")
    }
    original_observations = [
        item for item in observations
        if not isinstance(item, dict) or str(item.get("image_id") or "") not in diagnostic_ids
    ]
    if len(original_observations) != len(observations):
        compliance = dict(compliance)
        compliance["photo_authenticity_by_image"] = original_observations
        observations = original_observations
    if not isinstance(reviews, list):
        reviews = []

    candidate_sides: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates.values():
        candidate_sides[str(candidate["image_id"])].add(str(candidate["side"]))
    confirmed: dict[str, set[str]] = {}
    physical_features = {"screen_frame", "display_boundary", "screen_corner"}
    for review in reviews:
        if not isinstance(review, dict):
            continue
        candidate_id = str(review.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        image_id = str(review.get("image_id") or "")
        diagnostic_image_id = str(review.get("diagnostic_image_id") or "")
        expected_diagnostic_image_id = str(candidate.get("diagnostic_image_id") or "") if candidate else ""
        features = review.get("supporting_features")
        feature_set = {str(value) for value in features} if isinstance(features, list) else set()
        if (
            candidate is not None
            and image_id == str(candidate.get("image_id") or "")
            and (
                not expected_diagnostic_image_id
                or diagnostic_image_id == expected_diagnostic_image_id
            )
            and str(review.get("side") or "") == str(candidate.get("side") or "")
            and review.get("confirmed_external_screen") is True
            and review.get("classification") == "external_screen"
            and feature_set & physical_features
        ):
            confirmed.setdefault(image_id, set()).add(str(candidate["side"]))

    updated = dict(compliance)
    updated_observations: list[Any] = []
    for observation in observations:
        if not isinstance(observation, dict):
            updated_observations.append(observation)
            continue
        image_id = str(observation.get("image_id") or "")
        unconfirmed_sides = candidate_sides.get(image_id, set()) - confirmed.get(image_id, set())
        mapped = (
            _filter_unconfirmed_edge_evidence(observation, unconfirmed_sides)
            if unconfirmed_sides
            else observation
        )
        if image_id not in confirmed:
            updated_observations.append(mapped)
            continue
        mapped = dict(mapped)
        edges = dict(mapped.get("edges") or {})
        for side_name in confirmed[image_id]:
            if side_name in {"top", "right", "bottom", "left"}:
                edges[side_name] = "carrier_boundary"
        mapped["edges"] = edges
        mapped["screen_owner"] = "external_screen"
        strong = list(mapped.get("strong_evidence") or [])
        if not any(isinstance(item, dict) and item.get("code") == "EXTERNAL_PHOTO_CARRIER" for item in strong):
            strong.append({"code": "EXTERNAL_PHOTO_CARRIER", "regions": ["image_edge"]})
        mapped["strong_evidence"] = strong
        updated_observations.append(mapped)
    updated["photo_authenticity_by_image"] = updated_observations
    if updated["photo_authenticity_by_image"] == observations:
        return compliance
    return updated


def resolve_digital_activation_evidence_mode(value: str | None = None) -> str:
    mode = str(
        value if value is not None else os.environ.get("DIGITAL_ACTIVATION_EVIDENCE_MODE", "on")
    ).strip().lower()
    if mode not in DIGITAL_ACTIVATION_EVIDENCE_MODES:
        raise ValueError(f"invalid digital activation evidence mode: {mode}")
    return mode


def read_digital_activation_evidence_prompt() -> str:
    global _DIGITAL_ACTIVATION_EVIDENCE_PROMPT_CACHE
    if _DIGITAL_ACTIVATION_EVIDENCE_PROMPT_CACHE is None:
        _DIGITAL_ACTIVATION_EVIDENCE_PROMPT_CACHE = DIGITAL_ACTIVATION_EVIDENCE_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()
    return _DIGITAL_ACTIVATION_EVIDENCE_PROMPT_CACHE


SN_TARGETED_REVIEW_PROMPT = """你是国补审核的 SN 目标复核员。只输出严格 JSON 对象。
任务：只查看 SN码采集 / 激活照片 / 序列号照片分组，判断图片中是否能看到与输入 system_sn 完全一致的 SN。
不要重新发明 SN，不要用相似字符容错放行；O/0、I/1/L、S/5、B/8、E/F 等只要逐位不确定，就返回 sn_match=false 或 MODEL_UNCERTAIN。
如果能清楚看到完整 system_sn，返回 sn_match=true, matches_given_system_sn=true, observed_sn=system_sn。
如果只能看到相似但不完全一致的 SN，返回 sn_match=false, matches_given_system_sn=false, observed_sn=看到的原文, manual_reason_code=SN_MISMATCH。
如果看不清或无法确认，返回 sn_match=false, matches_given_system_sn=false, observed_sn="", manual_reason_code=MODEL_UNCERTAIN。
输出字段：sn_match, matches_given_system_sn, observed_sn, normalized_observed_sn, manual_reason_code, manual_reason, confidence。
"""

DIRECT_SN_PROMPT = """只读取图片里的 SN / S/N / Serial Number / 序列号。
只输出一个结果，不要解释，不要标点，不要 JSON，不要 Markdown。
如果能看到完整 SN，只输出 SN 原文。
如果看不到完整 SN，只输出 SN_NOT_FOUND。
不要猜测、纠正或补全；O 和 0、I 和 1、L 和 1、S 和 5、B 和 8 必须按图片原样输出。"""


COMPLIANCE_PROMPT = """你是国补订单图片合规审核员。只输出严格 JSON 对象。

重要边界：
1. 本阶段只审核图片合规，不重新执行 SN 独立识别。
2. 本阶段不判断图片中的 SN/IMEI 是否与订单 SN/IMEI 一致；只判断图片中是否存在清晰、可信、未篡改的设备身份信息证据。
3. SN 是否一致由前置 SN-only 阶段完成，本阶段不得重新识别、纠错、补全或覆盖前置 SN 结果。
4. 本阶段的“通过”只表示图片合规通过候选，不代表订单自动通过。
5. 家电、普通 3C、电脑必须分开审核。
6. 电脑属于 3C，但必须单独按电脑严格规则审核；只要商品属于电脑、笔记本、台式机、PC、Laptop、Computer，就不得适用普通 3C 或家电规则。
7. 强风险统一转人工，不自动驳回、不自动通过。
8. 照片顺序错误可以通过，不得作为转人工理由。
9. 如果无法判断且没有明确违规证据，返回 MODEL_UNCERTAIN。

审核顺序：
1. 判断商品类别：home_appliance / ordinary_3c / computer / unknown。
2. 检查发票编号旁是否有橘色感叹号。
3. 检查强风险：电子屏翻拍、网图、截图、相册图、拼图、P 图、图层不一、非实物照。
4. 检查重复图。
5. 检查商品照片。
6. 按品类检查拆封/安装照片。
7. 按品类检查激活/SN/序列号证据照片。
8. 输出 JSON。

一、发票橘色感叹号规则
如果图片中发票编号旁边存在橘色感叹号，直接转人工：
- manual_required=true
- manual_reason_codes=["INVOICE_ORANGE_WARNING"]
- invoice_orange_warning=true
- manual_reason 写“发票编号旁出现橘色感叹号，需人工复核。”
该规则优先级最高。橘色感叹号只使用 INVOICE_ORANGE_WARNING，不使用 IMAGE_STRONG_RISK。

二、通用强风险规则
以下情况直接转人工，原因码使用 IMAGE_STRONG_RISK：
- 图片明显是网图、宣传图、官网图、电商详情页、截图、相册图、图片查看器中的图片。
- 拍摄另一台手机或电脑屏幕里的商品照片、激活照片、序列号照片。
- 明显拼图、拼接、图层不一致。
- SN/IMEI/序列号区域有明显擦除、覆盖、贴字、局部底纹断裂、字体/亮度/噪声异常。
- 图片经过明显 PS 修改，导致证据链不可信。
- AI 生成图或明显非真实拍摄图。
- 多台设备或多个包装混拍，无法确定是哪一台订单商品。
- 商品、包装、屏幕之间明显不是同一实物证据链。
注意：
- 强风险只用于图片真实性或篡改风险。
- 普通缺图、看不清、证据不足，不要使用 IMAGE_STRONG_RISK，应使用对应无效码或 MODEL_UNCERTAIN。
- 只有存在明确证据时才判强风险。
- 不要因为画质一般、角度不佳、屏幕字体和包装字体不同，就直接判 P 图。
- P 图判断必须看同一表面、同一区域内文字与邻近文字是否一致。

三、重复图规则
- 如果所有必需证据位都被同一张图重复占位，导致无法形成商品、拆封/安装、激活/SN 三类独立证据，转人工，原因码 DUPLICATE_IMAGE_EVIDENCE。
- 三张照片完全重复，转人工，原因码 DUPLICATE_IMAGE_EVIDENCE。
- 如果只有两张照片重复，但激活/SN照片存在且有效，可以继续正常审核。
- 如果激活/SN照片本身无效，即使只有两张重复，也不能通过激活证据审核。
- 不同角度、同一场景但内容有差异，不算完全重复。

四、商品照片规则
商品照片合格：
- 商品单独个体照片。
- 未拆封包装照片。
- 裸机/商品本体照片。
- 外包装照片能明确体现商品类型。
商品照片不合格，返回 PRODUCT_PHOTO_INVALID：
- 看不到商品或包装。
- 只有快递面单、物流单、票据、聊天记录、网页、截图。
- 只有发票、收据、订单页。
- 商品类型无法辨认。
如果图片商品品类与订单商品类型明显不一致，返回 PRODUCT_TYPE_MISMATCH。

五、家电审核规则
家电范围：
冰箱、冰柜、冷吧、洗衣机、烘干机、电视、空调、热水器、燃气热水器、电热水器、壁挂炉等。
家电拆封/安装照片合格：
- 拆封/安装照片组自身能看到商品本体，并且能看到包装、外箱、拆封关系、安装/到家/使用场景中的任一证据。
- 有包装时，不能只有纸箱、箱体内部、泡沫、塑料袋、标签或说明书；必须同时能看到商品本体。
- 无包装时，必须能看到商品本体已经到家、安装、摆放或处于使用场景。
- 不得用商品照片中的商品本体去补拆封/安装照片缺失的商品本体。
家电拆封/安装照片不合格，返回 UNBOXING_PHOTO_INVALID：
- 看不到拆封、安装、交付或商品本体。
- 只有包装箱、纸箱内部、泡沫、塑料袋、标签、说明书或无关环境，无法看到商品本体。
- 只有无关环境照片。
家电激活/SN证据合格：
- 机身 SN 照片。
- 外包装 SN 照片。
- 包装标签、铭牌、条码下方 SN 信息清晰可见。
- 家电允许只有外包装 SN 作为激活/SN证据。
- 家电 SN 可忽略大小写、点号、横杠、空格等符号差异。
- 二维码/条码存在但没有可读 SN 文本时，不能单独作为有效 SN 证据。
家电激活/SN证据不合格，返回 ACTIVATION_PHOTO_INVALID：
- 激活/SN照片中没有机身、外包装、铭牌或 SN 信息。
- 只有商品外观，但没有任何序列号、条码文字、铭牌证据。
- SN 区域严重模糊、遮挡、涂抹或马赛克，无法形成证据链。

六、普通 3C 审核规则
普通 3C 范围：
手机、平板、智能手表/手环、智能眼镜等。不含电脑。
硬排除：
只要商品为电脑、笔记本、台式机、PC、Laptop、Computer，不得适用普通 3C 规则，必须进入电脑规则。
普通 3C 拆封照片合格：
- 商品本体和外包装合照。
- 商品已拆封，能看到设备本体和包装。
- 商品照片已体现包装，拆封照片能清楚体现设备本体。
普通 3C 拆封照片不合格，返回 UNBOXING_PHOTO_INVALID：
- 看不到设备本体。
- 看不到包装且无法形成拆封证据链。
- 只有截图、网页图、宣传图。
普通 3C 激活/SN/IMEI证据合格：
- 激活照片或设备信息页显示 SN。
- 激活照片或设备信息页显示 IMEI1 / IMEI2。
- 手机/普通 3C 屏幕未显示 SN，但有 IMEI1 / IMEI2，且同组盒子/包装 SN 可辅助验证，可以作为图片合规通过候选。
- 若序列号区域有轻微光影遮挡，但盒子或包装信息能辅助验证，可以作为图片合规通过候选。
普通 3C 激活/SN/IMEI证据不合格，返回 ACTIVATION_PHOTO_INVALID：
- 只有亮屏、锁屏、桌面、开机画面，没有 SN/IMEI/设备身份信息，也没有盒子/包装 SN 辅助证据。
- 只有包装外观，没有 SN/IMEI/条码文字。
- 二维码/条码存在但没有可读 SN/IMEI 文本，且无法确认设备身份信息。
- 无法看到任何可用于证明设备身份的 SN/IMEI/序列号证据。
如果激活图是相册图、截图、另一台设备屏幕里的图片，返回 IMAGE_STRONG_RISK。

七、电脑审核规则
电脑范围：
笔记本电脑、台式电脑、PC、Laptop、Computer。
笔记本电脑合格规则：
- 激活照片必须亮屏显示设备 SN。
- 同一张激活照片中必须同时出现亮屏设备 SN 和包装 SN 合照。
- 不能只用包装 SN、机身 SN 或单独亮屏照片替代。
笔记本电脑不合格，返回 ACTIVATION_PHOTO_INVALID：
- 只有包装 SN，没有亮屏设备 SN。
- 只有亮屏，没有设备 SN。
- 只有机身 SN，没有亮屏设备 SN 与包装 SN 合照。
- 亮屏设备 SN 和包装 SN 不在同一张激活照片中。
- 只有截图、相册图、另一台设备屏幕里的图片。
台式电脑合格规则：
- 台式电脑机身 SN 照片与外包装 SN 合照。
- 或主机本体 SN/铭牌与包装 SN 能形成同一商品证据链。
台式电脑不合格，返回 ACTIVATION_PHOTO_INVALID：
- 只有包装 SN，没有主机本体或机身/铭牌 SN。
- 只有主机外观，没有 SN/铭牌/包装 SN。
- 无法形成主机本体 SN 与包装 SN 的同一商品证据链。
电脑规则必须严格，不得套用家电“只有外包装 SN 可通过”的规则，也不得套用普通 3C“IMEI + 包装 SN 可通过”的规则。

八、兜底规则
- 图片模糊到无法判断商品、包装、SN/IMEI 内容时，转人工，原因码 MODEL_UNCERTAIN。
- 无法确定商品属于家电、普通 3C、电脑时，effective_category="unknown"，转人工，原因码 MODEL_UNCERTAIN。
- 多个商品、多个包装、多个 SN 混在一起且无法确认同一证据链时，转人工；如果有明显混拍风险，使用 IMAGE_STRONG_RISK，否则使用 MODEL_UNCERTAIN。
- 图片证据不足但无强风险时，使用具体无效码，不要泛化为 IMAGE_STRONG_RISK。
- 多个规则同时命中时，manual_reason_codes 输出所有命中的原因码，并按优先级排序。
- manual_reason 用一句中文说明最高优先级原因。

九、边界示例
示例 1：家电订单，商品图和拆封图合格，激活/SN图只有清晰外包装 SN。输出倾向：manual_required=false, activation_photo_ok=true, activation_evidence_type=PACKAGE_SN_ONLY。
示例 2：普通 3C 手机，屏幕显示 IMEI1/IMEI2，但未显示 SN；同组盒子/包装 SN 清晰可见。输出倾向：manual_required=false, activation_photo_ok=true, activation_evidence_type=SCREEN_ACTIVE_WITH_SN。
示例 3：笔记本电脑只有包装 SN，或只有亮屏但未显示设备 SN。输出倾向：manual_required=true, manual_reason_codes=["ACTIVATION_PHOTO_INVALID"], activation_photo_ok=false。
示例 4：发票编号旁边有橘色感叹号。输出倾向：manual_required=true, manual_reason_codes=["INVOICE_ORANGE_WARNING"], invoice_orange_warning=true。
示例 5：商品图、拆封图、激活图完全相同。输出倾向：manual_required=true, manual_reason_codes=["DUPLICATE_IMAGE_EVIDENCE"], duplicate_image_evidence=true。
示例 6：订单为笔记本电脑，屏幕无设备 SN，但包装 SN 清晰。输出倾向：manual_required=true, manual_reason_codes=["ACTIVATION_PHOTO_INVALID"]，不得按普通 3C 或家电规则通过。

十、输出 JSON
只输出 JSON，不要输出 Markdown，不要解释 JSON 之外的内容。
字段类型要求：
- manual_required：boolean。
- manual_reason_codes：array[string]，无问题时必须为 []。
- manual_reason：string，无问题时为空字符串。
- effective_category："home_appliance" | "ordinary_3c" | "computer" | "unknown"。
- product_type_match：boolean 或 null。
- product_photo_ok：boolean 或 null。
- unboxing_photo_ok：boolean 或 null。
- activation_photo_ok：boolean 或 null。
- activation_evidence_type：只能使用 SCREEN_SN, SCREEN_ACTIVE_WITH_SN, PACKAGE_SN_ONLY, SCREEN_ON_NO_SN, NO_SCREEN_ON, COLLAGE_OR_EDIT_RISK, UNCLEAR。
- image_risk：boolean。
- duplicate_image_evidence：boolean。
- invoice_orange_warning：boolean。
- sn_candidates：array，每项包含 image_id, source(SCREEN/PACKAGE_LABEL/BARCODE_TEXT), raw_text, normalized_text, readable, matches_system_sn。matches_system_sn 表示是否支持前置已验证的 observed_sn，不表示重新判断订单 SN。
- activation_screen：object，包含 screen_on, screen_content_type(ABOUT_DEVICE_SN/DEVICE_INFO_WITH_ID/ACTIVATION_SUCCESS/HOME_OR_LOCK_SCREEN/PHOTO_VIEWER_OR_SCREENSHOT/UNCLEAR/NONE), screen_sn_visible, screen_sn_text, screen_identity_text。
- photo_integrity：object，包含 collage_or_edit_risk, screen_shows_photo_or_screenshot, evidence_chain_trustworthy, risk_reason。
- tamper_checks：object，包含 font_consistency_ok, perspective_consistency_ok, noise_compression_consistency_ok, edge_blending_ok, screen_reflection_consistency_ok, erasure_or_overwrite_risk, local_background_break_risk。
- same_photo_or_same_group_chain：boolean。
- failed_items：array[string]。
- evidence_summary：string。
- confidence：number，0 到 1 之间。

字段联动规则：
- manual_required=false 时，manual_reason_codes 必须为 []，manual_reason 必须为空字符串。
- manual_required=true 时，manual_reason_codes 必须非空。
- invoice_orange_warning=true 时，manual_reason_codes 必须包含 INVOICE_ORANGE_WARNING。
- image_risk=true 时，manual_reason_codes 必须包含 IMAGE_STRONG_RISK。
- duplicate_image_evidence=true 时，manual_reason_codes 必须包含 DUPLICATE_IMAGE_EVIDENCE。
- product_type_match=false 时，manual_reason_codes 必须包含 PRODUCT_TYPE_MISMATCH。
- product_photo_ok=false 时，manual_reason_codes 必须包含 PRODUCT_PHOTO_INVALID。
- unboxing_photo_ok=false 时，manual_reason_codes 必须包含 UNBOXING_PHOTO_INVALID。
- activation_photo_ok=false 时，manual_reason_codes 必须包含 ACTIVATION_PHOTO_INVALID。

原因码只能使用：
INVOICE_ORANGE_WARNING, IMAGE_STRONG_RISK, DUPLICATE_IMAGE_EVIDENCE, PRODUCT_TYPE_MISMATCH, PRODUCT_PHOTO_INVALID, UNBOXING_PHOTO_INVALID, ACTIVATION_PHOTO_INVALID, MODEL_UNCERTAIN。
原因码优先级：
INVOICE_ORANGE_WARNING > IMAGE_STRONG_RISK > DUPLICATE_IMAGE_EVIDENCE > PRODUCT_TYPE_MISMATCH > PRODUCT_PHOTO_INVALID > UNBOXING_PHOTO_INVALID > ACTIVATION_PHOTO_INVALID > MODEL_UNCERTAIN。
"""

LEGACY_COMPLIANCE_PROMPT = COMPLIANCE_PROMPT

COMPLIANCE_REASON_CODES = {
    "INVOICE_ORANGE_WARNING",
    "IMAGE_STRONG_RISK",
    "DUPLICATE_IMAGE_EVIDENCE",
    "PRODUCT_TYPE_MISMATCH",
    "PRODUCT_PHOTO_INVALID",
    "UNBOXING_PHOTO_INVALID",
    "ACTIVATION_PHOTO_INVALID",
    "MODEL_UNCERTAIN",
}

COMPLIANCE_OUTPUT_SCHEMA = """只输出 JSON，不输出解释。
{
  "manual_required": boolean,
  "manual_reason_codes": [],
  "manual_reason": "",
  "effective_category": "home_appliance | ordinary_3c | computer | unknown",
  "product_type_match": "match | mismatch | unknown",
  "product_photo_ok": boolean,
  "unboxing_photo_ok": boolean,
  "package_visible": boolean,
  "activation_photo_ok": boolean,
  "activation_evidence_type": "SCREEN_SN | SCREEN_ACTIVE_WITH_SN | PACKAGE_SN_ONLY | SCREEN_ON_NO_SN | NO_SCREEN_ON | UNCLEAR",
  "activation_screen": {
    "screen_on": boolean,
    "screen_content_type": "ABOUT_DEVICE_SN | DEVICE_INFO_WITH_ID | ACTIVATION_SUCCESS | PAIRING_OR_SETUP | HOME_OR_LOCK_SCREEN | UNCLEAR | NONE",
    "screen_sn_visible": boolean,
    "screen_sn_text": "",
    "screen_identity_text": ""
  },
  "image_risk": boolean,
  "duplicate_image_evidence": boolean,
  "invoice_orange_warning": boolean,
  "failed_items": [],
  "evidence_summary": "",
  "confidence": number
}

原因码只能使用：
INVOICE_ORANGE_WARNING, IMAGE_STRONG_RISK, DUPLICATE_IMAGE_EVIDENCE, PRODUCT_TYPE_MISMATCH, PRODUCT_PHOTO_INVALID, UNBOXING_PHOTO_INVALID, ACTIVATION_PHOTO_INVALID, MODEL_UNCERTAIN。"""

HOME_APPLIANCE_OUTPUT_SCHEMA = COMPLIANCE_OUTPUT_SCHEMA.replace(
    '  "package_visible": boolean,\n',
    '  "package_visible": boolean,\n'
    '  "whole_product_visible": boolean,\n'
    '  "home_or_installation_scene_visible": boolean,\n',
)

COMPLIANCE_COMMON_PROMPT = """你是国补订单图片合规审核员。只输出 JSON。

前置条件：
1. SN 一致性已由系统完成，本阶段不重新识别或比对 SN。
2. 本阶段只判断图片是否合规、是否实拍、是否存在强风险。
3. 每张照片都要检查是否为真实拍摄。
4. 截图、相册图、电子屏二次翻拍、拼图、P图、SN/IMEI区域篡改，转人工 IMAGE_STRONG_RISK。
5. 发票编号旁有橘色感叹号，转人工 INVOICE_ORANGE_WARNING。
6. 只有至少三张照片为完全相同的原始文件、画面没有任何差异，才返回 DUPLICATE_IMAGE_EVIDENCE。只有两张重复不得拦截；角度、位置、裁切、透视、背景、光线或屏幕内容任一不同，都不属于完全重复。
7. 分类由系统完成，不要改判品类，不要输出自造原因码。
8. 即使 SN 一致性已由前置阶段判断，也必须客观填写 activation_screen；本阶段不要重复提取包装或机身 SN 候选。"""

HOME_APPLIANCE_COMPLIANCE_PROMPT = COMPLIANCE_COMMON_PROMPT + """

当前品类：家电。
适用：电冰箱、电视机、空调、热水器、洗衣机。

审核规则：
1. 商品照片应能看到商品本体或外包装。
2. 拆封/安装照片组自身必须看到商品本体；不得用商品照片中的商品本体去补拆封/安装照片缺失的商品本体。
3. 有包装时，拆封/安装照片组必须同时看到商品本体和包装/外箱/拆封关系；只有纸箱、箱体内部、泡沫、塑料袋、标签或说明书，返回 UNBOXING_PHOTO_INVALID。
4. 无包装时，必须能看到商品本体已经到家、安装、摆放或处于家庭/店铺/使用场景中，才可判定拆封/安装照片合格。
5. package_visible 只在拆封/安装照片组中出现可识别外箱或包装结构时返回 true；whole_product_visible 只根据拆封/安装照片组判断，不能根据商品照片判断；无包装但已安装到家/店/使用场景时，package_visible=false，同时 whole_product_visible=true 且 home_or_installation_scene_visible=true。
6. SN 已由第一阶段核验后，家电不要求亮屏或开机证据；激活/SN照片只检查是否真实拍摄，以及是否存在翻拍、截图、拼图、P图、篡改风险。
7. 仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。

""" + HOME_APPLIANCE_OUTPUT_SCHEMA

ORDINARY_3C_COMPLIANCE_PROMPT = COMPLIANCE_COMMON_PROMPT + """

当前品类：普通3C。
适用：手机、平板、智能手表手环、智能眼镜。

审核规则：
1. 商品照片应能看到设备本体或外包装。
2. 拆封照片应能看到设备本体，并能与包装形成同一商品证据链。
3. 激活照片应为设备真实亮屏页面，并显示 SN、序列号、IMEI1、IMEI2 等身份信息之一。
4. 只有亮屏、锁屏、桌面、开机画面，但没有身份信息，返回 ACTIVATION_PHOTO_INVALID。
5. 仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。
6. 禁止把 3C 产品正常亮屏激活页、设置页、关于本机页误判为二次翻拍。
7. 智能手表/手环的配对页、设备名称、开机标志、二维码不属于 SN/IMEI/序列号身份信息；屏幕没有身份信息时，即使包装 SN 清晰也返回 ACTIVATION_PHOTO_INVALID。
8. 必须在 activation_screen 中区分 PAIRING_OR_SETUP 与 ABOUT_DEVICE_SN/DEVICE_INFO_WITH_ID。
9. screen_sn_visible 和 screen_sn_text 只用于明确标注为 SN、S/N、Serial Number、序列号的内容；屏幕只有 IMEI 时，screen_sn_visible=false、screen_sn_text=""，IMEI 写入 screen_identity_text。

""" + COMPLIANCE_OUTPUT_SCHEMA

COMPUTER_COMPLIANCE_PROMPT = COMPLIANCE_COMMON_PROMPT + """

当前品类：电脑。
适用：电脑、笔记本、台式机、PC、Laptop、Computer。

审核规则：
1. 商品照片应能看到电脑本体或外包装。
2. 拆封照片应能看到电脑本体，并能与包装形成同一商品证据链。
3. 笔记本激活照片必须同时出现亮屏设备 SN 和包装 SN 合照。
4. 台式机必须出现主机机身 SN/铭牌与外包装 SN 证据链。
5. 电脑不得套用普通3C或家电规则。
6. 电脑正常亮屏 BIOS、系统信息页、设备信息页不属于二次翻拍。
7. 仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。

""" + COMPLIANCE_OUTPUT_SCHEMA

UNKNOWN_COMPLIANCE_PROMPT = COMPLIANCE_COMMON_PROMPT + """

当前品类：unknown。
无法确定品类时必须转人工 MODEL_UNCERTAIN。

""" + COMPLIANCE_OUTPUT_SCHEMA


PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM = r'''

【逐图真实性观察（只观察，不裁决）】
对本次输入的每个 image_id 分别输出一条观察，必须一图一条、完整覆盖且 image_id 不重复。不得跨图拼接证据；每个证据只能属于当前 image_id；不得输出最终真实性裁决，也不得用本段改变其他合规字段。
审图是否二次翻拍/拍屏/拍纸照，只记录可见证据。禁用水印、定位、时间、文件/路径/尺寸/EXIF、品牌记忆；水印正常，不作为风险证据。商品屏、实物屏、条码、激活页、普通反光、模糊、印刷网点、局部摩尔纹本身不是证据。
在原 JSON 对象中追加且仅追加字段：
"photo_authenticity_by_image": [{
  "image_id": "与输入完全一致",
  "edges": {"top": "", "right": "", "bottom": "", "left": ""},
  "screen_owner": "",
  "strong_evidence": [{"code": "", "regions": []}],
  "weak_evidence": [{"code": "", "regions": []}],
  "reason": "不超过160字的客观观察"
}]
枚举必须严格使用：
- edges: scene_continues | carrier_boundary | abrupt_cutoff | not_visible | uncertain。carrier_boundary只用于外部显示屏/照片/纸张等二次载体边界；不用于商品自身电脑/笔记本/显示器的屏幕边框、机身边框或品牌Logo底边。
- screen_owner: product_screen | external_screen | none | uncertain。只有能看到真实设备边框/机身关系，且屏幕内容属于该设备自身设置页、身份页或激活页时，才使用product_screen；相册、查看器、截图展示或归属不清时使用external_screen或uncertain。商品是电脑/笔记本/显示器时，商品自身屏幕内的系统UI、任务栏、鼠标光标正常，不作为风险；商品自身屏幕内UI不记PHOTO_VIEWER_UI。画面内出现鼠标箭头、桌面光标、电脑窗口控件、系统导航栏等不属于商品自身屏幕，压在包装/背景/照片载体/未知外部画面上的系统UI时，按外部屏画面/外部载体记录。
- regions: product_body | product_screen | package | hand | background | image_edge | unknown
strong仅在明确看到时使用：EXTERNAL_PHOTO_CARRIER=外部手机/显示器等照片载体；PHOTO_VIEWER_UI=外部屏幕的相册/查看器界面；PRINTED_PHOTO_CARRIER=纸张、相纸或印刷载体；NESTED_IMAGE_BOUNDARY=画面内另一张图片的完整边界；CROSS_OBJECT_MOIRE=同一图片的同类摩尔纹跨至少2个不同的非商品屏物理区域、跨product_screen与任一非屏物理区、或跨全图，regions列出区域。
weak按含义使用：EDGE_CUTOFF=边缘视觉突然缺块、异常截断或不连续，不限于笔直黑边；OUTER_PLANE_OPTICS=疑似外层平面的反光/光学痕迹；PLANAR_APPEARANCE=整体缺乏景深、疑似平面二次成像；LOCAL_MOIRE=单一区域摩尔纹；UI_CANDIDATE=疑似但不明确的外部界面。局部摩尔纹只记LOCAL_MOIRE。真实商品自身屏幕内、仅限product_screen区域的正常拍屏摩尔纹仍可如实记录LOCAL_MOIRE，程序会在无其他证据且四边连续时豁免；摩尔纹延伸到机身、包装、手部或背景时必须列出全部区域，不得错误标成仅product_screen。
普通反射、模糊、滤镜、常规裁切、局部纹理或单一弱证据不得记为strong。
无证据时对应 evidence 数组必须为 []；不要虚构证据，不要增加字段。
'''

PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE = r'''

【图片真实性裁决接管（仅 enforce 模式）】
本段替代通用强风险中与二次翻拍/拍屏/拍纸照真实性相关的旧裁决规则；不得因真实性观察单独设置顶层 IMAGE_STRONG_RISK、image_risk=true 或 manual_required=true。截图、相册/查看器图、网图、电子屏二次翻拍、AI生成图、明显非真实拍摄图，只按 photo_authenticity_by_image 输出逐图观察，由本地图片真实性规则统一裁决。SN/IMEI篡改、多台设备或多个包装混拍、证据链明显不一致，仍按原业务强风险规则输出 IMAGE_STRONG_RISK。
'''

COMPLIANCE_PROMPT = ORDINARY_3C_COMPLIANCE_PROMPT


FAST_AUDIT_PROMPT = """已废弃：旧 fast 一次性视觉审核提示词不得再作为业务规则来源。
实际审核必须使用分阶段链路：
1. SN 阶段使用 DIRECT_SN_PROMPT 或 SN_PROMPT，只负责读取图片 SN，不负责图片合规。
2. 图片合规阶段使用 COMPLIANCE_PROMPT，按家电、普通 3C、电脑分开审核。
3. 不允许再使用旧规则“用户上传图片默认按实拍处理，不要因为疑似网图而转人工”。
4. 不允许再用一次模型请求同时完成 SN 比对、图片合规、原因解释和自动通过判断。
保留该常量仅兼容旧导入，后续不得扩展其业务规则。
"""


CSV_COLUMNS = [
    ("id", "ID"),
    ("manual_flag", "是否转人工"),
    ("source_flow_status", "原始流程状态"),
    ("manual_reason_code", "manual_reason_code"),
    ("manual_reason_cn", "manual_reason_cn"),
    ("manual_reason", "manual_reason"),
    ("business_pass", "business_pass"),
    ("elapsed_sec", "elapsed_sec"),
    ("strategy", "瀹℃牳绛栫暐"),
    ("model_calls", "妯″瀷璋冪敤娆℃暟"),
    ("total_tokens", "鎬籺oken"),
    ("precheck_elapsed_sec", "precheck_elapsed_sec"),
    ("sn_elapsed_sec", "sn_elapsed_sec"),
    ("compliance_elapsed_sec", "compliance_elapsed_sec"),
    ("product_type", "鍟嗗搧绫诲瀷"),
    ("source_examine_status", "source_examine_status"),
    ("source_settle_status", "source_settle_status"),
    ("system_sn", "system_sn"),
    ("observed_sn", "璇嗗埆SN"),
    ("sn_match", "sn_match"),
    ("product_type_match", "product_type_match"),
    ("address_ok", "鍦板潃鍚堟牸"),
    ("product_photo_ok", "product_photo_ok"),
    ("unboxing_photo_ok", "unboxing_photo_ok"),
    ("activation_photo_ok", "婵€娲荤収鍚堟牸"),
    ("activation_evidence_type", "activation_evidence_type"),
    ("image_risk", "image_risk"),
    ("confidence", "confidence"),
    ("photo_authenticity_mode", "图片真实性模式"),
    ("photo_authenticity_local_tree_enabled", "图片真实性本地树是否启用"),
    ("photo_authenticity_local_tree_artifact_sha256", "图片真实性本地树artifact SHA256"),
    ("photo_authenticity_local_tree_hit_count", "图片真实性本地树命中图片数"),
    ("photo_authenticity_local_tree_unavailable_count", "图片真实性本地树不可用图片数"),
    ("photo_authenticity_would_manual", "图片真实性是否建议转人工"),
    ("photo_authenticity_strong_count", "图片真实性强证据图片数"),
    ("photo_authenticity_manual_count", "图片真实性人工复核图片数"),
    ("photo_authenticity_fft_count", "图片真实性FFT命中图片数"),
    ("photo_authenticity_service_failure", "图片真实性服务是否异常"),
    ("photo_authenticity_fallback_calls", "图片真实性兜底调用数"),
    ("photo_authenticity_elapsed_sec", "图片真实性后处理耗时秒（兼容列）"),
    ("photo_authenticity_tokens", "图片真实性后处理token（兼容列）"),
    ("photo_authenticity_image_results", "图片真实性逐图结果JSON"),
    ("photo_authenticity_error", "图片真实性异常"),
    ("merged_compliance_total_tokens", "合并后整次图片合规token"),
    ("merged_compliance_total_elapsed_sec", "合并后整次图片合规耗时秒"),
    ("photo_authenticity_postprocess_tokens", "真实性后处理token"),
    ("photo_authenticity_postprocess_elapsed_sec", "真实性后处理耗时秒"),
    ("baseline_compliance_tokens", "历史同类合规基线token"),
    ("baseline_compliance_elapsed_sec", "历史同类合规基线耗时秒"),
    ("photo_authenticity_incremental_tokens", "真实性可计算增量token"),
    ("photo_authenticity_incremental_elapsed_sec", "真实性可计算增量耗时秒"),
    ("photo_authenticity_incremental_available", "真实性增量是否可计算"),
    ("sn_char_review_mode", "SN相似字符复核模式"),
    ("sn_barcode_mode", "SN条码二次确认模式"),
    ("sn_label_auth_review_mode", "SN标签真实性插件模式"),
    ("digital_activation_evidence_mode", "普通3C激活证据插件模式"),
]


PHOTO_AUTHENTICITY_REPORT_DEFAULTS = {
    "photo_authenticity_mode": "off",
    "photo_authenticity_local_tree_enabled": "off",
    "photo_authenticity_local_tree_artifact_sha256": "",
    "photo_authenticity_local_tree_hit_count": 0,
    "photo_authenticity_local_tree_unavailable_count": 0,
    "photo_authenticity_would_manual": False,
    "photo_authenticity_strong_count": 0,
    "photo_authenticity_manual_count": 0,
    "photo_authenticity_fft_count": 0,
    "photo_authenticity_service_failure": False,
    "photo_authenticity_fallback_calls": 0,
    "photo_authenticity_elapsed_sec": 0.0,
    "photo_authenticity_tokens": 0,
    "photo_authenticity_image_results": "",
    "photo_authenticity_error": "",
    "merged_compliance_total_tokens": "",
    "merged_compliance_total_elapsed_sec": "",
    "photo_authenticity_postprocess_tokens": 0,
    "photo_authenticity_postprocess_elapsed_sec": 0.0,
    "baseline_compliance_tokens": "",
    "baseline_compliance_elapsed_sec": "",
    "photo_authenticity_incremental_tokens": "",
    "photo_authenticity_incremental_elapsed_sec": "",
    "photo_authenticity_incremental_available": False,
}


def prepare_photo_authenticity_report_fields(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in PHOTO_AUTHENTICITY_REPORT_DEFAULTS.items():
        row.setdefault(key, value)
    image_results = row.get("photo_authenticity_image_results")
    if isinstance(image_results, dict):
        values = tuple(image_results.values())
        row["photo_authenticity_strong_count"] = sum(
            item.get("result") == "high_risk_non_real" for item in values
        )
        row["photo_authenticity_manual_count"] = sum(
            item.get("result") == "manual_review" for item in values
        )
        row["photo_authenticity_fft_count"] = sum(bool(item.get("rescued_by_fft")) for item in values)
        row["photo_authenticity_local_tree_hit_count"] = sum(
            item.get("rule") == "LOCAL_TREE" and item.get("result") == "high_risk_non_real"
            for item in values
        )
        row["photo_authenticity_local_tree_unavailable_count"] = sum(
            item.get("status") == "local_tree_unavailable" for item in values
        )
        row["photo_authenticity_image_results"] = json.dumps(
            image_results, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    row["photo_authenticity_tokens"] = int(row.get("photo_authenticity_postprocess_tokens") or 0)
    row["photo_authenticity_elapsed_sec"] = float(row.get("photo_authenticity_postprocess_elapsed_sec") or 0)
    has_baseline = row.get("baseline_compliance_tokens") != "" and row.get("baseline_compliance_elapsed_sec") != ""
    has_merged = row.get("merged_compliance_total_tokens") != "" and row.get("merged_compliance_total_elapsed_sec") != ""
    if has_baseline and has_merged:
        row["photo_authenticity_incremental_tokens"] = (
            int(row["merged_compliance_total_tokens"]) - int(row["baseline_compliance_tokens"])
            + int(row["photo_authenticity_postprocess_tokens"] or 0)
        )
        row["photo_authenticity_incremental_elapsed_sec"] = round(
            float(row["merged_compliance_total_elapsed_sec"]) - float(row["baseline_compliance_elapsed_sec"])
            + float(row["photo_authenticity_postprocess_elapsed_sec"] or 0), 4,
        )
        row["photo_authenticity_incremental_available"] = True
    return row


def apply_optional_compliance_baseline(row: dict[str, Any], env: Mapping[str, str]) -> None:
    path_text = str(env.get("PHOTO_AUTHENTICITY_BASELINE_PATH") or "").strip()
    if not path_text:
        return
    mapping = json.loads(Path(path_text).read_text(encoding="utf-8-sig"))
    item = mapping.get(str(row.get("id") or "")) if isinstance(mapping, dict) else None
    if item is None:
        return
    if not isinstance(item, dict) or "tokens" not in item or "elapsed_sec" not in item:
        raise ValueError("photo authenticity baseline entry requires tokens and elapsed_sec")
    row["baseline_compliance_tokens"] = int(item["tokens"])
    row["baseline_compliance_elapsed_sec"] = float(item["elapsed_sec"])


def finalize_photo_authenticity_report_fields(
    row: dict[str, Any], config: PhotoAuthenticityConfig,
) -> dict[str, Any]:
    """Record the configured mode even when the authenticity stage was skipped."""
    row["photo_authenticity_mode"] = config.mode
    row["photo_authenticity_local_tree_enabled"] = (
        "on" if config.mode != "off" and config.local_tree_enabled else "off"
    )
    row["photo_authenticity_local_tree_artifact_sha256"] = (
        EXPECTED_LOCAL_TREE_SHA256 if config.mode != "off" and config.local_tree_enabled else ""
    )
    row["sn_label_auth_review_mode"] = (
        "on" if config.mode != "off" and config.sn_label_auth_review_enabled else "off"
    )
    apply_optional_compliance_baseline(row, os.environ)
    return prepare_photo_authenticity_report_fields(row)


def verify_photo_authenticity_local_tree_artifact(config: PhotoAuthenticityConfig) -> None:
    if config.mode == "off" or not config.local_tree_enabled:
        return
    try:
        LocalTreeNonRealRescue.load(config.local_tree_artifact_path)
    except Exception as exc:
        raise RuntimeError(
            "photo authenticity local tree artifact unavailable: "
            f"{config.local_tree_artifact_path}: {type(exc).__name__}: {exc}"
        ) from exc


def summarize_photo_authenticity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mode_counts: dict[str, int] = {}
    for row in rows:
        mode = str(row.get("photo_authenticity_mode") or "off")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    baseline_orders = sum(bool(row.get("photo_authenticity_incremental_available")) for row in rows)
    return {
        "mode_counts": mode_counts,
        "would_manual_orders": sum(bool(row.get("photo_authenticity_would_manual")) for row in rows),
        "strong_images": sum(int(row.get("photo_authenticity_strong_count") or 0) for row in rows),
        "manual_images": sum(int(row.get("photo_authenticity_manual_count") or 0) for row in rows),
        "fft_images": sum(int(row.get("photo_authenticity_fft_count") or 0) for row in rows),
        "local_tree_hit_images": sum(int(row.get("photo_authenticity_local_tree_hit_count") or 0) for row in rows),
        "local_tree_unavailable_images": sum(int(row.get("photo_authenticity_local_tree_unavailable_count") or 0) for row in rows),
        "failure_orders": sum(bool(row.get("photo_authenticity_service_failure")) for row in rows),
        "fallback_calls": sum(int(row.get("photo_authenticity_fallback_calls") or 0) for row in rows),
        "latency_sec": round(sum(float(row.get("photo_authenticity_elapsed_sec") or 0) for row in rows), 2),
        "tokens": sum(int(row.get("photo_authenticity_tokens") or 0) for row in rows),
        "merged_compliance_total_tokens": sum(int(row.get("merged_compliance_total_tokens") or 0) for row in rows),
        "merged_compliance_total_elapsed_sec": round(sum(float(row.get("merged_compliance_total_elapsed_sec") or 0) for row in rows), 2),
        "postprocess_tokens": sum(int(row.get("photo_authenticity_postprocess_tokens") or 0) for row in rows),
        "postprocess_elapsed_sec": round(sum(float(row.get("photo_authenticity_postprocess_elapsed_sec") or 0) for row in rows), 2),
        "available_incremental_tokens": sum(int(row.get("photo_authenticity_incremental_tokens") or 0) for row in rows if row.get("photo_authenticity_incremental_available")),
        "available_incremental_elapsed_sec": round(sum(float(row.get("photo_authenticity_incremental_elapsed_sec") or 0) for row in rows if row.get("photo_authenticity_incremental_available")), 2),
        "baseline_coverage": {
            "orders_with_baseline": baseline_orders, "total_orders": len(rows),
            "rate": round(baseline_orders / len(rows), 4) if rows else 0.0,
        },
    }

MODEL_TIMEOUT_SEC = 60
MODEL_RETRY_TIMEOUT_SEC = 15
ORDER_TIMEOUT_SEC = 60
MIN_STAGE_TIMEOUT_SEC = 1
MODEL_REQUEST_BUFFER_SEC = 3
MODEL_CONNECT_TIMEOUT_SEC = 5
MODEL_CONNECT_RETRIES = 1
AUTO_PASS_MIN_CONFIDENCE = 0.90

_model_request_lock = threading.Lock()
_last_model_request_at: float | None = None


class ModelConnectionError(RuntimeError):
    pass


class OrderBudgetExceeded(TimeoutError):
    pass


REASON_CODE_CN = {
    "INVOICE_ORANGE_WARNING": "发票编号旁出现橘色感叹号，需人工复核",
    "SN_MISMATCH": "系统SN与照片中SN不一致",
    "SN_NOT_FOUND": "激活/SN照片未拍到完整可用SN",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "PRODUCT_TYPE_MISMATCH": "照片商品品类与页面商品类型不一致",
    "UNBOXING_PHOTO_INVALID": "拆封/安装照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活/SN证据链不足",
    "IMAGE_STRONG_RISK": "图片疑似拼接或处理，需人工复核",
    "DUPLICATE_IMAGE_EVIDENCE": "不同分组图片重复，证据不足",
    "MODEL_UNCERTAIN": "模型识别不稳定或超时，需人工复核",
    "ADDRESS_TOO_COARSE": "家电收货地址不够精确",
    "CHANNEL_ORDER_NO_MISSING": "渠道订单号缺失",
    "PRODUCT_TYPE_MISSING": "商品类型缺失",
    "SYSTEM_SN_MISSING": "系统SN缺失",
    "IMAGE_MISSING": "审核图片缺失",
    "DUPLICATE_IMAGE": "图片重复，证据不足",
}


def reason_code_to_chinese(code: str) -> str:
    return REASON_CODE_CN.get(str(code or "").strip(), "需人工复核")


def build_chinese_reason(reason_codes: list[str], reason_text: str) -> str:
    if not reason_codes:
        return ""
    primary = reason_codes[0]
    readable = reason_code_to_chinese(primary)
    if reason_text:
        return f"{readable}：{reason_text}"
    return readable


def configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def normalize_sn(value: Any) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"\bS\s*/?\s*N\s*[:：]?", "", text)
    text = re.sub(r"\bSN\s*(?:码|CODE)?\s*[:：]?", "", text)
    text = re.sub(r"\bSERIAL\s*NO\.?\s*[:：]?", "", text)
    text = re.sub(r"序列号\s*[:：]?", "", text)
    return re.sub(r"[^0-9A-Z]", "", text)


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (0 if left_char == right_char else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def group_images_by_title(task: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for title, images in (task.get("image_groups") or {}).items():
        normalized_title = str(title or "").strip() or "unnamed_image"
        for image in images or []:
            item = dict(image)
            item["title"] = str(item.get("title") or normalized_title)
            groups[normalized_title].append(item)
    if groups:
        return dict(groups)

    for image in task.get("images") or []:
        title = str(image.get("title") or "").strip() or "unnamed_image"
        groups[title].append(image)
    return dict(groups)


def flatten_image_groups(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [image for images in groups.values() for image in images]


def ordered_task_images(task: dict[str, Any]) -> list[dict[str, Any]]:
    if task.get("images"):
        return [dict(image) for image in task.get("images") or []]
    return flatten_image_groups(group_images_by_title(task))


def assign_order_based_roles(task: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    images = ordered_task_images(task)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not images:
        return {}
    if len(images) == 1:
        image = dict(images[0])
        image["title"] = "SN鐮侀噰闆?/ 婵€娲荤収鐗?"
        groups["SN鐮侀噰闆?/ 婵€娲荤収鐗?"].append(image)
        return dict(groups)

    product = dict(images[0])
    product["title"] = "鍟嗗搧鐓х墖"
    groups["鍟嗗搧鐓х墖"].append(product)

    for image in images[1:-1]:
        item = dict(image)
        item["title"] = "鎷嗗皝鐓х墖"
        groups["鎷嗗皝鐓х墖"].append(item)

    activation = dict(images[-1])
    activation["title"] = "SN鐮侀噰闆?/ 婵€娲荤収鐗?"
    groups["SN鐮侀噰闆?/ 婵€娲荤収鐗?"].append(activation)
    return dict(groups)


def is_activation_title(title: str) -> bool:
    normalized = title.replace(" ", "")
    normalized_upper = normalized.upper()
    return any(
        keyword in normalized
        for keyword in (
            "SN码采集",
            "激活照片",
            "SN照片",
            "序列号照片",
            "SN鐮侀噰闆?",
            "婵€娲荤収鐗?",
            "SN鐓х墖",
            "搴忓垪鍙风収鐗?",
        )
    ) or any(keyword in normalized_upper for keyword in ("SNPHOTO", "ACTIVATIONPHOTO", "SERIALPHOTO"))


ADDRESS_PASS_KEYWORDS = ("商贸", "京东家电", "楼")
ADDRESS_LONG_ALNUM_SUFFIX_RE = re.compile(r"(?i)[a-z0-9]{11,}$")
ADDRESS_DETAIL_MARKERS = (
    "号",
    "栋",
    "幢",
    "单元",
    "室",
    "户",
    "门牌",
    "店铺",
    "门店",
    "商铺",
    "村",
    "组",
    "市场",
    "商场",
    "建材",
    "商城",
    "楼",
)


def is_address_precise_enough(address: str | None) -> bool:
    text = str(address or "").strip()
    if ADDRESS_LONG_ALNUM_SUFFIX_RE.search(text) and not any(
        marker in text for marker in ADDRESS_DETAIL_MARKERS
    ):
        return False
    if any(keyword in text for keyword in ADDRESS_PASS_KEYWORDS):
        return True
    if "村" in text:
        return True
    if re.search(r"\d\s*$", text):
        return True
    if re.search(r"(?:\d+|[零〇一二三四五六七八九十百两]+)\s*组$", text):
        return True
    if len(text) < 6:
        return False
    if re.search(r"\d+\s*[-\uff0d]\s*\d+(?:\s*[-\uff0d]\s*\d+)?", text):
        return True
    if re.search(
        r"(?:市场|商场|建材|商铺|店铺|门店|商城|mall).*[A-ZＡ-Ｚ]?\d*\s*[-\uff0d]\s*\d+",
        text,
        re.IGNORECASE,
    ):
        return True
    precise_markers = (
        "号",
        "栋",
        "幢",
        "单元",
        "室",
        "户",
        "门牌",
        "店铺",
        "门店",
        "商铺",
        "村",
        "B1-",
        "B-",
    )
    return any(marker in text for marker in precise_markers)


def _image_identity(image: dict[str, Any]) -> str:
    local_path = str(image.get("local_path") or "").strip()
    if local_path:
        path = Path(local_path)
        if path.is_file():
            digest = hashlib.sha256()
            try:
                with path.open("rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
                return "sha256:" + digest.hexdigest()
            except OSError:
                pass
    source = str(image.get("source_url") or image.get("url") or "").strip()
    return "url:" + source if source else ""


def has_duplicate_cross_group_images(groups: dict[str, list[dict[str, Any]]]) -> bool:
    return bool(exact_duplicate_image_groups(groups))


def all_image_groups_share_duplicate(groups: dict[str, list[dict[str, Any]]]) -> bool:
    return bool(exact_duplicate_image_groups(groups))


def exact_duplicate_image_groups(groups: dict[str, list[dict[str, Any]]]) -> list[list[str]]:
    buckets: dict[str, set[str]] = defaultdict(set)
    for title, images in groups.items():
        for index, image in enumerate(images):
            image_id = str(image.get("image_id") or "").strip() or f"{title}:{index}"
            identity = _image_identity(image)
            if identity:
                buckets[identity].add(image_id)
    duplicate_groups = [sorted(image_ids) for image_ids in buckets.values() if len(image_ids) >= 3]
    return sorted(duplicate_groups)


def category_name_from_fields(fields: dict[str, Any]) -> str:
    for key in ("category_name", "cate_code_name"):
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return re.sub(r"\[[^\]]+\]", "", str(fields.get("product_type") or "")).strip()


def _category_from_name(category_name: str) -> str:
    text = str(category_name or "").strip().lower()
    if any(
        keyword in text
        for keyword in (
            "家电",
            "电冰箱",
            "冰箱",
            "冰柜",
            "冷柜",
            "冷吧",
            "电视机",
            "电视",
            "空调",
            "热水器",
            "热水炉",
            "壁挂炉",
            "洗衣机",
            "烘干机",
            "refrigerator",
            "fridge",
            "freezer",
            "washing machine",
            "washer",
            "air conditioner",
            "television",
            "water heater",
        )
    ):
        return "home_appliance"
    if any(keyword in text for keyword in ("电脑", "笔记本", "台式机")) or re.search(
        r"(?<![a-z0-9])(?:pc|laptop|computer|notebook)(?![a-z0-9])",
        text,
    ):
        return "computer"
    if any(
        keyword in text
        for keyword in (
            "手机",
            "平板",
            "智能手表手环",
            "智能手表",
            "手表",
            "手环",
            "智能眼镜",
            "phone",
            "tablet",
            "watch",
        )
    ):
        return "ordinary_3c"
    return "unknown"


def effective_product_category(fields: dict[str, Any]) -> str:
    category_name = category_name_from_fields(fields)
    if category_name:
        return _category_from_name(category_name)
    text = " ".join(str(fields.get(key) or "") for key in ("goods_name", "product_name", "model"))
    fallback = _category_from_name(text)
    if fallback != "unknown":
        return fallback
    category = classify_audit_category("guobu", fields).category
    if category == "home_appliance":
        return "home_appliance"
    if category == "3c":
        return "ordinary_3c"
    return "unknown"


def compliance_prompt_for_category(
    category: str,
    *,
    product_type: str | None = None,
    include_photo_authenticity: bool = False,
    replace_legacy_authenticity_adjudication: bool = False,
    sn_label_auth_review_mode: str | None = None,
    photo_auth_edge_mapping_mode: str | None = None,
    digital_activation_evidence_mode: str | None = None,
) -> str:
    normalized = str(category or "").strip().lower()
    if normalized == "home_appliance":
        prompt = HOME_APPLIANCE_COMPLIANCE_PROMPT
    elif normalized == "computer":
        prompt = COMPUTER_COMPLIANCE_PROMPT
    elif normalized == "ordinary_3c" or normalized == "3c":
        prompt = ORDINARY_3C_COMPLIANCE_PROMPT
    else:
        prompt = UNKNOWN_COMPLIANCE_PROMPT
    if include_photo_authenticity:
        prompt = prompt + PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM
        if replace_legacy_authenticity_adjudication:
            prompt = prompt + PHOTO_AUTHENTICITY_REPLACEMENT_DIRECTIVE
    if include_photo_authenticity and resolve_sn_label_auth_review_mode(sn_label_auth_review_mode) == "on":
        prompt = prompt + "\n\n" + read_sn_label_auth_review_prompt()
    product_text = str(product_type or "").strip().upper()
    digital_product_supported = (
        any(marker in product_text for marker in ("手机", "平板", "手表", "手环"))
        or bool(re.search(r"\b(?:PHONE|SMARTPHONE|TABLET|WATCH|SMARTWATCH|WRISTBAND)\b", product_text))
    )
    if (
        normalized in {"ordinary_3c", "3c"}
        and digital_product_supported
        and resolve_digital_activation_evidence_mode(digital_activation_evidence_mode) == "on"
    ):
        legacy_rules = """3. 激活照片应为设备真实亮屏页面，并显示 SN、序列号、IMEI1、IMEI2 等身份信息之一。
4. 只有亮屏、锁屏、桌面、开机画面，但没有身份信息，返回 ACTIVATION_PHOTO_INVALID。
5. 仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。
6. 禁止把 3C 产品正常亮屏激活页、设置页、关于本机页误判为二次翻拍。
7. 智能手表/手环的配对页、设备名称、开机标志、二维码不属于 SN/IMEI/序列号身份信息；屏幕没有身份信息时，即使包装 SN 清晰也返回 ACTIVATION_PHOTO_INVALID。
8. 必须在 activation_screen 中区分 PAIRING_OR_SETUP 与 ABOUT_DEVICE_SN/DEVICE_INFO_WITH_ID。
9. screen_sn_visible 和 screen_sn_text 只用于明确标注为 SN、S/N、Serial Number、序列号的内容；屏幕只有 IMEI 时，screen_sn_visible=false、screen_sn_text=""，IMEI 写入 screen_identity_text。"""
        plugin_bridge = """3. 普通3C激活照片按文末《普通3C激活证据统一口径插件》逐图记录并由本地程序裁决。
4. activation_photo_ok、activation_evidence_type、activation_screen 和自由文本只作兼容输出，不能替代 activation_identity_by_image。
5. 仅根据照片中可明确辨认的商品形态，判断其是否与订单商品类型（category_name）属于同类商品，不得增加订单未提及的条件。仅当两者明显不属于同一类商品时才返回 PRODUCT_TYPE_MISMATCH，无法判断时返回 MODEL_UNCERTAIN。"""
        if legacy_rules not in prompt:
            raise RuntimeError("ordinary 3C activation rules changed without updating the digital activation plugin bridge")
        prompt = prompt.replace(legacy_rules, plugin_bridge)
        prompt = prompt + "\n\n" + read_digital_activation_evidence_prompt()
    if include_photo_authenticity and resolve_photo_auth_edge_mapping_mode(photo_auth_edge_mapping_mode) == "on":
        prompt = prompt + "\n\n" + read_photo_auth_edge_mapping_prompt()
    return prompt


def _normalize_photo_authenticity_observations(
    compliance: dict[str, Any], expected_image_ids: Any
) -> dict[str, ImageObservation]:
    normalized = validate_image_observations(
        compliance.get("photo_authenticity_by_image"), expected_image_ids
    )
    compliance["photo_authenticity_by_image"] = [
        asdict(normalized[image_id]) for image_id in expected_image_ids
    ]
    return normalized


def is_computer_product(fields: dict[str, Any]) -> bool:
    text = " ".join(
        str(fields.get(key) or "")
        for key in ("product_type", "cate_code_name", "goods_name", "product_name", "model")
    ).upper()
    return any(keyword in text for keyword in ("电脑", "笔记本", "PC", "LAPTOP", "COMPUTER"))


def precheck_task(task: dict[str, Any]) -> dict[str, Any]:
    fields = task.get("fields") or {}
    if not task.get("channel_order_no"):
        return _manual_precheck(task, "FIELD_MISSING", "channel_order_no missing")
    if not fields.get("product_type"):
        return _manual_precheck(task, "FIELD_MISSING", "鍟嗗搧绫诲瀷缂哄け")
    if not fields.get("system_sn"):
        return _manual_precheck(task, "SYSTEM_SN_MISSING", "绯荤粺SN缂哄け")

    groups = group_images_by_title(task) if task.get("image_groups") else assign_order_based_roles(task)
    activation_images = [
        image
        for title, images in groups.items()
        if is_activation_title(title)
        for image in images
    ]
    if not activation_images:
        return _manual_precheck(task, "ACTIVATION_PHOTO_INVALID", "activation photo group missing")
    if all_image_groups_share_duplicate(groups):
        return _manual_precheck(task, "DUPLICATE_IMAGE_EVIDENCE", "涓嶅悓鏍囬鍒嗙粍瀛樺湪閲嶅鍥剧墖")

    effective_category = effective_product_category(fields)
    is_home = effective_category == "home_appliance"
    address_ok = None
    if is_home:
        address_ok = is_address_precise_enough(fields.get("address"))
        if not address_ok:
            return _manual_precheck(task, "ADDRESS_TOO_COARSE", reason_code_to_chinese("ADDRESS_TOO_COARSE"), address_ok=False)

    return {
        "manual_required": False,
        "manual_reason_codes": [],
        "manual_reason": "",
        "groups": groups,
        "activation_images": activation_images,
        "address_ok": address_ok,
        "effective_category": effective_category,
    }


def _manual_precheck(task: dict[str, Any], code: str, reason: str, address_ok: bool | None = None) -> dict[str, Any]:
    return {
        "manual_required": True,
        "manual_reason_codes": [code],
        "manual_reason": reason,
        "groups": group_images_by_title(task),
        "activation_images": [],
        "address_ok": address_ok,
    }


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y", "是", "鏄?"}
    return bool(value)


def as_codes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def normalize_compliance_reason_codes(value: Any) -> list[str]:
    aliases = {
        "PHOTO_FAKE": "IMAGE_STRONG_RISK",
        "SCREEN_PHOTO_OF_SCREEN": "IMAGE_STRONG_RISK",
        "SN_TAMPERED": "IMAGE_STRONG_RISK",
        "IMAGE_TAMPERED": "IMAGE_STRONG_RISK",
        "PHOTO_INVALID": "MODEL_UNCERTAIN",
        "SN_MISSING_IN_ACTIVATION_PHOTO": "ACTIVATION_PHOTO_INVALID",
        "SN_NOT_FOUND": "ACTIVATION_PHOTO_INVALID",
        "SN_MISMATCH": "MODEL_UNCERTAIN",
        "OK": "",
        "PASS": "",
        "SN_FOUND": "",
        "SN_MATCH": "",
    }
    normalized_codes: list[str] = []
    for raw_code in as_codes(value):
        code = re.sub(r"\s+", "_", str(raw_code or "").strip().upper())
        code = aliases.get(code, code)
        if not code:
            continue
        if code not in COMPLIANCE_REASON_CODES:
            code = "MODEL_UNCERTAIN"
        if code not in normalized_codes:
            normalized_codes.append(code)
    priority = [
        "INVOICE_ORANGE_WARNING",
        "IMAGE_STRONG_RISK",
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
        "UNBOXING_PHOTO_INVALID",
        "ACTIVATION_PHOTO_INVALID",
        "MODEL_UNCERTAIN",
    ]
    return sorted(normalized_codes, key=lambda item: priority.index(item) if item in priority else len(priority))


def _local_image_content_digest(image: dict[str, Any]) -> str:
    local_path = str(image.get("local_path") or "").strip()
    if not local_path:
        return ""
    try:
        return hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return ""


def _write_json_atomically(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None
    return value if isinstance(value, dict) else None


def _cache_key(model: str, stage: str, prompt: str, payload: dict[str, Any], images: list[dict[str, Any]]) -> str:
    image_refs = [
        {
            "image_id": image.get("image_id"),
            "title": image.get("title"),
            "source_url": image.get("source_url") or image.get("url"),
            "local_path": image.get("local_path"),
            "local_content_sha256": _local_image_content_digest(image),
            "detail": image.get("_detail"),
        }
        for image in images
    ]
    raw = json.dumps(
        {"model": model, "stage": stage, "prompt": prompt, "payload": payload, "images": image_refs},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_cacheable_model_result(stage: str, prompt: str, parsed: dict[str, Any], images: list[dict[str, Any]]) -> bool:
    if stage == "hybrid_photo_authenticity_fallback":
        if len(images) != 1:
            return False
        observation = dict(parsed)
        observation.pop("result", None)
        observation["image_id"] = str(images[0].get("image_id") or "")
        try:
            validate_image_observations([observation], [observation["image_id"]])
            return True
        except PhotoAuthenticitySchemaError:
            return False
    if stage != "hybrid_compliance" or PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM not in prompt:
        return True
    expected_image_ids = [str(image.get("image_id") or "") for image in images]
    observations = parsed.get("photo_authenticity_by_image")
    if "【外部屏幕边缘候选复核插件】" in prompt:
        expected_image_ids = [
            image_id for image_id in expected_image_ids
            if not image_id.startswith(PHOTO_AUTH_EDGE_DIAGNOSTIC_PREFIX)
        ]
        if isinstance(observations, list):
            observations = [
                item for item in observations
                if not isinstance(item, dict)
                or not str(item.get("image_id") or "").startswith(PHOTO_AUTH_EDGE_DIAGNOSTIC_PREFIX)
            ]
    try:
        validate_image_observations(
            observations,
            expected_image_ids,
        )
        return True
    except PhotoAuthenticitySchemaError:
        return False


def _should_disable_qwen_thinking(model: str, stage: str) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.startswith("qwen3.") or normalized.startswith("qwen-")


def _wait_before_model_request(stage_deadline_at: float | None = None) -> None:
    global _last_model_request_at
    if MODEL_REQUEST_BUFFER_SEC <= 0:
        return

    if stage_deadline_at is None:
        _model_request_lock.acquire()
    else:
        remaining = stage_deadline_at - time.time()
        if remaining <= 0:
            raise OrderBudgetExceeded(_order_timeout_reason())
        if not _model_request_lock.acquire(timeout=remaining):
            raise OrderBudgetExceeded(_order_timeout_reason())
    try:
        if stage_deadline_at is not None and stage_deadline_at - time.time() <= 0:
            raise OrderBudgetExceeded(_order_timeout_reason())
        now = time.monotonic()
        if _last_model_request_at is not None:
            wait_sec = (_last_model_request_at + MODEL_REQUEST_BUFFER_SEC) - now
            if wait_sec > 0:
                if stage_deadline_at is not None:
                    remaining = stage_deadline_at - time.time()
                    if remaining <= 0:
                        raise OrderBudgetExceeded(_order_timeout_reason())
                    if wait_sec > remaining:
                        time.sleep(remaining)
                        raise OrderBudgetExceeded(_order_timeout_reason())
                time.sleep(wait_sec)
                now = time.monotonic()
        _last_model_request_at = now
    finally:
        _model_request_lock.release()


def _http_connection_for_url(parsed_url: urllib.parse.ParseResult, timeout: float) -> http.client.HTTPConnection:
    if parsed_url.scheme == "https":
        return http.client.HTTPSConnection(parsed_url.netloc, timeout=timeout)
    if parsed_url.scheme == "http":
        return http.client.HTTPConnection(parsed_url.netloc, timeout=timeout)
    raise ValueError(f"unsupported API URL scheme: {parsed_url.scheme}")


def _request_path(parsed_url: urllib.parse.ParseResult) -> str:
    path = parsed_url.path or "/"
    if parsed_url.query:
        return path + "?" + parsed_url.query
    return path


def _post_chat_completion_json(
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    *,
    read_timeout_sec: float = MODEL_TIMEOUT_SEC,
) -> dict[str, Any]:
    stage_timeout_sec = float(read_timeout_sec)
    if stage_timeout_sec <= 0:
        raise OrderBudgetExceeded(_order_timeout_reason())
    stage_deadline_at = time.time() + stage_timeout_sec
    deadline_expired = threading.Event()
    active_connection: dict[str, http.client.HTTPConnection | None] = {"connection": None}

    def deadline_reached() -> bool:
        return deadline_expired.is_set() or stage_deadline_at - time.time() <= 0

    def remaining_stage_timeout() -> float:
        if deadline_expired.is_set():
            raise OrderBudgetExceeded(_order_timeout_reason())
        remaining = stage_deadline_at - time.time()
        if remaining <= 0:
            deadline_expired.set()
            raise OrderBudgetExceeded(_order_timeout_reason())
        return remaining

    def close_connection_on_deadline() -> None:
        deadline_expired.set()
        connection = active_connection.get("connection")
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def read_response_body(
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
    ) -> bytes:
        read1 = getattr(response, "read1", None)
        if callable(read1):
            chunks: list[bytes] = []
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(remaining_stage_timeout())
                else:
                    remaining_stage_timeout()
                chunk = read1(65536)
                if not chunk:
                    remaining_stage_timeout()
                    return b"".join(chunks)
                chunks.append(chunk)
                remaining_stage_timeout()
        remaining_stage_timeout()
        raw = response.read()
        remaining_stage_timeout()
        return raw

    deadline_timer = threading.Timer(stage_timeout_sec, close_connection_on_deadline)
    deadline_timer.daemon = True
    deadline_timer.start()
    url = base_url.rstrip("/") + "/chat/completions"
    parsed_url = urllib.parse.urlparse(url)
    request_body = json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    last_exc: Exception | None = None

    try:
        for attempt in range(MODEL_CONNECT_RETRIES + 1):
            connection: http.client.HTTPConnection | None = None
            phase = "connect"
            try:
                _wait_before_model_request(stage_deadline_at)
                connect_timeout_sec = min(MODEL_CONNECT_TIMEOUT_SEC, remaining_stage_timeout())
                connection = _http_connection_for_url(parsed_url, connect_timeout_sec)
                active_connection["connection"] = connection
                connection.request("POST", _request_path(parsed_url), body=request_body, headers=headers)
                if connection.sock is not None:
                    connection.sock.settimeout(remaining_stage_timeout())
                phase = "read"
                response = connection.getresponse()
                remaining_stage_timeout()
                if response.status >= 400:
                    raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, None)
                raw = read_response_body(response, connection).decode("utf-8")
                result = json.loads(raw)
                remaining_stage_timeout()
                return result
            except OrderBudgetExceeded:
                raise
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                if deadline_reached():
                    raise OrderBudgetExceeded(_order_timeout_reason()) from exc
                last_exc = exc
                if phase == "connect" and attempt < MODEL_CONNECT_RETRIES:
                    continue
                if phase == "connect":
                    raise ModelConnectionError(
                        f"connect failed after {MODEL_CONNECT_RETRIES + 1} attempts "
                        f"with {MODEL_CONNECT_TIMEOUT_SEC}s timeout"
                    ) from exc
                raise
            finally:
                active_connection["connection"] = None
                if connection is not None:
                    connection.close()

        raise ModelConnectionError(
            f"connect failed after {MODEL_CONNECT_RETRIES + 1} attempts with {MODEL_CONNECT_TIMEOUT_SEC}s timeout"
        ) from last_exc
    finally:
        deadline_timer.cancel()
        if deadline_timer.is_alive():
            deadline_timer.join(timeout=0.1)


def _image_url_for_model(image: dict[str, Any]) -> str:
    url = image.get("source_url") or image.get("url")
    if url:
        return str(url)
    local_path = image.get("local_path")
    if not local_path:
        return ""
    path = Path(str(local_path))
    if not path.exists():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def clean_direct_sn_text(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^```(?:text)?|```$", "", value, flags=re.IGNORECASE).strip()
    value = value.strip("\"'`：: ，,。;；")
    if not value:
        return ""
    if "SN_NOT_FOUND" in value.upper():
        return "SN_NOT_FOUND"
    value = re.sub(r"^(?:SN|S/N|SERIAL\s*(?:NO\.?|NUMBER)?|序列号)\s*[:：]?\s*", "", value, flags=re.IGNORECASE).strip()
    parts = re.findall(r"[A-Za-z0-9][A-Za-z0-9_./: -]{4,}[A-Za-z0-9]", value)
    if parts:
        return max(parts, key=len).strip()
    return value.splitlines()[0].strip()


def call_direct_sn_ocr(
    base_url: str,
    api_key: str,
    model: str,
    images: list[dict[str, Any]],
    *,
    cache_dir: Path | None = None,
    timeout_sec: float = MODEL_TIMEOUT_SEC,
) -> tuple[str, float, dict[str, Any], bool]:
    image_refs = [
        {
            "image_id": image.get("image_id"),
            "title": image.get("title"),
            "source_url": image.get("source_url") or image.get("url"),
            "local_path": image.get("local_path"),
            "local_content_sha256": _local_image_content_digest(image),
            "detail": image.get("_detail"),
        }
        for image in images
    ]
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        raw_key = json.dumps(
            {"model": model, "stage": "direct_sn_ocr", "prompt": DIRECT_SN_PROMPT, "images": image_refs},
            ensure_ascii=False,
            sort_keys=True,
        )
        cache_path = cache_dir / f"{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}.json"
        if cache_path.exists():
            cached = _read_json_cache(cache_path)
            if cached is not None and "observed_sn" in cached:
                return cached["observed_sn"], 0.0, cached.get("usage") or {}, True

    content: list[dict[str, Any]] = [{"type": "text", "text": "只输出图片中最完整的 SN。"}]
    for image in images:
        url = _image_url_for_model(image)
        if url:
            content.append({"type": "image_url", "image_url": {"url": url, "detail": image.get("_detail") or "high"}})
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": DIRECT_SN_PROMPT}, {"role": "user", "content": content}],
    }
    if _should_disable_qwen_thinking(model, "direct_sn_ocr"):
        body["enable_thinking"] = False
    started = time.time()
    response_data = _post_chat_completion_json(base_url, api_key, body, read_timeout_sec=timeout_sec)
    elapsed = time.time() - started
    observed_sn = clean_direct_sn_text(response_data["choices"][0]["message"]["content"])
    usage = response_data.get("usage") or {}
    if cache_dir is not None:
        _write_json_atomically(
            cache_path,
            {
                "stage": "direct_sn_ocr",
                "cached_at": datetime.now().isoformat(),
                "observed_sn": observed_sn,
                "usage": usage,
            },
        )
    return observed_sn, elapsed, usage, False


def call_model(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    payload: dict[str, Any],
    images: list[dict[str, Any]],
    *,
    stage: str,
    cache_dir: Path | None = None,
    detail: str = "auto",
    timeout_sec: float = MODEL_TIMEOUT_SEC,
    allow_non_object: bool = False,
) -> tuple[Any, str, float, dict[str, Any], bool]:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{_cache_key(model, stage, prompt, payload, images)}.json"
        if cache_path.exists():
            cached = _read_json_cache(cache_path)
            if cached is not None and isinstance(cached.get("parsed"), dict) and _is_cacheable_model_result(stage, prompt, cached["parsed"], images):
                return cached["parsed"], cached["content_text"], 0.0, cached.get("usage") or {}, True
            if cached is not None:
                cache_path.unlink(missing_ok=True)

    content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    for image in images:
        url = image.get("source_url") or image.get("url")
        if not url and image.get("local_path"):
            path = Path(str(image.get("local_path")))
            if path.exists():
                mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                url = f"data:{mime};base64,{encoded}"
        if url:
            prompt_label = str(image.get("_prompt_label") or "").strip()
            if prompt_label:
                content.append({"type": "text", "text": prompt_label})
            content.append({"type": "image_url", "image_url": {"url": url, "detail": image.get("_detail") or detail}})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    if _should_disable_qwen_thinking(model, stage):
        body["enable_thinking"] = False
    started = time.time()
    response_data = _post_chat_completion_json(base_url, api_key, body, read_timeout_sec=timeout_sec)
    elapsed = time.time() - started
    content_text = response_data["choices"][0]["message"]["content"]
    parsed = json.loads(content_text)
    if not isinstance(parsed, dict) and not allow_non_object:
        raise ValueError("model JSON is not an object")
    usage = response_data.get("usage") or {}
    if (
        cache_dir is not None
        and isinstance(parsed, dict)
        and _is_cacheable_model_result(stage, prompt, parsed, images)
    ):
        _write_json_atomically(
            cache_path,
            {
                "stage": stage,
                "cached_at": datetime.now().isoformat(),
                "parsed": parsed,
                "content_text": content_text,
                "usage": usage,
            },
        )
    return parsed, content_text, elapsed, usage, False


def _is_retryable_model_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 425, 429, 500, 502, 503, 504}
    return False


def call_model_with_retry(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    payload: dict[str, Any],
    images: list[dict[str, Any]],
    *,
    stage: str,
    cache_dir: Path | None = None,
    detail: str = "auto",
    timeout_sec: float = MODEL_TIMEOUT_SEC,
    retry_timeout_sec: float = MODEL_RETRY_TIMEOUT_SEC,
    order_deadline_at: float | None = None,
    allow_non_object: bool = False,
) -> tuple[Any, str, float, dict[str, Any], bool]:
    effective_timeout_sec = _timeout_within_order_deadline(timeout_sec, order_deadline_at)
    kwargs = {
        "stage": stage,
        "cache_dir": cache_dir,
        "timeout_sec": effective_timeout_sec,
    }
    if detail != "auto":
        kwargs["detail"] = detail
    if allow_non_object:
        kwargs["allow_non_object"] = True
    try:
        return call_model(
            base_url,
            api_key,
            model,
            prompt,
            payload,
            images,
            **kwargs,
        )
    except Exception as exc:
        if isinstance(exc, OrderBudgetExceeded):
            raise
        if not _is_retryable_model_error(exc):
            raise
        if retry_timeout_sec <= 0:
            raise
        retry_kwargs = dict(kwargs)
        retry_kwargs["timeout_sec"] = _timeout_within_order_deadline(retry_timeout_sec, order_deadline_at)
        retry_result = call_model(
            base_url,
            api_key,
            model,
            prompt,
            payload,
            images,
            **retry_kwargs,
        )
        parsed, content_text, retry_elapsed, usage, cached = retry_result
        return parsed, content_text, effective_timeout_sec + retry_elapsed, usage, cached


def _usage_total(*usages: dict[str, Any]) -> int:
    return sum(int(usage.get("total_tokens") or 0) for usage in usages if usage)


def _order_deadline(started: float, order_timeout_sec: int = ORDER_TIMEOUT_SEC) -> float:
    return float(started) + float(order_timeout_sec)


def _order_timeout_reason(context: str = "") -> str:
    context_text = f"（{context}）" if context else ""
    return f"模型审核超过每单{ORDER_TIMEOUT_SEC}秒总期限{context_text}，已转人工复核"


def _order_timeout_manual(precheck: dict[str, Any], context: str = "") -> dict[str, Any]:
    return {
        "manual_required": True,
        "manual_reason_codes": ["MODEL_UNCERTAIN"],
        "manual_reason": _order_timeout_reason(context),
        "address_ok": precheck.get("address_ok", ""),
    }


def _remaining_order_timeout(started: float, order_timeout_sec: int = ORDER_TIMEOUT_SEC) -> float:
    return max(0.0, float(order_timeout_sec) - (time.time() - started))


def _remaining_deadline_timeout(order_deadline_at: float | None) -> float:
    if order_deadline_at is None:
        return float(MODEL_TIMEOUT_SEC)
    return max(0.0, float(order_deadline_at) - time.time())


def _timeout_within_order_deadline(timeout_sec: float, order_deadline_at: float | None = None) -> float:
    requested = float(timeout_sec)
    if order_deadline_at is None:
        return requested
    remaining = _remaining_deadline_timeout(order_deadline_at)
    if remaining <= 0:
        raise OrderBudgetExceeded(_order_timeout_reason())
    return min(requested, remaining)


def _stage_timeout_from_budget(started: float, order_timeout_sec: int = ORDER_TIMEOUT_SEC) -> float:
    remaining = _remaining_order_timeout(started, order_timeout_sec)
    if remaining <= 0:
        raise OrderBudgetExceeded(_order_timeout_reason())
    return min(float(MODEL_TIMEOUT_SEC), remaining)


def _order_budget_exhausted(started: float, order_timeout_sec: int = ORDER_TIMEOUT_SEC) -> bool:
    return _remaining_order_timeout(started, order_timeout_sec) <= MIN_STAGE_TIMEOUT_SEC


def _order_deadline_reached(started: float, order_timeout_sec: int = ORDER_TIMEOUT_SEC) -> bool:
    return _remaining_order_timeout(started, order_timeout_sec) <= 0


def _confidence_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sn_only_activation_images(task: dict[str, Any]) -> list[dict[str, Any]]:
    groups = group_images_by_title(task) if task.get("image_groups") else assign_order_based_roles(task)
    return [
        image
        for title, images in groups.items()
        if is_activation_title(title)
        for image in images
    ]


def _sn_diff_summary(system_sn: str, observed_sn: str) -> str:
    system = normalize_sn(system_sn)
    observed = normalize_sn(observed_sn)
    if not system or not observed:
        return ""
    diffs: list[str] = []
    max_len = max(len(system), len(observed))
    for index in range(max_len):
        left = system[index] if index < len(system) else "缺失"
        right = observed[index] if index < len(observed) else "缺失"
        if left != right:
            diffs.append(f"第{index + 1}位 系统={left} 模型={right}")
        if len(diffs) >= 8:
            break
    if len(system) != len(observed):
        diffs.append(f"长度 系统={len(system)} 模型={len(observed)}")
    return "；".join(diffs)


def _with_detail(images: list[dict[str, Any]], detail: str) -> list[dict[str, Any]]:
    return [dict(image, _detail=detail) for image in images]


def _all_grouped_images(groups: dict[str, list[dict[str, Any]]], activation_detail: str, other_detail: str) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for title, images in groups.items():
        detail = activation_detail if is_activation_title(title) else other_detail
        prepared.extend(_with_detail(images, detail))
    return prepared


def build_sn_payload(task: dict[str, Any], fields: dict[str, Any], activation_images: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_system_sn = normalize_sn(fields.get("system_sn", ""))
    return {
        "id": task["channel_order_no"],
        "product_type": fields.get("product_type", ""),
        "category_name": category_name_from_fields(fields),
        "sn_stage": "independent_read",
        "system_sn_available_to_model": False,
        "system_sn_len": len(normalized_system_sn),
        "comparison_policy": "read the SN from image independently; no full system SN is provided in this pass; sn_match=true means a complete readable SN was found, not that it matches the system SN. Local code will compare against the canonical system SN.",
        "ambiguous_character_policy": "When unsure, report the visible text and describe O/0/Q, I/1/L/7, S/5, B/8 ambiguity in manual_reason or visual_ambiguity_notes.",
        "activation_images": [
            {"image_id": image.get("image_id"), "title": image.get("title"), "url": image.get("source_url")}
            for image in activation_images
        ],
    }


VISUAL_SN_CONFUSION_GROUPS = (
    frozenset("0OQ"),
    frozenset("1IL7"),
    frozenset("5S"),
    frozenset("8B"),
)
VISUAL_SN_CONFUSION_CHARS = frozenset().union(*VISUAL_SN_CONFUSION_GROUPS)
SN_SUPPORT_CODES = {"", "OK", "PASS", "SN_MATCH", "MATCH"}
SN_NOT_FOUND_SENTINELS = {
    "SN_NOT_FOUND",
    "SNNOTFOUND",
    "NOT_FOUND",
    "NOTFOUND",
    "UNKNOWN",
    "NA",
    "N/A",
    "NONE",
    "NULL",
}


def _same_visual_sn_group(left: str, right: str) -> bool:
    if left == right:
        return True
    return any(left in group and right in group for group in VISUAL_SN_CONFUSION_GROUPS)


def _model_claims_system_sn_support(result: dict[str, Any]) -> bool:
    code = str(result.get("manual_reason_code") or "").strip().upper()
    if code in {"SN_MISMATCH", "SN_NOT_FOUND", "MODEL_UNCERTAIN"}:
        return False
    if _confidence_value(result.get("confidence")) < 0.9:
        return False
    return as_bool(result.get("matches_given_system_sn")) or as_bool(result.get("sn_match")) or code in (SN_SUPPORT_CODES - {""})


def _only_visual_sn_substitutions(system: str, observed: str) -> bool:
    if len(system) != len(observed) or not system or not observed:
        return False
    mismatches = [(left, right) for left, right in zip(system, observed) if left != right]
    if not mismatches:
        return False
    max_mismatches = 2 if len(system) >= 20 else 1
    return len(mismatches) <= max_mismatches and all(_same_visual_sn_group(left, right) for left, right in mismatches)


def _one_sided_visual_sn_insertion(shorter: str, longer: str) -> bool:
    if len(longer) - len(shorter) not in {1, 2}:
        return False
    i = j = edits = 0
    inserted: list[str] = []
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j] or _same_visual_sn_group(shorter[i], longer[j]):
            i += 1
            j += 1
            continue
        edits += 1
        inserted.append(longer[j])
        j += 1
        if edits > 2:
            return False
    if j < len(longer):
        inserted.extend(longer[j:])
    if i != len(shorter):
        return False
    return bool(inserted) and all(char in VISUAL_SN_CONFUSION_CHARS for char in inserted)


def _small_visual_sn_alignment_error(system: str, observed: str) -> bool:
    if not system or not observed:
        return False
    if min(len(system), len(observed)) < 12:
        return False
    if abs(len(system) - len(observed)) > 2:
        return False
    if len(system) == len(observed):
        return _only_visual_sn_substitutions(system, observed)
    shorter, longer = (system, observed) if len(system) < len(observed) else (observed, system)
    return _one_sided_visual_sn_insertion(shorter, longer)


def _is_sn_not_found_sentinel(value: Any) -> bool:
    raw = str(value or "").strip().upper()
    if not raw:
        return True
    normalized = normalize_sn(raw)
    return raw in SN_NOT_FOUND_SENTINELS or normalized in SN_NOT_FOUND_SENTINELS


def _candidate_observed_sn(result: dict[str, Any]) -> str:
    for key in ("observed_sn", "normalized_observed_sn", "read_sn", "normalized_read_sn"):
        value = result.get(key)
        if value is not None and not _is_sn_not_found_sentinel(value):
            return str(value)
    return ""


def _near_sn_mismatch_for_targeted_review(system: str, observed: str) -> bool:
    if not system or not observed:
        return False
    if min(len(system), len(observed)) < 12:
        return False
    if abs(len(system) - len(observed)) > 2:
        return False
    return _levenshtein_distance(system, observed) <= 2


def _needs_targeted_sn_review(fields: dict[str, Any], result: dict[str, Any]) -> bool:
    if result.get("manual_reason_code") != "SN_MISMATCH":
        return False
    system = normalize_sn(fields.get("system_sn", ""))
    observed = normalize_sn(result.get("normalized_observed_sn") or result.get("observed_sn") or "")
    return _near_sn_mismatch_for_targeted_review(system, observed)


def build_targeted_sn_payload(
    task: dict[str, Any],
    fields: dict[str, Any],
    activation_images: list[dict[str, Any]],
    previous_decision: dict[str, Any],
) -> dict[str, Any]:
    normalized_system_sn = normalize_sn(fields.get("system_sn", ""))
    return {
        "id": task["channel_order_no"],
        "product_type": fields.get("product_type", ""),
        "category_name": category_name_from_fields(fields),
        "sn_stage": "targeted_system_sn_review",
        "system_sn_available_to_model": True,
        "system_sn": fields.get("system_sn", ""),
        "normalized_system_sn": normalized_system_sn,
        "previous_decision": previous_decision,
        "comparison_policy": "targeted review: answer whether the exact system_sn is visible in the activation/SN images. Return matches_given_system_sn=true only if the image supports the given system_sn; do not pass similar-looking strings.",
        "activation_images": [
            {"image_id": image.get("image_id"), "title": image.get("title"), "url": image.get("source_url")}
            for image in activation_images
        ],
    }


def _system_sn_supported_by_visual_ambiguity(
    system: str,
    observed: str,
    result: dict[str, Any],
    *,
    allow_alignment_error: bool = False,
    allow_explicit_false: bool = True,
) -> bool:
    return False


def _normalize_sn_result(fields: dict[str, Any], result: dict[str, Any], *, require_positive_system_match: bool = False) -> dict[str, Any]:
    normalized = dict(result)
    system = normalize_sn(fields.get("system_sn", ""))
    decision = {
        **result,
        "system_sn": system,
        "imei1": fields.get("imei1", ""),
        "imei2": fields.get("imei2", ""),
        "effective_category": fields.get("effective_category") or effective_product_category(fields),
    }
    state, observed_source, observed = _evaluate_sn_evidence(decision)
    code = str(result.get("manual_reason_code") or "").strip().upper()
    explicit_rejection = (
        is_explicit_false(result.get("matches_given_system_sn"))
        or code in {"SN_MISMATCH", "SN_NOT_FOUND", "MODEL_UNCERTAIN"}
    )
    if state == "match" and require_positive_system_match and explicit_rejection:
        normalized["sn_match"] = False
        normalized["observed_sn"] = observed_source
        normalized["normalized_observed_sn"] = observed
        if not code or code in {"OK", "PASS", "SN_MATCH", "MATCH"}:
            code = "MODEL_UNCERTAIN"
        normalized["manual_reason_code"] = code
        normalized["manual_reason_codes"] = [code]
        return normalized

    normalized["sn_match"] = state == "match"
    normalized["observed_sn"] = observed_source
    normalized["normalized_observed_sn"] = observed
    if state == "mismatch":
        normalized["manual_reason_code"] = "SN_MISMATCH"
        normalized["manual_reason_codes"] = ["SN_MISMATCH"]
        normalized["manual_reason"] = "照片中SN与系统SN不一致"
    elif state == "not_found":
        normalized["manual_reason_code"] = "SN_NOT_FOUND"
        normalized["manual_reason_codes"] = ["SN_NOT_FOUND"]
    else:
        normalized["manual_reason_code"] = ""
        normalized["manual_reason_codes"] = []
    return normalized


def _normalize_sn_v2_result(evidence: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    normalized = {**evidence, **decision}
    code = str(decision.get("manual_reason_code") or "").strip().upper()
    normalized["manual_reason_codes"] = [code] if code else []
    normalized["raw_observed_sn"] = str(decision.get("observed_sn") or "")
    normalized["confidence"] = evidence.get("confidence", "")
    return normalized


def needs_high_detail_review(result: dict[str, Any]) -> bool:
    codes = set(as_codes(result.get("manual_reason_codes")))
    if result.get("manual_reason_code"):
        codes.add(str(result.get("manual_reason_code")))
    if "SN_NOT_FOUND" in codes or "MODEL_UNCERTAIN" in codes:
        return True
    if _confidence_value(result.get("confidence")) and _confidence_value(result.get("confidence")) < 0.75:
        return True
    return False


def is_explicit_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "no", "0", "n", "否"}
    return False


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _candidate_sn(candidate: dict[str, Any]) -> str:
    return normalize_sn(candidate.get("normalized_text") or candidate.get("raw_text") or "")


def _candidate_source(candidate: dict[str, Any]) -> str:
    return str(candidate.get("source") or "").strip().upper()


def _candidate_raw_text(candidate: dict[str, Any]) -> str:
    return str(candidate.get("raw_text") or candidate.get("label") or "").strip()


_NON_SN_LABEL_RE = re.compile(
    r"(?<![0-9A-Z])(?:IMEI(?:[ _]*[12])?|EID)(?![0-9A-Z])",
    re.IGNORECASE,
)
_SN_BINDING_LABEL_RE = re.compile(
    r"(?<![0-9A-Z])(?:S\s*/\s*N|SN|(?:\(S\)\s*)?SERIAL(?:\s+(?:NO\.?|NUMBER))?|序列号)(?![0-9A-Z])",
    re.IGNORECASE,
)
_MODEL_BINDING_LABEL_RE = re.compile(
    r"(?<![0-9A-Z])(?:MODEL(?:[\s_/-]+(?:NO\.?|NUMBER))?|型号|产品型号)(?![0-9A-Z])",
    re.IGNORECASE,
)
_STRUCTURED_IDENTITY_LABEL_RE = re.compile(
    r"(?<![0-9A-Z])(?:I[\s_/-]*M[\s_/-]*E[\s_/-]*I(?:[\s_/-]*[12])?|E[\s_/-]*I[\s_/-]*D)(?![0-9A-Z])",
    re.IGNORECASE,
)
_SN_SOURCE_ORDER = {
    "DEVICE_SCREEN": 0,
    "SCREEN": 0,
    "DEVICE_BODY": 1,
    "PACKAGE_LABEL": 2,
    "BARCODE_TEXT": 2,
}


def _candidate_texts(candidate: dict[str, Any]) -> list[str]:
    return [
        str(candidate.get(key) or "").strip()
        for key in ("raw_text", "label")
        if str(candidate.get(key) or "").strip()
    ]


def _compact_field_type(value: Any) -> str:
    return re.sub(r"[ _]", "", str(value or "").strip().upper())


def _punctuation_free_field_type(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").strip().upper())


def _order_imeis(decision: dict[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            normalize_sn(decision.get("imei1") or ""),
            normalize_sn(decision.get("imei2") or ""),
        )
        if normalized
    }


def _has_non_sn_label(value: Any) -> bool:
    return bool(_NON_SN_LABEL_RE.search(str(value or "")))


def _has_normalized_identity_block(value: Any) -> bool:
    compact = re.sub(r"[^0-9A-Z]", "", str(value or "").upper())
    markers = list(re.finditer(r"(?:IMEI[12]?|EID)(?=\d)", compact))
    return bool(markers and (markers[0].start() == 0 or len(markers) >= 2))


def _label_binds_candidate_value(raw_text: Any, candidate_sn: str, label_re: Any) -> bool:
    if not candidate_sn:
        return False
    separated_value = r"[\W_]*".join(re.escape(char) for char in candidate_sn)
    binding_re = re.compile(
        rf"(?:{label_re.pattern})[\W_]*{separated_value}(?![0-9A-Z])",
        re.IGNORECASE,
    )
    return bool(binding_re.search(str(raw_text or "")))


def _is_explicit_sn_candidate(candidate: dict[str, Any]) -> bool:
    if _compact_field_type(candidate.get("field_type")) in {"SN", "SERIAL", "SERIALNUMBER"}:
        return True
    candidate_sn = _candidate_sn(candidate)
    return any(
        _label_binds_candidate_value(text, candidate_sn, _SN_BINDING_LABEL_RE)
        for text in _candidate_texts(candidate)
    )


def _is_non_sn_candidate(candidate: dict[str, Any], decision: dict[str, Any]) -> bool:
    field_type = _compact_field_type(candidate.get("field_type"))
    if field_type in {"IMEI", "IMEI1", "IMEI2", "EID"}:
        return True
    candidate_sn = _candidate_sn(candidate)
    if candidate_sn and candidate_sn in _order_imeis(decision):
        return True
    normalized_text = str(candidate.get("normalized_text") or "").strip()
    if _has_non_sn_label(normalized_text) or _has_normalized_identity_block(normalized_text):
        return True
    raw_text = str(candidate.get("raw_text") or "").strip()
    sn_bound = _label_binds_candidate_value(raw_text, candidate_sn, _SN_BINDING_LABEL_RE)
    identity_bound = any(
        _label_binds_candidate_value(text, candidate_sn, _NON_SN_LABEL_RE)
        for text in _candidate_texts(candidate)
    )
    if identity_bound:
        return True
    has_identity_label = any(_has_non_sn_label(text) for text in _candidate_texts(candidate))
    if has_identity_label and field_type in {"SN", "SERIAL", "SERIALNUMBER"}:
        return not sn_bound
    return has_identity_label


def _is_trustworthy_sn_candidate(candidate: dict[str, Any], decision: dict[str, Any]) -> bool:
    return (
        as_bool(candidate.get("readable"))
        and _is_explicit_sn_candidate(candidate)
        and not _is_non_sn_candidate(candidate, decision)
        and bool(_candidate_sn(candidate))
    )


def _is_readable_non_imei_candidate(candidate: dict[str, Any], decision: dict[str, Any]) -> bool:
    return (
        as_bool(candidate.get("readable"))
        and not _is_non_sn_candidate(candidate, decision)
        and bool(_candidate_sn(candidate))
    )


def _is_explicit_model_candidate(candidate: dict[str, Any]) -> bool:
    if _punctuation_free_field_type(candidate.get("field_type")) in {
        "MODEL",
        "MODELNO",
        "MODELNUMBER",
    }:
        return True
    if any(
        _MODEL_BINDING_LABEL_RE.search(str(candidate.get(key) or ""))
        for key in ("label", "label_text")
    ):
        return True
    candidate_sn = _candidate_sn(candidate)
    return any(
        _label_binds_candidate_value(candidate.get(key), candidate_sn, _MODEL_BINDING_LABEL_RE)
        for key in ("raw_context", "raw_text")
    )


def _is_explicit_identity_candidate(candidate: dict[str, Any]) -> bool:
    if _punctuation_free_field_type(candidate.get("field_type")) in {
        "IMEI",
        "IMEI1",
        "IMEI2",
        "EID",
    }:
        return True
    if any(
        _STRUCTURED_IDENTITY_LABEL_RE.search(str(candidate.get(key) or ""))
        for key in ("label", "label_text")
    ):
        return True
    candidate_sn = _candidate_sn(candidate)
    return any(
        _label_binds_candidate_value(
            candidate.get(key), candidate_sn, _STRUCTURED_IDENTITY_LABEL_RE
        )
        for key in ("raw_context", "raw_text")
    )


def _authoritative_home_appliance_sn(decision: dict[str, Any]) -> str:
    category = str(decision.get("effective_category") or "").strip().lower()
    if category:
        if category != "home_appliance":
            return ""
    elif not as_bool(decision.get("is_home_appliance")):
        return ""

    system_sn = normalize_sn(decision.get("system_sn") or decision.get("normalized_system_sn") or "")
    if not system_sn:
        return ""

    for candidate in _list_value(decision.get("sn_candidates")):
        if (
            not isinstance(candidate, dict)
            or _is_explicit_identity_candidate(candidate)
            or not _is_readable_non_imei_candidate(candidate, decision)
            or _is_explicit_model_candidate(candidate)
        ):
            continue
        if _candidate_sn(candidate) == system_sn:
            return system_sn
    return ""


def _trustworthy_sn_candidates(
    decision: dict[str, Any],
    *,
    sources: set[str] | None = None,
) -> list[tuple[int, str, str]]:
    trustworthy: list[tuple[int, str, str]] = []
    for candidate in _list_value(decision.get("sn_candidates")):
        if not isinstance(candidate, dict) or not _is_trustworthy_sn_candidate(candidate, decision):
            continue
        source = _candidate_source(candidate)
        if sources is not None and source not in sources:
            continue
        candidate_sn = _candidate_sn(candidate)
        trustworthy.append(
            (
                _SN_SOURCE_ORDER.get(source, len(_SN_SOURCE_ORDER)),
                candidate_sn,
                _candidate_raw_text(candidate),
            )
        )
    return sorted(trustworthy, key=lambda item: (item[0], item[1], item[2].upper()))


def _top_level_observed_group(
    decision: dict[str, Any],
) -> tuple[str, str, bool, tuple[tuple[str, str], ...]]:
    observed_values = [
        decision.get(key)
        for key in ("observed_sn", "normalized_observed_sn", "read_sn", "normalized_read_sn")
        if decision.get(key) is not None and not _is_sn_not_found_sentinel(decision.get(key))
    ]
    order_imeis = _order_imeis(decision)
    has_identity_value = False
    unique_readings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in observed_values:
        normalized = normalize_sn(value)
        if (
            _has_non_sn_label(value)
            or _has_normalized_identity_block(value)
            or normalized in order_imeis
        ):
            has_identity_value = True
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_readings.append((str(value), normalized))

    if not unique_readings:
        return "", "", has_identity_value, ()
    observed_source, observed_sn = unique_readings[0]
    return observed_source, observed_sn, has_identity_value, tuple(unique_readings)


def _preferred_sn_conflict(
    readings: tuple[tuple[str, str], ...],
    candidates: list[tuple[int, str, str]],
    system_sn: str,
) -> tuple[str, str]:
    for reading in readings:
        if reading[1] != system_sn:
            return reading
    for _rank, candidate_sn, _raw in candidates:
        if candidate_sn != system_sn:
            return candidate_sn, candidate_sn
    if readings:
        return readings[0]
    if candidates:
        return candidates[0][1], candidates[0][1]
    return "", ""


def _evaluate_sn_evidence(decision: dict[str, Any]) -> tuple[str, str, str]:
    system_sn = normalize_sn(decision.get("system_sn") or decision.get("normalized_system_sn") or "")
    authoritative_sn = _authoritative_home_appliance_sn(decision)
    if authoritative_sn:
        return "match", authoritative_sn, authoritative_sn

    observed_source, observed_sn, _has_identity_value, top_level_readings = _top_level_observed_group(decision)
    candidates = _trustworthy_sn_candidates(decision)
    unique_candidate_values = {candidate_sn for _rank, candidate_sn, _raw in candidates}

    if len(top_level_readings) >= 2:
        conflict_source, conflict_sn = _preferred_sn_conflict(
            top_level_readings, candidates, system_sn
        )
        return "mismatch", conflict_source, conflict_sn

    if observed_sn:
        distinct_candidates = [candidate for candidate in candidates if candidate[1] != observed_sn]
        if distinct_candidates:
            if observed_sn != system_sn:
                return "mismatch", observed_source, observed_sn
            conflict = next(
                (candidate for candidate in distinct_candidates if candidate[1] != system_sn),
                distinct_candidates[0],
            )
            return "mismatch", conflict[1], conflict[1]
        return ("match" if system_sn and observed_sn == system_sn else "mismatch"), observed_source, observed_sn

    if not candidates:
        return "not_found", "", ""

    selected_sn = candidates[0][1]
    if len(unique_candidate_values) >= 2:
        conflict_source, conflict_sn = _preferred_sn_conflict((), candidates, system_sn)
        return "mismatch", conflict_source, conflict_sn
    return ("match" if system_sn and selected_sn == system_sn else "mismatch"), selected_sn, selected_sn


def _conflicting_screen_sn(decision: dict[str, Any]) -> str:
    system_sn = normalize_sn(decision.get("system_sn") or decision.get("normalized_system_sn") or "")
    if not system_sn:
        return ""

    for _rank, candidate_sn, _raw in _trustworthy_sn_candidates(
        decision,
        sources={"SCREEN", "DEVICE_SCREEN"},
    ):
        if candidate_sn != system_sn:
            return candidate_sn

    return ""


def _observed_sn_conflict_reason(decision: dict[str, Any]) -> str:
    return "SN_MISMATCH" if _conflicting_observed_sn(decision) else ""


def _conflicting_observed_sn(decision: dict[str, Any]) -> str:
    system_sn = normalize_sn(decision.get("system_sn") or decision.get("normalized_system_sn") or "")
    if not system_sn:
        return ""
    if _authoritative_home_appliance_sn(decision):
        return ""

    _observed_source, observed_sn, _has_identity_value, top_level_readings = _top_level_observed_group(decision)
    candidates = _trustworthy_sn_candidates(decision)
    if len(top_level_readings) >= 2:
        return _preferred_sn_conflict(top_level_readings, candidates, system_sn)[1]
    if observed_sn and observed_sn != system_sn:
        return observed_sn
    for _rank, candidate_sn, _raw in candidates:
        if candidate_sn != system_sn:
            return candidate_sn
    return ""


def _has_photo_integrity_risk(decision: dict[str, Any], evidence_type: str) -> bool:
    integrity = _dict_value(decision.get("photo_integrity"))
    screen = _dict_value(decision.get("activation_screen"))
    return (
        as_bool(decision.get("image_risk"))
        or evidence_type == "COLLAGE_OR_EDIT_RISK"
        or as_bool(decision.get("collage_or_edit_suspected"))
        or as_bool(integrity.get("collage_or_edit_risk"))
        or as_bool(integrity.get("screen_shows_photo_or_screenshot"))
        or str(screen.get("screen_content_type") or "").strip().upper() == "PHOTO_VIEWER_OR_SCREENSHOT"
        or _has_structured_photo_integrity_risk(decision, evidence_type)
    )


def _has_structured_photo_integrity_risk(decision: dict[str, Any], evidence_type: str) -> bool:
    integrity = _dict_value(decision.get("photo_integrity"))
    tamper = _dict_value(decision.get("tamper_checks"))
    non_font_tamper_fields = (
        "perspective_consistency_ok",
        "noise_compression_consistency_ok",
        "edge_blending_ok",
        "screen_reflection_consistency_ok",
    )
    return (
        as_bool(integrity.get("erasure_or_overwrite_risk"))
        or as_bool(integrity.get("local_background_break_risk"))
        or is_explicit_false(integrity.get("evidence_chain_trustworthy"))
        or as_bool(tamper.get("erasure_or_overwrite_risk"))
        or as_bool(tamper.get("local_background_break_risk"))
        or any(is_explicit_false(tamper.get(field)) for field in non_font_tamper_fields)
    )


def _is_high_risk_digital_product(decision: dict[str, Any]) -> bool:
    product_type = str(decision.get("product_type") or "").upper()
    return any(marker in product_type for marker in ("平板", "手表", "手环", "TABLET", "WATCH"))


def _is_watch_product(decision: dict[str, Any]) -> bool:
    text = " ".join(
        str(decision.get(key) or "")
        for key in ("product_type", "cate_code_name", "goods_name", "product_name", "model")
    ).upper()
    return (
        any(marker in text for marker in ("手表", "手环"))
        or bool(re.search(r"\b(?:WATCH|SMARTWATCH|WRISTBAND)\b", text))
    )


def _is_phone_or_tablet_product(decision: dict[str, Any]) -> bool:
    text = " ".join(
        str(decision.get(key) or "")
        for key in ("product_type", "cate_code_name", "goods_name", "product_name", "model")
    ).upper()
    return (
        any(marker in text for marker in ("手机", "平板"))
        or bool(re.search(r"\b(?:PHONE|SMARTPHONE|TABLET)\b", text))
    )


def _is_phone_product(decision: dict[str, Any]) -> bool:
    text = " ".join(
        str(decision.get(key) or "")
        for key in ("product_type", "cate_code_name", "goods_name", "product_name", "model")
    ).upper()
    return (
        "手机" in text
        or bool(re.search(r"\b(?:PHONE|SMARTPHONE)\b", text))
    )


def _digital_activation_evidence_reason(decision: dict[str, Any]) -> str | None:
    if str(decision.get("digital_activation_evidence_mode") or "off").strip().lower() != "on":
        return None
    if str(decision.get("effective_category") or "").strip().lower() != "ordinary_3c":
        return None
    is_watch = _is_watch_product(decision)
    if not is_watch and not _is_phone_or_tablet_product(decision):
        return None

    expected_image_ids = {
        str(image_id).strip()
        for image_id in _list_value(decision.get("_activation_image_ids"))
        if str(image_id).strip()
    }
    raw_observations = decision.get("activation_identity_by_image")
    if not expected_image_ids or not isinstance(raw_observations, list) or not raw_observations:
        return "MODEL_UNCERTAIN"

    raw_image_ids = [
        str(raw.get("image_id") or "").strip() if isinstance(raw, dict) else ""
        for raw in raw_observations
    ]
    if (
        len(raw_image_ids) != len(expected_image_ids)
        or len(set(raw_image_ids)) != len(raw_image_ids)
        or set(raw_image_ids) != expected_image_ids
    ):
        return "MODEL_UNCERTAIN"

    observations: list[list[Any]] = []
    for raw in raw_observations:
        if not isinstance(raw, dict):
            return "MODEL_UNCERTAIN"
        identity_fields = raw.get("identity_fields")
        if not isinstance(identity_fields, list):
            return "MODEL_UNCERTAIN"
        observations.append(identity_fields)

    allowed_types = (
        {"SN", "SERIAL_NUMBER", "IMEI1", "IMEI2"}
        if not is_watch and _is_phone_product(decision)
        else {"SN", "SERIAL_NUMBER"}
    )
    aliases = {"SERIAL": "SERIAL_NUMBER", "SERIALNUMBER": "SERIAL_NUMBER", "S/N": "SERIAL_NUMBER"}
    for identity_fields in observations:
        for field in identity_fields:
            if not isinstance(field, dict):
                return "MODEL_UNCERTAIN"
            field_type = str(field.get("field_type") or "").strip().upper()
            field_type = aliases.get(field_type, field_type)
            if (
                field_type in allowed_types
                and isinstance(field.get("readable"), bool)
                and field["readable"]
                and isinstance(field.get("complete"), bool)
                and field["complete"]
                and bool(str(field.get("raw_value") or "").strip())
            ):
                return ""
    return "ACTIVATION_PHOTO_INVALID"


def _is_home_appliance_decision(decision: dict[str, Any]) -> bool:
    effective_category = str(decision.get("effective_category") or "").strip().lower()
    if effective_category:
        return effective_category == "home_appliance"
    text = " ".join(
        str(decision.get(key) or "")
        for key in ("product_type", "cate_code_name", "goods_name", "product_name", "model")
    ).upper()
    if any(marker in text for marker in ("电脑", "笔记本", "PC", "LAPTOP", "COMPUTER")):
        return False
    category = classify_audit_category("guobu", decision).category
    if category == "home_appliance":
        return True
    if category == "3c":
        return False
    return as_bool(decision.get("is_home_appliance"))


def _strict_home_no_box_evidence(decision: dict[str, Any]) -> bool:
    product_type_match = str(decision.get("product_type_match") or "").strip().lower()
    evidence_type = str(decision.get("activation_evidence_type") or "").strip().upper()
    return (
        as_bool(decision.get("_sn_already_verified_by_system"))
        and as_bool(decision.get("whole_product_visible"))
        and as_bool(decision.get("home_or_installation_scene_visible"))
        and product_type_match in {"true", "match", "matched"}
        and as_bool(decision.get("product_photo_ok"))
        and as_bool(decision.get("unboxing_photo_ok"))
        and not _has_photo_integrity_risk(decision, evidence_type)
    )


def _activation_pass_gate_reason(decision: dict[str, Any]) -> str:
    evidence_type = str(decision.get("activation_evidence_type") or "").strip().upper()
    if not evidence_type:
        return "ACTIVATION_PHOTO_INVALID"

    system_sn = normalize_sn(decision.get("system_sn") or decision.get("normalized_system_sn") or "")
    if not system_sn:
        return "ACTIVATION_PHOTO_INVALID"

    candidates = [item for item in _list_value(decision.get("sn_candidates")) if isinstance(item, dict)]
    readable_candidates = [
        candidate
        for candidate in candidates
        if _is_readable_non_imei_candidate(candidate, decision)
    ]

    def candidate_supports_system_sn(candidate: dict[str, Any]) -> bool:
        candidate_sn = _candidate_sn(candidate)
        candidate_result = {
            "sn_match": candidate.get("matches_system_sn"),
            "confidence": decision.get("confidence"),
            "manual_reason_code": decision.get("manual_reason_code", ""),
        }
        return candidate_sn == system_sn or _system_sn_supported_by_visual_ambiguity(
            system_sn,
            candidate_sn,
            candidate_result,
            allow_explicit_false=False,
        )

    def text_supports_system_sn(text: str) -> bool:
        text_sn = normalize_sn(text)
        text_result = {
            "sn_match": decision.get("sn_match", True),
            "confidence": decision.get("confidence"),
            "manual_reason_code": decision.get("manual_reason_code", ""),
        }
        return text_sn == system_sn or _system_sn_supported_by_visual_ambiguity(
            system_sn,
            text_sn,
            text_result,
            allow_explicit_false=False,
        )

    for candidate in readable_candidates:
        candidate_sn = _candidate_sn(candidate)
        if candidate_sn != system_sn and _is_explicit_sn_candidate(candidate) and not candidate_supports_system_sn(candidate):
            return "SN_MISMATCH"
        if is_explicit_false(candidate.get("matches_system_sn")) and _is_explicit_sn_candidate(candidate) and not candidate_supports_system_sn(candidate):
            return "SN_MISMATCH"

    screen = _dict_value(decision.get("activation_screen"))
    screen_text = normalize_sn(screen.get("screen_sn_text") or "")
    if as_bool(screen.get("screen_sn_visible")) and screen_text and screen_text != system_sn and not text_supports_system_sn(screen_text):
        return "SN_MISMATCH"

    if evidence_type == "PACKAGE_SN_ONLY" and _is_home_appliance_decision(decision):
        observed_sn = normalize_sn(decision.get("observed_sn") or decision.get("normalized_observed_sn") or "")
        package_match = any(_candidate_source(candidate) == "PACKAGE_LABEL" and candidate_supports_system_sn(candidate) for candidate in readable_candidates)
        return "" if observed_sn == system_sn or text_supports_system_sn(observed_sn) or package_match else "ACTIVATION_PHOTO_INVALID"

    screen_match = any(_candidate_source(candidate) == "SCREEN" and candidate_supports_system_sn(candidate) for candidate in readable_candidates)
    if evidence_type == "SCREEN_SN":
        if as_bool(screen.get("screen_sn_visible")) and (text_supports_system_sn(screen_text) or screen_match):
            return ""
        return "ACTIVATION_PHOTO_INVALID"
    if not readable_candidates:
        return "ACTIVATION_PHOTO_INVALID"
    if evidence_type == "SCREEN_ACTIVE_WITH_SN":
        package_match = any(_candidate_source(candidate) == "PACKAGE_LABEL" and candidate_supports_system_sn(candidate) for candidate in readable_candidates)
        screen_content_type = str(screen.get("screen_content_type") or "").strip().upper()
        screen_identity = bool(str(screen.get("screen_identity_text") or "").strip()) or screen_content_type in {"DEVICE_INFO_WITH_ID", "ACTIVATION_SUCCESS"}
        if package_match and as_bool(decision.get("same_photo_or_same_group_chain")) and as_bool(screen.get("screen_on")) and screen_identity:
            return ""
        return "ACTIVATION_PHOTO_INVALID"
    return "ACTIVATION_PHOTO_INVALID"


def _verified_sn_activation_form_reason(decision: dict[str, Any]) -> str:
    category = str(decision.get("effective_category") or "").strip().lower()
    evidence_type = str(decision.get("activation_evidence_type") or "").strip().upper()
    if _conflicting_screen_sn(decision):
        return "SN_MISMATCH"
    digital_reason = _digital_activation_evidence_reason(decision)
    if digital_reason is not None:
        return digital_reason
    if category == "ordinary_3c" and _is_watch_product(decision):
        screen = _dict_value(decision.get("activation_screen"))
        screen_content_type = str(screen.get("screen_content_type") or "").strip().upper()
        screen_sn = normalize_sn(screen.get("screen_sn_text") or "")
        identity_text = str(screen.get("screen_identity_text") or "").strip()
        has_identity_label = bool(re.search(r"SN|S/N|SERIAL|IMEI|序列号|激活码", identity_text, re.IGNORECASE))
        if not as_bool(screen.get("screen_on")):
            return "ACTIVATION_PHOTO_INVALID"
        if screen_content_type not in {"ABOUT_DEVICE_SN", "DEVICE_INFO_WITH_ID"}:
            return "ACTIVATION_PHOTO_INVALID"
        if not (as_bool(screen.get("screen_sn_visible")) and screen_sn) and not has_identity_label:
            return "ACTIVATION_PHOTO_INVALID"
    if category == "home_appliance":
        return ""
    if category == "unknown":
        return "MODEL_UNCERTAIN"
    invalid_evidence = {
        "",
        "UNKNOWN",
        "UNCLEAR",
        "NONE",
        "NO_SCREEN_ON",
        "SCREEN_ON_NO_SN",
        "SCREEN_ON_NO_IDENTITY",
        "PACKAGE_ONLY",
        "PACKAGE_SN_ONLY",
    }
    if category in {"ordinary_3c", "computer"} and evidence_type in invalid_evidence:
        return "ACTIVATION_PHOTO_INVALID"
    return ""


def _all_three_duplicate_claim(decision: dict[str, Any]) -> bool:
    groups = decision.get("_exact_duplicate_image_groups")
    if not isinstance(groups, list):
        return False
    return any(
        isinstance(group, list) and len({str(image_id) for image_id in group if str(image_id)}) >= 3
        for group in groups
    )


_NON_AUTH_IMAGE_STRONG_RISK_MARKERS = (
    "SN", "IMEI", "序列号", "条码", "擦除", "覆盖", "贴字", "底纹", "字体", "亮度",
    "噪声", "篡改", "PS", "P图", "修改", "拼图", "拼接", "图层", "多台", "多个包装",
    "混拍", "证据链", "同一实物",
)


def _should_defer_image_strong_risk_to_authenticity_gate(decision: dict[str, Any]) -> bool:
    """Defer only legacy authenticity-only IMAGE_STRONG_RISK claims to the new gate."""
    evidence_type = str(decision.get("activation_evidence_type") or "").strip().upper()
    if _has_structured_photo_integrity_risk(decision, evidence_type):
        return False
    reason_text = " ".join(
        str(decision.get(key) or "")
        for key in ("manual_reason", "manual_reason_cn", "image_risk_reason")
    )
    return not any(marker in reason_text for marker in _NON_AUTH_IMAGE_STRONG_RISK_MARKERS)


def enforce_photo_noncompliance_manual(
    decision: dict[str, Any],
    *,
    address_ok: bool | None = None,
    defer_image_authenticity_to_local: bool = False,
) -> dict[str, Any]:
    sn_already_verified = as_bool(decision.get("_sn_already_verified_by_system"))
    protected_code_priority = [
        "INVOICE_ORANGE_WARNING",
        "IMAGE_STRONG_RISK",
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
        "UNBOXING_PHOTO_INVALID",
        "ACTIVATION_PHOTO_INVALID",
        "MODEL_UNCERTAIN",
    ]
    if not sn_already_verified:
        protected_code_priority.insert(2, "SN_MISMATCH")
        protected_code_priority.insert(3, "SN_NOT_FOUND")
    protected_codes = set(protected_code_priority)
    normalized = dict(decision)
    if sn_already_verified:
        existing_codes = normalize_compliance_reason_codes(normalized.get("manual_reason_codes"))
    else:
        existing_codes = as_codes(normalized.get("manual_reason_codes"))
    if normalized.get("manual_reason_code"):
        if sn_already_verified:
            existing_codes.extend(normalize_compliance_reason_codes(normalized.get("manual_reason_code")))
        else:
            existing_codes.append(str(normalized.get("manual_reason_code")))
    defer_image_strong_risk = (
        defer_image_authenticity_to_local
        and "IMAGE_STRONG_RISK" in existing_codes
        and _should_defer_image_strong_risk_to_authenticity_gate(normalized)
    )
    if defer_image_strong_risk:
        protected_code_priority = [code for code in protected_code_priority if code != "IMAGE_STRONG_RISK"]
        protected_codes.discard("IMAGE_STRONG_RISK")
        existing_codes = [code for code in existing_codes if code != "IMAGE_STRONG_RISK"]
        normalized["image_risk"] = False

    all_three_duplicate = _all_three_duplicate_claim(normalized)
    if not all_three_duplicate:
        existing_codes = [item for item in existing_codes if item != "DUPLICATE_IMAGE_EVIDENCE"]

    code = next((item for item in protected_code_priority if item in existing_codes), "")
    original_code = code
    sn_conflict_code = "" if sn_already_verified else _observed_sn_conflict_reason(normalized)
    if code == "SN_MISMATCH" and not sn_conflict_code:
        code = ""
        existing_codes = [item for item in existing_codes if item != "SN_MISMATCH"]
    if sn_conflict_code and code != "IMAGE_STRONG_RISK":
        code = sn_conflict_code
    home_package_missing = (
        _is_home_appliance_decision(normalized)
        and not as_bool(normalized.get("package_visible"))
        and not _strict_home_no_box_evidence(normalized)
    )
    home_unboxing_product_missing = (
        _is_home_appliance_decision(normalized)
        and is_explicit_false(normalized.get("whole_product_visible"))
    )
    if (home_package_missing or home_unboxing_product_missing) and code not in {
        "INVOICE_ORANGE_WARNING",
        "IMAGE_STRONG_RISK",
        "DUPLICATE_IMAGE_EVIDENCE",
        "PRODUCT_TYPE_MISMATCH",
        "PRODUCT_PHOTO_INVALID",
    }:
        code = "UNBOXING_PHOTO_INVALID"
    verified_home_activation_fallback = sn_already_verified and _is_home_appliance_decision(normalized)
    digital_activation_reason = _digital_activation_evidence_reason(normalized) if sn_already_verified else None
    if digital_activation_reason is not None:
        activation_gate_code = digital_activation_reason
    else:
        activation_gate_code = (
            _verified_sn_activation_form_reason(normalized)
            if sn_already_verified
            else _activation_pass_gate_reason(normalized)
        )
    if digital_activation_reason == "IMAGE_STRONG_RISK" and code != "INVOICE_ORANGE_WARNING":
        code = "IMAGE_STRONG_RISK"
    elif code == "ACTIVATION_PHOTO_INVALID" and not activation_gate_code and (
        verified_home_activation_fallback or not sn_already_verified or digital_activation_reason == ""
    ):
        code = ""
        existing_codes = [item for item in existing_codes if item != "ACTIVATION_PHOTO_INVALID"]
    if not code:
        evidence_type = str(normalized.get("activation_evidence_type") or "").strip().upper()
        if not defer_image_authenticity_to_local and _has_photo_integrity_risk(normalized, evidence_type):
            code = "IMAGE_STRONG_RISK"
        elif existing_codes and not (existing_codes[0] == "ACTIVATION_PHOTO_INVALID" and not activation_gate_code and not sn_already_verified):
            code = existing_codes[0]
        elif as_bool(normalized.get("invoice_orange_warning")):
            code = "INVOICE_ORANGE_WARNING"
        elif all_three_duplicate:
            code = "DUPLICATE_IMAGE_EVIDENCE"
        elif is_explicit_false(normalized.get("product_type_match")) or str(normalized.get("product_type_match") or "").strip().lower() == "mismatch":
            code = "PRODUCT_TYPE_MISMATCH"
        elif is_explicit_false(normalized.get("product_photo_ok")):
            code = "PRODUCT_PHOTO_INVALID"
        elif is_explicit_false(normalized.get("unboxing_photo_ok")):
            code = "UNBOXING_PHOTO_INVALID"
        elif activation_gate_code:
            code = activation_gate_code
        elif (
            digital_activation_reason is None
            and not verified_home_activation_fallback
            and is_explicit_false(normalized.get("activation_photo_ok"))
            and (sn_already_verified or activation_gate_code)
        ):
            code = "ACTIVATION_PHOTO_INVALID"
        elif _confidence_value(normalized.get("sn_confidence")) and _confidence_value(normalized.get("sn_confidence")) < AUTO_PASS_MIN_CONFIDENCE:
            code = "MODEL_UNCERTAIN"
        elif _confidence_value(normalized.get("confidence")) and _confidence_value(normalized.get("confidence")) < AUTO_PASS_MIN_CONFIDENCE:
            code = "MODEL_UNCERTAIN"
        else:
            code = "" if sn_already_verified else (_observed_sn_conflict_reason(normalized) or _activation_pass_gate_reason(normalized))

    if code:
        normalized["manual_required"] = True
        normalized["manual_reason_codes"] = [code]
        if code != original_code:
            normalized["manual_reason"] = reason_code_to_chinese(code)
        else:
            normalized["manual_reason"] = normalized.get("manual_reason") or reason_code_to_chinese(code)
        if address_ok is not None and "address_ok" not in normalized:
            normalized["address_ok"] = address_ok
    else:
        normalized["manual_required"] = False
        normalized["manual_reason_codes"] = []
        normalized["manual_reason"] = ""
        if defer_image_strong_risk:
            normalized["image_risk"] = False
    return normalized


def audit_task_fast(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    allow_review: bool = True,
) -> dict[str, Any]:
    started = time.time()
    order_deadline = _order_deadline(started)
    pre_started = time.time()
    precheck = precheck_task(task)
    pre_elapsed = time.time() - pre_started
    if precheck["manual_required"]:
        row = _final_row(task, precheck, {}, {}, time.time() - started, pre_elapsed, 0.0, 0.0)
        row["strategy"] = "local_precheck"
        row["model_calls"] = 0
        row["total_tokens"] = 0
        return row

    fields = task["fields"]
    sn_payload = build_sn_payload(task, fields, precheck["activation_images"])
    sn_images = _with_detail(precheck["activation_images"], "high")
    sn_result, sn_raw, sn_elapsed, sn_usage, sn_cached = call_model_with_retry(
        base_url,
        api_key,
        model,
        build_sn_prompt(),
        sn_payload,
        sn_images,
        stage="fast_sn",
        cache_dir=cache_dir,
        detail="high",
        timeout_sec=_stage_timeout_from_budget(started),
        order_deadline_at=order_deadline,
    )
    normalized_sn = _normalize_sn_result(fields, sn_result)
    model_calls = 0 if sn_cached else 1
    total_tokens = _usage_total(sn_usage)

    if allow_review and needs_high_detail_review(normalized_sn):
        if _order_deadline_reached(started):
            manual = _order_timeout_manual(precheck)
            row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
            row["strategy"] = "fast_order_timeout_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
            return row
        review_payload = dict(sn_payload)
        review_payload["previous_decision"] = normalized_sn
        review_result, review_raw, review_elapsed, review_usage, review_cached = call_model_with_retry(
            base_url,
            api_key,
            model,
            build_sn_prompt(),
            review_payload,
            sn_images,
            stage="fast_sn_review",
            cache_dir=cache_dir,
            detail="high",
            timeout_sec=_stage_timeout_from_budget(started),
            order_deadline_at=order_deadline,
        )
        normalized_review = _normalize_sn_result(fields, review_result)
        model_calls += 0 if review_cached else 1
        total_tokens += _usage_total(review_usage)
        if as_bool(normalized_review.get("sn_match")):
            normalized_sn = normalized_review
        else:
            manual = {
                "manual_required": True,
                "manual_reason_codes": as_codes(normalized_review.get("manual_reason_code")) or as_codes(normalized_review.get("manual_reason_codes")) or ["SN_NOT_FOUND"],
                "manual_reason": normalized_review.get("manual_reason") or normalized_sn.get("manual_reason") or "楂樼簿搴﹀鏍镐粛鏈瘑鍒埌涓€鑷碨N",
                "address_ok": precheck["address_ok"],
            }
            row = _final_row(task, manual, normalized_review, {}, time.time() - started, pre_elapsed, sn_elapsed + review_elapsed, 0.0)
            row["strategy"] = "fast_sn_review_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {
                "sn_raw": sn_raw,
                "sn_usage": sn_usage,
                "sn_cached": sn_cached,
                "review_raw": review_raw,
                "review_usage": review_usage,
                "review_cached": review_cached,
            }
            return row

    if not as_bool(normalized_sn.get("sn_match")):
        code = normalized_sn.get("manual_reason_code") or ("SN_MISMATCH" if normalized_sn.get("observed_sn") else "SN_NOT_FOUND")
        manual = {
            "manual_required": True,
            "manual_reason_codes": [code],
            "manual_reason": normalized_sn.get("manual_reason") or "婵€娲荤収鐗囧垎缁勬湭璇嗗埆鍒颁竴鑷碨N",
            "address_ok": precheck["address_ok"],
        }
        row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["strategy"] = "fast_sn_manual"
        row["model_calls"] = model_calls
        row["total_tokens"] = total_tokens
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return row

    lightweight_compliance = {
        "manual_required": True,
        "manual_reason_codes": ["MODEL_UNCERTAIN"],
        "manual_reason": "fast模式未完成照片合规审核，不能自动通过",
        "address_ok": precheck["address_ok"],
        "product_type_match": "",
        "product_photo_ok": "",
        "unboxing_photo_ok": "",
        "activation_photo_ok": True,
        "confidence": normalized_sn.get("confidence", ""),
    }
    row = _final_row(task, lightweight_compliance, normalized_sn, lightweight_compliance, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
    row["strategy"] = "fast_sn_only_manual"
    row["model_calls"] = model_calls
    row["total_tokens"] = total_tokens
    row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
    return row


def audit_task_sn_only(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    order_deadline = _order_deadline(started)
    fields = task.get("fields") or {}
    system_sn = str(fields.get("system_sn") or "").strip()
    activation_images = _with_detail(_sn_only_activation_images(task), "high")

    observed_sn = ""
    sn_elapsed = 0.0
    usage: dict[str, Any] = {}
    cached = False
    if system_sn and activation_images:
        observed_sn, sn_elapsed, usage, cached = call_direct_sn_ocr(
            base_url,
            api_key,
            model,
            activation_images,
            cache_dir=cache_dir,
            timeout_sec=_timeout_within_order_deadline(_stage_timeout_from_budget(started), order_deadline),
        )

    normalized_system = normalize_sn(system_sn)
    normalized_observed = "" if observed_sn == "SN_NOT_FOUND" else normalize_sn(observed_sn)
    if not system_sn:
        code = "SYSTEM_SN_MISSING"
        reason_text = "系统SN缺失，无法做SN识别对比"
        sn_match = False
    elif not activation_images:
        code = "ACTIVATION_PHOTO_INVALID"
        reason_text = "SN/激活照片缺失，无法读取SN"
        sn_match = False
    elif not normalized_observed:
        code = "SN_NOT_FOUND"
        reason_text = f"模型未在激活/SN图片中读取到完整SN；系统SN={system_sn}"
        sn_match = False
    elif normalized_observed == normalized_system:
        code = "SN_ONLY_MATCH_NOT_FULL_AUDIT"
        reason_text = f"SN-only模式仅完成SN识别，未执行图片合规审核，不能自动通过；系统SN={system_sn}；模型识别SN={observed_sn}"
        sn_match = True
    else:
        code = "SN_MISMATCH"
        diff = _sn_diff_summary(system_sn, observed_sn)
        reason_text = f"系统SN={system_sn}；模型识别SN={observed_sn}"
        if diff:
            reason_text += f"；差异={diff}"
        sn_match = False

    total_elapsed = time.time() - started
    return {
        "id": task.get("channel_order_no", ""),
        "manual_flag": "是",
        "manual_reason_code": code,
        "manual_reason_cn": reason_code_to_chinese(code) if code in REASON_CODE_CN else code,
        "manual_reason": f"{code}: {reason_text}",
        "business_pass": False,
        "sn_only": True,
        "elapsed_sec": round(total_elapsed, 2),
        "precheck_elapsed_sec": 0.0,
        "sn_elapsed_sec": round(sn_elapsed, 2),
        "compliance_elapsed_sec": 0.0,
        "product_type": fields.get("product_type", ""),
        "source_flow_status": fields.get("source_flow_status", fields.get("flow_status", fields.get("status", ""))),
        "source_examine_status": fields.get("examine_status", ""),
        "source_settle_status": fields.get("settle_status", ""),
        "system_sn": system_sn,
        "observed_sn": observed_sn,
        "sn_match": sn_match,
        "product_type_match": "",
        "address_ok": "",
        "product_photo_ok": "",
        "unboxing_photo_ok": "",
        "activation_photo_ok": "",
        "activation_evidence_type": "",
        "image_risk": "",
        "confidence": "",
        "strategy": "sn_only_direct_ocr",
        "model_calls": 0 if cached else (1 if activation_images and system_sn else 0),
        "total_tokens": _usage_total(usage),
    }


def audit_task_hybrid(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    allow_review: bool = True,
    allow_targeted_review: bool = True,
    sn_policy_version: str | None = None,
    sn_barcode_mode: str | None = None,
    barcode_scanner: BarcodeScanner | None = None,
) -> dict[str, Any]:
    started = time.time()
    order_deadline = _order_deadline(started)
    active_sn_policy = resolve_sn_policy_version(sn_policy_version)
    active_sn_barcode_mode = resolve_sn_barcode_mode(sn_barcode_mode)
    authenticity_config = PhotoAuthenticityConfig.from_env(os.environ)
    digital_activation_mode = resolve_digital_activation_evidence_mode()
    pre_started = time.time()
    precheck = precheck_task(task)
    pre_elapsed = time.time() - pre_started
    if precheck["manual_required"]:
        row = _final_row(task, precheck, {}, {}, time.time() - started, pre_elapsed, 0.0, 0.0)
        row["strategy"] = "local_precheck"
        row["model_calls"] = 0
        row["total_tokens"] = 0
        row["sn_barcode_mode"] = active_sn_barcode_mode
        return finalize_photo_authenticity_report_fields(row, authenticity_config)

    fields = task["fields"]
    sn_images = _with_detail(precheck["activation_images"], "high")
    if active_sn_policy == "v2":
        sn_category = classify_sn_v2_category(
            fields,
            effective_category=precheck["effective_category"],
        )
        sn_payload = build_sn_v2_payload(
            task,
            sn_category,
            precheck["activation_images"],
        )
        if sn_category is SnV2Category.UNSUPPORTED:
            sn_result = {}
            sn_raw = ""
            sn_elapsed = 0.0
            sn_usage = {}
            sn_cached = True
        else:
            sn_result, sn_raw, sn_elapsed, sn_usage, sn_cached = call_model_with_retry(
                base_url,
                api_key,
                model,
                build_sn_v2_prompt(sn_category),
                sn_payload,
                sn_images,
                stage="hybrid_sn_v2",
                cache_dir=cache_dir,
                detail="high",
                timeout_sec=_stage_timeout_from_budget(started),
                retry_timeout_sec=0,
                order_deadline_at=order_deadline,
            )
        sn_decision = decide_sn_v2(
            fields,
            sn_result,
            allowed_image_ids={
                str(image.get("image_id") or "")
                for image in precheck["activation_images"]
            },
            effective_category=precheck["effective_category"],
        )
        sn_decision, sn_barcode_result = apply_barcode_second_check(
            task,
            sn_decision,
            precheck["activation_images"],
            barcode_scanner=barcode_scanner,
            barcode_mode=active_sn_barcode_mode,
        )
        normalized_sn = _normalize_sn_v2_result(sn_result, sn_decision)
    else:
        sn_payload = build_sn_payload(task, fields, precheck["activation_images"])
        sn_result, sn_raw, sn_elapsed, sn_usage, sn_cached = call_model_with_retry(
            base_url,
            api_key,
            model,
            build_sn_prompt(),
            sn_payload,
            sn_images,
            stage="hybrid_sn",
            cache_dir=cache_dir,
            detail="high",
            timeout_sec=_stage_timeout_from_budget(started),
            retry_timeout_sec=0,
            order_deadline_at=order_deadline,
        )
        normalized_sn = _normalize_sn_result(fields, sn_result)
        sn_barcode_result = None
    model_calls = 0 if sn_cached else 1
    total_tokens = _usage_total(sn_usage)

    def attach_sn_barcode_result(row: dict[str, Any]) -> dict[str, Any]:
        row["sn_barcode_mode"] = active_sn_barcode_mode
        if sn_barcode_result is not None:
            row.setdefault("_raw", {})["sn_barcode_result"] = sn_barcode_result
        return row

    if _order_budget_exhausted(started):
        manual = _order_timeout_manual(precheck)
        row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["strategy"] = "hybrid_order_timeout_manual"
        row["model_calls"] = model_calls
        row["total_tokens"] = total_tokens
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return finalize_photo_authenticity_report_fields(attach_sn_barcode_result(row), authenticity_config)

    if active_sn_policy == "v1" and allow_review and needs_high_detail_review(normalized_sn):
        if _order_budget_exhausted(started):
            manual = _order_timeout_manual(precheck)
            row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
            row["strategy"] = "hybrid_order_timeout_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
            return finalize_photo_authenticity_report_fields(row, authenticity_config)
        review_payload = dict(sn_payload)
        review_payload["previous_decision"] = normalized_sn
        review_result, review_raw, review_elapsed, review_usage, review_cached = call_model_with_retry(
            base_url,
            api_key,
            model,
            build_sn_prompt(),
            review_payload,
            sn_images,
            stage="hybrid_sn_review",
            cache_dir=cache_dir,
            detail="high",
            timeout_sec=_stage_timeout_from_budget(started),
            retry_timeout_sec=0,
            order_deadline_at=order_deadline,
        )
        normalized_review = _normalize_sn_result(fields, review_result)
        model_calls += 0 if review_cached else 1
        total_tokens += _usage_total(review_usage)
        if as_bool(normalized_review.get("sn_match")):
            normalized_sn = normalized_review
        else:
            code = normalized_review.get("manual_reason_code") or ("SN_MISMATCH" if normalized_review.get("observed_sn") else "SN_NOT_FOUND")
            manual = {
                "manual_required": True,
                "manual_reason_codes": [code],
                "manual_reason": normalized_review.get("manual_reason") or "SN澶嶆牳鏈€氳繃",
                "address_ok": precheck["address_ok"],
            }
            row = _final_row(task, manual, normalized_review, {}, time.time() - started, pre_elapsed, sn_elapsed + review_elapsed, 0.0)
            row["strategy"] = "hybrid_sn_review_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {
                "sn_raw": sn_raw,
                "sn_usage": sn_usage,
                "sn_cached": sn_cached,
                "review_raw": review_raw,
                "review_usage": review_usage,
                "review_cached": review_cached,
            }
            return finalize_photo_authenticity_report_fields(row, authenticity_config)

    if active_sn_policy == "v1" and allow_targeted_review and _needs_targeted_sn_review(fields, normalized_sn):
        if _order_budget_exhausted(started):
            manual = _order_timeout_manual(precheck, "targeted SN review 前")
            row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
            row["strategy"] = "hybrid_order_timeout_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
            return finalize_photo_authenticity_report_fields(row, authenticity_config)
        target_payload = build_targeted_sn_payload(task, fields, precheck["activation_images"], normalized_sn)
        target_result, target_raw, target_elapsed, target_usage, target_cached = call_model_with_retry(
            base_url,
            api_key,
            model,
            SN_TARGETED_REVIEW_PROMPT,
            target_payload,
            sn_images,
            stage="hybrid_sn_targeted_review",
            cache_dir=cache_dir,
            detail="high",
            timeout_sec=_stage_timeout_from_budget(started),
            retry_timeout_sec=0,
            order_deadline_at=order_deadline,
        )
        normalized_target = _normalize_sn_result(fields, target_result, require_positive_system_match=True)
        model_calls += 0 if target_cached else 1
        total_tokens += _usage_total(target_usage)
        sn_elapsed += target_elapsed
        if as_bool(normalized_target.get("sn_match")):
            normalized_sn = normalized_target
        else:
            code = normalized_target.get("manual_reason_code") or ("SN_MISMATCH" if normalized_target.get("observed_sn") else "MODEL_UNCERTAIN")
            manual = {
                "manual_required": True,
                "manual_reason_codes": [code],
                "manual_reason": normalized_target.get("manual_reason") or "targeted SN review did not confirm system SN",
                "address_ok": precheck["address_ok"],
            }
            row = _final_row(task, manual, normalized_target, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
            row["strategy"] = "hybrid_sn_targeted_review_manual"
            row["model_calls"] = model_calls
            row["total_tokens"] = total_tokens
            row["_raw"] = {
                "sn_raw": sn_raw,
                "sn_usage": sn_usage,
                "sn_cached": sn_cached,
                "target_review_raw": target_raw,
                "target_review_usage": target_usage,
                "target_review_cached": target_cached,
            }
            return finalize_photo_authenticity_report_fields(row, authenticity_config)

    if not as_bool(normalized_sn.get("sn_match")):
        code = normalized_sn.get("manual_reason_code") or ("SN_MISMATCH" if normalized_sn.get("observed_sn") else "SN_NOT_FOUND")
        manual = {
            "manual_required": True,
            "manual_reason_codes": [code],
            "manual_reason": normalized_sn.get("manual_reason") or "婵€娲荤収鐗囧垎缁勬湭璇嗗埆鍒颁竴鑷碨N",
            "address_ok": precheck["address_ok"],
        }
        row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["strategy"] = "hybrid_sn_v2_manual" if active_sn_policy == "v2" else "hybrid_sn_manual"
        row["model_calls"] = model_calls
        row["total_tokens"] = total_tokens
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return finalize_photo_authenticity_report_fields(attach_sn_barcode_result(row), authenticity_config)

    if _order_budget_exhausted(started):
        manual = _order_timeout_manual(precheck)
        row = _final_row(task, manual, normalized_sn, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["strategy"] = "hybrid_order_timeout_manual"
        row["model_calls"] = model_calls
        row["total_tokens"] = total_tokens
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return finalize_photo_authenticity_report_fields(attach_sn_barcode_result(row), authenticity_config)

    compliance_payload = {
        "id": task["channel_order_no"],
        "product_type": fields.get("product_type", ""),
        "category_name": category_name_from_fields(fields),
        "effective_category": precheck["effective_category"],
        "is_home_appliance": precheck["effective_category"] == "home_appliance",
        "address_ok": precheck["address_ok"],
        "sn_match": True,
        "observed_sn": normalized_sn.get("observed_sn") or fields.get("system_sn", ""),
        "raw_observed_sn": normalized_sn.get("raw_observed_sn", ""),
        "visual_sn_ambiguity": as_bool(normalized_sn.get("visual_sn_ambiguity")),
        "image_groups": {
            title: [{"image_id": image.get("image_id"), "title": image.get("title"), "url": image.get("source_url")} for image in images]
            for title, images in precheck["groups"].items()
        },
    }
    compliance_images = _all_grouped_images(precheck["groups"], activation_detail="high", other_detail="low")
    edge_mapping_mode = (
        resolve_photo_auth_edge_mapping_mode()
        if authenticity_config.mode != "off"
        else "off"
    )
    model_compliance_images, compliance_payload = prepare_photo_auth_edge_mapping_inputs(
        compliance_images,
        compliance_payload,
        mode=edge_mapping_mode,
        output_dir=cache_dir or PROJECT_ROOT / "reports" / "model_audit" / "cache_v2",
    )
    photo_auth_edge_candidates = compliance_payload.get("photo_auth_edge_candidates")
    effective_edge_mapping_mode = "on" if photo_auth_edge_candidates else "off"
    compliance_prompt = compliance_prompt_for_category(
        precheck["effective_category"],
        product_type=fields.get("product_type", ""),
        include_photo_authenticity=authenticity_config.mode != "off",
        replace_legacy_authenticity_adjudication=authenticity_config.mode == "enforce",
        photo_auth_edge_mapping_mode=effective_edge_mapping_mode,
        digital_activation_evidence_mode=digital_activation_mode,
    )
    compliance, compliance_raw, compliance_elapsed, compliance_usage, compliance_cached = call_model_with_retry(
        base_url,
        api_key,
        model,
        compliance_prompt,
        compliance_payload,
        model_compliance_images,
        stage="hybrid_compliance",
        cache_dir=cache_dir,
        detail="low",
        timeout_sec=_stage_timeout_from_budget(started),
        retry_timeout_sec=0,
        order_deadline_at=order_deadline,
    )
    model_calls += 0 if compliance_cached else 1
    total_tokens += _usage_total(compliance_usage)
    compliance = apply_photo_auth_edge_candidate_reviews(compliance, photo_auth_edge_candidates)
    model_effective_category = compliance.get("effective_category", "")
    compliance["model_effective_category"] = model_effective_category
    compliance["product_type"] = fields.get("product_type", "")
    compliance["category_name"] = category_name_from_fields(fields)
    compliance["effective_category"] = precheck["effective_category"]
    compliance["is_home_appliance"] = precheck["effective_category"] == "home_appliance"
    compliance["system_sn"] = fields.get("system_sn", "")
    compliance["normalized_system_sn"] = normalize_sn(fields.get("system_sn", ""))
    compliance["imei1"] = fields.get("imei1", "")
    compliance["imei2"] = fields.get("imei2", "")
    compliance["sn_confidence"] = normalized_sn.get("confidence", "")
    compliance["_sn_already_verified_by_system"] = True
    compliance["digital_activation_evidence_mode"] = digital_activation_mode
    compliance["_activation_image_ids"] = [
        str(image.get("image_id") or "") for image in precheck["activation_images"]
    ]
    compliance["_exact_duplicate_image_groups"] = exact_duplicate_image_groups(precheck["groups"])
    compliance = enforce_photo_noncompliance_manual(
        compliance,
        address_ok=precheck["address_ok"],
        defer_image_authenticity_to_local=authenticity_config.mode == "enforce",
    )

    row = _final_row(task, compliance, normalized_sn, compliance, time.time() - started, pre_elapsed, sn_elapsed, compliance_elapsed)
    authenticity_postprocess_tokens = 0
    if authenticity_config.mode != "off":
        row["merged_compliance_total_tokens"] = _usage_total(compliance_usage)
        row["merged_compliance_total_elapsed_sec"] = round(compliance_elapsed, 4)
    fallback_raw: dict[str, Any] = {}
    fallback_usage: dict[str, Any] = {}
    fallback_cached = False

    if authenticity_config.mode != "off" and row.get("manual_flag") == "否":
        authenticity_started = time.time()
        row = apply_photo_authenticity_gate(
            legacy_row=row,
            compliance=compliance,
            images=compliance_images,
            config=authenticity_config,
            fallback=None,
            budget_available=lambda: not _order_budget_exhausted(started),
        )
        row["elapsed_sec"] = round(time.time() - started, 2)
        row["compliance_elapsed_sec"] = round(compliance_elapsed, 2)
        row["photo_authenticity_postprocess_elapsed_sec"] = round(time.time() - authenticity_started, 4)
    row["photo_authenticity_postprocess_tokens"] = authenticity_postprocess_tokens
    row["strategy"] = (
        "hybrid_sn_v2_then_compliance"
        if active_sn_policy == "v2"
        else "hybrid_sn_then_compliance"
    )
    row["model_calls"] = model_calls
    row["total_tokens"] = total_tokens
    row["_raw"] = {
        "sn_raw": sn_raw,
        "sn_usage": sn_usage,
        "sn_cached": sn_cached,
        "compliance_raw": compliance_raw,
        "compliance_usage": compliance_usage,
        "compliance_cached": compliance_cached,
    }
    attach_sn_barcode_result(row)
    if authenticity_config.mode != "off":
        row["_raw"].update({
            "photo_authenticity_fallback_raw": fallback_raw,
            "photo_authenticity_fallback_usage": fallback_usage,
            "photo_authenticity_fallback_cached": fallback_cached,
        })
    return finalize_photo_authenticity_report_fields(row, authenticity_config)


def audit_task_v2(
    base_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    order_deadline = _order_deadline(started)
    digital_activation_mode = resolve_digital_activation_evidence_mode()
    pre_started = time.time()
    precheck = precheck_task(task)
    pre_elapsed = time.time() - pre_started
    if precheck["manual_required"]:
        return _final_row(task, precheck, {}, {}, time.time() - started, pre_elapsed, 0.0, 0.0)

    fields = task["fields"]
    sn_payload = build_sn_payload(task, fields, precheck["activation_images"])
    sn_result, sn_raw, sn_elapsed, sn_usage, sn_cached = call_model_with_retry(
        base_url,
        api_key,
        model,
        build_sn_prompt(),
        sn_payload,
        precheck["activation_images"],
        stage="sn",
        cache_dir=cache_dir,
        timeout_sec=_stage_timeout_from_budget(started),
        order_deadline_at=order_deadline,
    )
    normalized_sn_result = _normalize_sn_result(fields, sn_result)
    if not as_bool(normalized_sn_result.get("sn_match")):
        code = normalized_sn_result.get("manual_reason_code") or "SN_NOT_FOUND"
        manual = {
            "manual_required": True,
            "manual_reason_codes": [code],
            "manual_reason": normalized_sn_result.get("manual_reason") or "激活照片分组未识别到一致SN",
            "address_ok": precheck["address_ok"],
        }
        row = _final_row(task, manual, normalized_sn_result, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return row

    if _order_deadline_reached(started):
        manual = _order_timeout_manual(precheck)
        row = _final_row(task, manual, normalized_sn_result, {}, time.time() - started, pre_elapsed, sn_elapsed, 0.0)
        row["strategy"] = "v2_order_timeout_manual"
        row["model_calls"] = 0 if sn_cached else 1
        row["total_tokens"] = _usage_total(sn_usage)
        row["_raw"] = {"sn_raw": sn_raw, "sn_usage": sn_usage, "sn_cached": sn_cached}
        return row

    all_images = flatten_image_groups(precheck["groups"])
    compliance_payload = {
        "id": task["channel_order_no"],
        "product_type": fields.get("product_type", ""),
        "category_name": category_name_from_fields(fields),
        "effective_category": precheck["effective_category"],
        "is_home_appliance": precheck["effective_category"] == "home_appliance",
        "address_ok": precheck["address_ok"],
        "sn_match": True,
        "observed_sn": normalized_sn_result.get("observed_sn") or fields.get("system_sn", ""),
        "raw_observed_sn": normalized_sn_result.get("raw_observed_sn", ""),
        "visual_sn_ambiguity": as_bool(normalized_sn_result.get("visual_sn_ambiguity")),
        "image_groups": {
            title: [{"image_id": image.get("image_id"), "title": image.get("title"), "url": image.get("source_url")} for image in images]
            for title, images in precheck["groups"].items()
        },
    }
    compliance, compliance_raw, compliance_elapsed, compliance_usage, compliance_cached = call_model_with_retry(
        base_url,
        api_key,
        model,
        compliance_prompt_for_category(
            precheck["effective_category"],
            product_type=fields.get("product_type", ""),
            digital_activation_evidence_mode=digital_activation_mode,
        ),
        compliance_payload,
        all_images,
        stage="compliance",
        cache_dir=cache_dir,
        timeout_sec=_stage_timeout_from_budget(started),
        order_deadline_at=order_deadline,
    )
    model_effective_category = compliance.get("effective_category", "")
    compliance["model_effective_category"] = model_effective_category
    compliance["product_type"] = fields.get("product_type", "")
    compliance["category_name"] = category_name_from_fields(fields)
    compliance["effective_category"] = precheck["effective_category"]
    compliance["is_home_appliance"] = precheck["effective_category"] == "home_appliance"
    compliance["system_sn"] = fields.get("system_sn", "")
    compliance["normalized_system_sn"] = normalize_sn(fields.get("system_sn", ""))
    compliance["imei1"] = fields.get("imei1", "")
    compliance["imei2"] = fields.get("imei2", "")
    compliance["sn_confidence"] = normalized_sn_result.get("confidence", "")
    compliance["_sn_already_verified_by_system"] = True
    compliance["digital_activation_evidence_mode"] = digital_activation_mode
    compliance["_activation_image_ids"] = [
        str(image.get("image_id") or "") for image in precheck["activation_images"]
    ]
    compliance["_exact_duplicate_image_groups"] = exact_duplicate_image_groups(precheck["groups"])
    compliance = enforce_photo_noncompliance_manual(compliance, address_ok=precheck["address_ok"])
    row = _final_row(task, compliance, normalized_sn_result, compliance, time.time() - started, pre_elapsed, sn_elapsed, compliance_elapsed)
    row["_raw"] = {
        "sn_raw": sn_raw,
        "sn_usage": sn_usage,
        "sn_cached": sn_cached,
        "compliance_raw": compliance_raw,
        "compliance_usage": compliance_usage,
        "compliance_cached": compliance_cached,
    }
    return row


def _final_row(
    task: dict[str, Any],
    decision: dict[str, Any],
    sn_result: dict[str, Any],
    compliance: dict[str, Any],
    total_elapsed: float,
    pre_elapsed: float,
    sn_elapsed: float,
    compliance_elapsed: float,
) -> dict[str, Any]:
    fields = task.get("fields") or {}
    trusted_category = effective_product_category(fields)
    display_decision = dict(compliance)
    display_decision.setdefault("system_sn", fields.get("system_sn", ""))
    display_decision.setdefault("imei1", fields.get("imei1", ""))
    display_decision.setdefault("imei2", fields.get("imei2", ""))
    if trusted_category != "unknown":
        display_decision["effective_category"] = trusted_category
    else:
        display_decision.setdefault("effective_category", trusted_category)
    sn_display_decision = {
        **sn_result,
        "system_sn": fields.get("system_sn", ""),
        "imei1": fields.get("imei1", ""),
        "imei2": fields.get("imei2", ""),
        "effective_category": trusted_category,
    }
    authoritative_sn = _authoritative_home_appliance_sn(sn_display_decision)
    manual = as_bool(decision.get("manual_required"))
    reason_codes = as_codes(decision.get("manual_reason_codes"))
    reason_text = str(decision.get("manual_reason") or "")
    if authoritative_sn and "SN_MISMATCH" in reason_codes:
        reason_codes = [code for code in reason_codes if code != "SN_MISMATCH"]
        reason_text = ""
        if not reason_codes:
            manual = False
    reason = "" if not manual else ";".join(reason_codes) + ((": " + reason_text) if reason_text else "")
    primary_reason_code = reason_codes[0] if manual and reason_codes else ""
    reason_cn = build_chinese_reason(reason_codes, reason_text) if manual else ""
    conflict_sn = (
        _conflicting_observed_sn(display_decision)
        if primary_reason_code == "SN_MISMATCH" and not authoritative_sn
        else ""
    )
    observed_sn = sn_result.get("observed_sn") or fields.get("system_sn", "") if as_bool(sn_result.get("sn_match")) else sn_result.get("observed_sn", "")
    sn_match = as_bool(sn_result.get("sn_match")) if sn_result else False
    if authoritative_sn:
        observed_sn = authoritative_sn
        sn_match = True
    elif conflict_sn:
        observed_sn = conflict_sn
        sn_match = False
    row = {
        "id": task.get("channel_order_no", ""),
        "manual_flag": "是" if manual else "否",
        "manual_reason_code": primary_reason_code,
        "manual_reason_cn": reason_cn,
        "manual_reason": reason,
        "elapsed_sec": round(total_elapsed, 2),
        "precheck_elapsed_sec": round(pre_elapsed, 4),
        "sn_elapsed_sec": round(sn_elapsed, 2),
        "compliance_elapsed_sec": round(compliance_elapsed, 2),
        "product_type": fields.get("product_type", ""),
        "source_flow_status": fields.get("source_flow_status", fields.get("flow_status", fields.get("status", ""))),
        "source_examine_status": fields.get("examine_status", ""),
        "source_settle_status": fields.get("settle_status", ""),
        "system_sn": fields.get("system_sn", ""),
        "observed_sn": observed_sn,
        "sn_match": sn_match,
        "sn_char_review_mode": resolve_sn_char_review_mode(),
        "sn_barcode_mode": resolve_sn_barcode_mode(),
        "sn_label_auth_review_mode": resolve_sn_label_auth_review_mode(),
        "digital_activation_evidence_mode": resolve_digital_activation_evidence_mode(),
        "product_type_match": compliance.get("product_type_match", ""),
        "address_ok": decision.get("address_ok", compliance.get("address_ok", "")),
        "product_photo_ok": compliance.get("product_photo_ok", ""),
        "unboxing_photo_ok": compliance.get("unboxing_photo_ok", ""),
        "activation_photo_ok": compliance.get("activation_photo_ok", ""),
        "activation_evidence_type": compliance.get("activation_evidence_type", ""),
        "image_risk": compliance.get("image_risk", ""),
        "confidence": compliance.get("confidence", sn_result.get("confidence", "")),
    }
    row.update(PHOTO_AUTHENTICITY_REPORT_DEFAULTS)
    return row


def audit_task_path(
    index: int,
    total: int,
    task_path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    mode: str,
    cache_dir: Path,
    allow_review: bool,
    allow_targeted_review: bool,
    sn_barcode_mode: str | None = None,
) -> tuple[int, dict[str, Any]]:
    task = json.loads(task_path.read_text(encoding="utf-8-sig"))
    task_started = time.time()
    authenticity_config = PhotoAuthenticityConfig.from_env(
        os.environ if mode == "hybrid" else {"PHOTO_AUTHENTICITY_MODE": "off"}
    )
    try:
        if mode == "fast":
            result = audit_task_fast(
                base_url,
                api_key,
                model,
                task,
                cache_dir=cache_dir,
                allow_review=allow_review,
            )
        elif mode == "sn_only":
            result = audit_task_sn_only(
                base_url,
                api_key,
                model,
                task,
                cache_dir=cache_dir,
            )
        elif mode == "hybrid":
            result = audit_task_hybrid(
                base_url,
                api_key,
                model,
                task,
                cache_dir=cache_dir,
                allow_review=allow_review,
                allow_targeted_review=allow_targeted_review,
                sn_barcode_mode=sn_barcode_mode,
            )
        else:
            result = audit_task_v2(base_url, api_key, model, task, cache_dir=cache_dir)
    except Exception as exc:
        elapsed = time.time() - task_started
        result = _final_row(
            task,
            {"manual_required": True, "manual_reason_codes": ["MODEL_UNCERTAIN"], "manual_reason": f"{type(exc).__name__}: {exc}"},
            {},
            {},
            elapsed,
            0,
            0,
            0,
        )
        result["strategy"] = "error_to_manual"
        result["model_calls"] = 1
        result["total_tokens"] = 0
        result["_error"] = traceback.format_exc()
        result = finalize_photo_authenticity_report_fields(result, authenticity_config)
    return index, {"task": task, "result": result, "total": total}


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default=os.environ.get("VISION_MODEL_NAME", "gpt-5.5"))
    parser.add_argument("--cache-dir", default="reports/model_audit/cache_v2")
    parser.add_argument("--mode", choices=["fast", "hybrid", "v2", "sn_only"], default="hybrid")
    parser.add_argument(
        "--sn-policy-version",
        choices=["v1", "v2"],
        default=os.environ.get("SN_POLICY_VERSION", "v1"),
        help="hybrid模式SN策略；默认v1，v2仅用于显式对比测试",
    )
    parser.add_argument(
        "--sn-barcode-mode",
        choices=["off", "shadow", "enforce"],
        default=os.environ.get("SN_BARCODE_MODE", "enforce"),
        help="SN条码/二维码二次确认插件；默认enforce参与救回，可设shadow只记录",
    )
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--no-targeted-sn-review", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--sn-char-review-mode",
        choices=["off", "on", "v2"],
        default=os.environ.get("SN_CHAR_REVIEW_MODE", "off"),
        help="SN相似字符逐字复核提示词插件；on为v1，v2为高精度字形版，默认off",
    )
    parser.add_argument(
        "--sn-label-auth-review-mode",
        choices=["off", "on"],
        default=os.environ.get("SN_LABEL_AUTH_REVIEW_MODE", "off"),
        help="SN/条码标签非实拍合规提示词插件；默认off，可显式设为on开启",
    )
    parser.add_argument(
        "--photo-auth-edge-mapping-mode",
        choices=["off", "on"],
        default=os.environ.get("PHOTO_AUTH_EDGE_MAPPING_MODE", "off"),
        help="图片真实性边缘与外部屏幕映射实验插件；默认off，可显式设为on开启",
    )
    parser.add_argument(
        "--digital-activation-evidence-mode",
        choices=["off", "on"],
        default=os.environ.get("DIGITAL_ACTIVATION_EVIDENCE_MODE", "on"),
        help="普通3C激活证据统一口径插件；默认on，可显式设为off关闭",
    )
    parser.add_argument(
        "--photo-authenticity-mode",
        choices=["off", "shadow", "enforce"],
        default=os.environ.get("PHOTO_AUTHENTICITY_MODE", "enforce"),
        help="图片真实性审核模式；默认enforce可转人工，shadow只记录，off关闭",
    )
    parser.add_argument(
        "--photo-authenticity-artifact-dir",
        default=os.environ.get("PHOTO_AUTHENTICITY_ARTIFACT_DIR"),
        help="可覆盖FFT模型目录；阈值仍强制读取并校验冻结metadata中的0.995",
    )
    parser.add_argument(
        "--photo-authenticity-local-tree-enabled",
        choices=["true", "false"],
        default=os.environ.get("PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED", "false"),
        help="图片真实性本地树融合规则；默认false关闭，可显式设true用于实验/回放",
    )
    parser.add_argument(
        "--photo-authenticity-baseline-path",
        default=os.environ.get("PHOTO_AUTHENTICITY_BASELINE_PATH"),
        help="可选历史同类订单基线JSON，用于计算合并后的真实增量；缺失时增量标记不可用",
    )
    args = parser.parse_args(argv)
    try:
        resolve_sn_char_review_mode(args.sn_char_review_mode)
    except ValueError as exc:
        parser.error(str(exc))
    if args.mode == "sn_only" and args.sn_char_review_mode != "off":
        parser.error("SN character review plugins are not applied in sn_only mode")
    if args.mode != "hybrid" and args.photo_auth_edge_mapping_mode != "off":
        parser.error("photo authenticity edge mapping plugin is only applied in hybrid mode")
    if args.photo_auth_edge_mapping_mode != "off" and args.photo_authenticity_mode == "off":
        parser.error("photo authenticity edge mapping plugin requires photo authenticity mode shadow or enforce")
    return args


def main() -> None:
    configure_utf8_stdio()
    args = parse_cli_args()
    os.environ["SN_CHAR_REVIEW_MODE"] = args.sn_char_review_mode
    os.environ["SN_POLICY_VERSION"] = args.sn_policy_version
    os.environ["SN_BARCODE_MODE"] = args.sn_barcode_mode
    os.environ["SN_LABEL_AUTH_REVIEW_MODE"] = args.sn_label_auth_review_mode
    os.environ["PHOTO_AUTH_EDGE_MAPPING_MODE"] = args.photo_auth_edge_mapping_mode
    os.environ["DIGITAL_ACTIVATION_EVIDENCE_MODE"] = args.digital_activation_evidence_mode
    os.environ["PHOTO_AUTHENTICITY_MODE"] = args.photo_authenticity_mode
    os.environ["PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED"] = args.photo_authenticity_local_tree_enabled
    if args.photo_authenticity_artifact_dir:
        os.environ["PHOTO_AUTHENTICITY_ARTIFACT_DIR"] = args.photo_authenticity_artifact_dir
    if args.photo_authenticity_baseline_path:
        os.environ["PHOTO_AUTHENTICITY_BASELINE_PATH"] = args.photo_authenticity_baseline_path

    verify_photo_authenticity_local_tree_artifact(PhotoAuthenticityConfig.from_env(os.environ))

    base_url = os.environ["VISION_API_BASE_URL"]
    api_key = os.environ["VISION_API_KEY"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"gpt55_guobu_audit_{args.mode}_{stamp}.json"
    csv_path = out_dir / f"gpt55_guobu_audit_{args.mode}_{stamp}.csv"
    partial_jsonl_path = out_dir / f"gpt55_guobu_audit_{args.mode}_{stamp}.jsonl"

    rows: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    task_paths = sorted(Path(args.tasks_dir).glob("*.json"))

    futures = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for index, task_path in enumerate(task_paths, 1):
            futures.append(
                executor.submit(
                    audit_task_path,
                    index,
                    len(task_paths),
                    task_path,
                    base_url=base_url,
                    api_key=api_key,
                    model=args.model,
                    mode=args.mode,
                    cache_dir=cache_dir,
                    allow_review=not args.no_review,
                    allow_targeted_review=not args.no_targeted_sn_review,
                    sn_barcode_mode=args.sn_barcode_mode,
                )
            )

        for future in as_completed(futures):
            index, payload = future.result()
            task = payload["task"]
            result = payload["result"]
            print(f"[{index}/{len(task_paths)}] {task['channel_order_no']}", flush=True)
            raw = {key: value for key, value in result.items() if key.startswith("_")}
            row = {key: value for key, value in result.items() if not key.startswith("_")}
            prepare_photo_authenticity_report_fields(row)
            rows.append(row)
            full.append({"task": task, "row": row, **raw})
            with partial_jsonl_path.open("a", encoding="utf-8") as partial_file:
                partial_file.write(json.dumps({"task": task, "row": row, **raw}, ensure_ascii=False) + "\n")
            print(f" -> {row['manual_flag']} {str(row['manual_reason'])[:100]} {row['elapsed_sec']}s", flush=True)

    json_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[key for key, _label in CSV_COLUMNS])
        writer.writerow({key: label for key, label in CSV_COLUMNS})
        writer.writerows(rows)
    print("JSON=" + str(json_path))
    print("CSV=" + str(csv_path))
    print("PARTIAL_JSONL=" + str(partial_jsonl_path))
    print("COUNT=" + str(len(rows)))
    print("TOTAL_SECONDS=" + str(round(sum(float(row["elapsed_sec"]) for row in rows), 2)))
    print("PHOTO_AUTHENTICITY_SUMMARY=" + json.dumps(summarize_photo_authenticity(rows), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
