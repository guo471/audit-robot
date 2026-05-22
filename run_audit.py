#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
消费券合规审核机器人 - 主入口
影刀通过命令行调用此脚本

用法:
    python run_audit.py --page_text "<页面原始文本>" --image_urls "<图片URL列表>"
    python run_audit.py --images_dir <图片目录> --system_data '<系统数据JSON>'
    python run_audit.py --request_file temp/request.json

返回:
    JSON 格式的审核结论
"""
import argparse
import json
import logging
import logging.handlers
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

# 修复 Windows GBK 编码问题
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 确保能导入 modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import AUDIT_ORDER_TIMEOUT_SEC, LOGS_DIR, LOG_LEVEL, LOG_FORMAT, TEMP_DIR
from modules import classify_audit_category
from modules.report_writer import append_report_row
from modules.audit_models import AuditImage, AuditRequest, normalize_decision
from modules.audit_runner import audit_request
from modules.page_parser import merge_page_text_fields, parse_page_text
from modules.privacy import redact_text, remove_temp_dir


def parse_image_urls(raw: str) -> list[str]:
    """解析图片URL列表，支持多种格式"""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        return json.loads(raw)
    if "|" in raw:
        return [u.strip() for u in raw.split("|") if u.strip()]
    if "," in raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return [raw]


def sanitize_output_text(value) -> str:
    text = redact_text(value)
    text = re.sub(r"(系统[:：=]\s*)[^\s;；，,。]+", r"\1[CODE]", text)
    text = re.sub(r"(OCR识别结果[:：]\s*)\[[^\]]*\]", r"\1[CODE_LIST]", text)
    text = re.sub(r"\b(?:SN|IMEI)\s*[=:：]\s*[A-Za-z0-9\-]{6,30}\b", "[CODE]", text, flags=re.IGNORECASE)
    return text


def sanitize_audit_output(value):
    sensitive_keys = {
        "id_number",
        "valid_from",
        "valid_to",
        "address",
        "name",
        "ocr_texts",
    }
    if isinstance(value, dict):
        return {
            key: sanitize_audit_output(item)
            for key, item in value.items()
            if key not in sensitive_keys
        }
    if isinstance(value, list):
        return [sanitize_audit_output(item) for item in value]
    if isinstance(value, str):
        return sanitize_output_text(value)
    return value


def setup_logging():
    """配置日志（限制 10MB，保留最近 3 份）"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "audit.log", maxBytes=10_485_760, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        handlers=[handler, logging.StreamHandler()],
    )


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="消费券合规审核引擎")
    parser.add_argument(
        "--images_dir",
        help="包含用户上传图片的目录路径（与 --image_urls 二选一）",
    )
    parser.add_argument(
        "--image_urls",
        help='图片URL列表：JSON数组 或 | 或 , 分隔的字符串，如 "url1|url2|url3"',
    )
    parser.add_argument(
        "--system_data",
        help='系统数据的 JSON 字符串，例: \'{"name":"张三","sn":"..."}\'',
    )
    parser.add_argument(
        "--system_data_file",
        help="系统数据的 JSON 文件路径（与 --system_data 二选一）",
    )
    parser.add_argument(
        "--page_text",
        help="页面原始文本（影刀获取元素文本内容后传入），引擎自动解析各字段",
    )
    parser.add_argument(
        "--request_file",
        help="请求JSON文件路径（包含 page_text / system_data / image_urls）",
    )
    parser.add_argument(
        "--output_file",
        help="将结果输出到指定文件（可选）",
    )
    parser.add_argument("--report", help="将脱敏审核结果追加写入指定 CSV 报表")
    parser.add_argument(
        "--scene",
        choices=["guobu", "no_coupon"],
        default="guobu",
        help='审核场景：guobu（默认，国补）/ no_coupon（非发券）',
    )
    return parser.parse_args()


def load_images(images_dir: str) -> list[Path]:
    """加载目录中的图片文件"""
    supported_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
    img_dir = Path(images_dir)

    if not img_dir.exists():
        raise FileNotFoundError(f"图片目录不存在: {images_dir}")

    images = []
    for f in sorted(img_dir.iterdir()):
        if f.suffix.lower() in supported_exts:
            images.append(f)

    if not images:
        raise FileNotFoundError(f"目录中未找到支持的图片: {images_dir}")

    logging.info("加载了 %s 张图片", len(images))
    return images


def download_images_from_urls(image_urls: list[str]) -> list[Path]:
    """从 URL 列表下载图片到临时目录"""
    import urllib.request
    import uuid

    temp_dir = TEMP_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for i, url in enumerate(image_urls):
        ext = ".jpg"
        # 从 URL 判断扩展名
        for known_ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
            if known_ext in url.lower():
                ext = known_ext
                break

        save_path = temp_dir / f"{i+1}{ext}"
        try:
            urllib.request.urlretrieve(url, save_path)
            saved_paths.append(save_path)
            logging.info("已下载图片 %s/%s", i + 1, len(image_urls))
        except Exception as e:
            raise RuntimeError(f"下载图片 {i+1} 失败: {e}")

    if not saved_paths:
        raise RuntimeError("没有成功下载任何图片")

    return saved_paths


def determine_product_type(system_data: dict, scene_hint: str = "") -> tuple[bool, bool]:
    """根据系统数据判断产品类别

    Returns:
        (is_home_appliance, is_3c_product)
    """
    category = classify_audit_category(scene_hint, system_data)
    return category.category == "home_appliance", category.category == "3c"


def run_audit(images_dir: str, system_data: dict, scene: str = "guobu") -> dict:
    """从目录加载图片并执行审核"""
    images = load_images(images_dir)
    return run_audit_with_images(images, system_data, scene=scene)


def run_audit_with_images(images: list[Path], system_data: dict, scene: str = "guobu") -> dict:
    """从图片路径列表执行审核"""
    request = build_audit_request(images, system_data, scene)
    return run_audit_request(request)


def run_audit_request(request: AuditRequest) -> dict:
    response = audit_request(request, timeout_sec=AUDIT_ORDER_TIMEOUT_SEC)
    return response.to_dict()


def build_audit_request(images: list[Path], system_data: dict, scene: str) -> AuditRequest:
    """Build the single internal request used by both CLI and service paths."""
    fields = dict(system_data or {})
    jl_order_no = str(fields.get("jl_order_no") or fields.get("order_id") or "")
    channel_order_no = str(fields.get("channel_order_no") or "")
    audit_images = [
        AuditImage(title=path.stem, path=str(path))
        for path in images
    ]
    return AuditRequest(
        jl_order_no=jl_order_no,
        channel_order_no=channel_order_no,
        scene_hint=scene,
        fields=fields,
        images=audit_images,
    )


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("main")

    try:
        args = parse_args()

        # === 获取请求数据 ===
        system_data = {}
        image_urls = []
        request_payload = None
        scene = args.scene

        # 优先级1: --request_file (JSON文件，包含所有数据)
        if args.request_file:
            with open(args.request_file, "r", encoding="utf-8") as f:
                req = json.load(f)
            if "fields" in req or "images" in req or "scene_hint" in req:
                request_payload = AuditRequest.from_dict(req)
                if request_payload.page_text:
                    request_payload = replace(
                        request_payload,
                        fields=merge_page_text_fields(request_payload.page_text, request_payload.fields),
                    )
                system_data = request_payload.system_data()
                scene = request_payload.scene_hint or scene
            else:
                system_data = req.get("system_data", {})
                scene = req.get("scene_hint") or req.get("scene") or scene
            image_urls = req.get("image_urls", [])
            if req.get("page_text"):
                system_data = merge_page_text_fields(req["page_text"], system_data)

        # 优先级2: --system_data / --system_data_file
        if not system_data:
            if args.system_data_file:
                with open(args.system_data_file, "r", encoding="utf-8") as f:
                    system_data = json.load(f)
            elif args.system_data:
                system_data = json.loads(args.system_data)
            elif args.page_text:
                # 从页面文本解析
                system_data = parse_page_text(args.page_text)

        if not system_data:
            raise ValueError("请提供 --system_data / --system_data_file / --page_text / --request_file")

        # === 获取图片 ===
        if args.image_urls:
            image_urls = parse_image_urls(args.image_urls)

        if not image_urls and args.request_file:
            # image_urls already loaded from request_file above
            pass

        if request_payload is not None and request_payload.images:
            logger.info("开始审核: request_file 已提供图片, scene=%s", scene)
            result = run_audit_request(request_payload)
        elif image_urls:
            logger.info(f"开始审核: {len(image_urls)} 张图片来自 URL, scene={scene}")
            images = download_images_from_urls(image_urls)
            try:
                result = run_audit_with_images(images, system_data, scene=scene)
            finally:
                # 用完即删临时图片
                temp_dir = images[0].parent
                if temp_dir.exists():
                    remove_temp_dir(temp_dir)
                    logger.info("临时图片已清理")
        elif args.images_dir:
            logger.info(
                f"开始审核: 图片目录已提供, "
                f"order={redact_text(system_data.get('order_id', 'unknown'))}, "
                f"scene={scene}"
            )
            result = run_audit(args.images_dir, system_data, scene=scene)
        else:
            logger.info("未提供图片，按人工处理")
            if request_payload is not None:
                result = run_audit_request(request_payload)
            else:
                result = run_audit_with_images([], system_data, scene=scene)

        if result.get("decision") != "engine_error":
            result["decision"] = normalize_decision(result.get("decision"))

        if args.report:
            append_report_row(args.report, {
                "jl_order_no": system_data.get("jl_order_no") or system_data.get("order_id", ""),
                "channel_order_no": system_data.get("channel_order_no", ""),
                "scene": result.get("scene", scene),
                "category": system_data.get("product_type", ""),
                "decision": result.get("decision"),
                "path": result.get("path", ""),
                "elapsed_sec": result.get("elapsed_sec", ""),
                "manual_reason": result.get("manual_reason") or result.get("skip_reason"),
                "sn_match": result.get("evidence", {}).get("sn_match", ""),
                "image_roles_ok": result.get("evidence", {}).get("image_roles_ok", ""),
                "real_photo_pass": result.get("evidence", {}).get("real_photo_pass", ""),
                "id_name_match": result.get("evidence", {}).get("id_name_match", ""),
                "id_valid": result.get("evidence", {}).get("id_valid", ""),
                "address_detail_ok": result.get("evidence", {}).get("address_detail_ok", ""),
            })

        safe_result = sanitize_audit_output(result)
        output_json = json.dumps(safe_result, ensure_ascii=False, indent=2, default=str)
        print(output_json)

        # 写入输出文件（可选）
        if args.output_file:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(output_json)
            logger.info(f"结果已写入: {args.output_file}")

        sys.exit(0)

    except Exception as e:
        error_result = {
            "decision": "manual",
            "action": "next",
            "manual_reason": "引擎异常，转人工",
        }
        print(json.dumps(error_result, ensure_ascii=True, default=str))
        logger.error("审核异常: %s", redact_text(e))
        logger.debug("审核异常详情", exc_info=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
