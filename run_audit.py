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
from pathlib import Path

# 修复 Windows GBK 编码问题
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 确保能导入 modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import LOGS_DIR, LOG_LEVEL, LOG_FORMAT, TEMP_DIR
from modules import (
    OCREngine,
    IDCardParser,
    CodeExtractor,
    ImageForensics,
    AddressChecker,
    RuleEngine,
    classify_audit_category,
)
from modules.report_writer import append_report_row
from modules.audit_models import normalize_decision
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
        "found_sns",
        "found_imeis",
        "match_details",
        "rules",
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


def parse_page_text(text: str) -> dict:
    """从原始页面文本中解析各字段"""
    result = {}
    if not text:
        return result

    # 姓名 (2-10个中文字符)
    m = re.search(r'姓名[：:\s]*([^\s]{2,10})', text)
    if m:
        result['name'] = m.group(1).strip()

    # SN码: 支持 SN码/sn码/序列号/Serial
    m = re.search(r'(?:SN[码]?|sn[码]?|序列号|产品序列号|Serial|S/N)[：:\s]*([A-Za-z0-9\-]{6,30})', text)
    if m:
        result['sn'] = m.group(1).strip()

    # IMEI1 / IMEI2
    m = re.search(r'IMEI1[：:\s]*(\d{15})', text, re.IGNORECASE)
    if m:
        result['imei1'] = m.group(1)
    m = re.search(r'IMEI2[：:\s]*(\d{15})', text, re.IGNORECASE)
    if m:
        result['imei2'] = m.group(1)

    # 地址: 从"地址"往后抓，直到遇到下一个字段名或行尾
    m = re.search(r'地址[：:\s]*(.{5,}?)(?=\s*(?:IMEI|类型|SN|产品|$))', text, re.DOTALL)
    if m:
        result['address'] = m.group(1).strip()

    # 产品类型: 优先匹配完整标签，避免"品类类型"被"品类"提前匹配
    m = re.search(r'产品类型[：:\s]*([^\s]{2,20})', text)
    if not m:
        m = re.search(r'品类类型[：:\s]*([^\s]{2,20})', text)
    if not m:
        m = re.search(r'类型[：:\s]*([^\s]{2,20})', text)
    if m:
        result['product_type'] = m.group(1).strip()

    safe_result = {
        key: ("[REDACTED]" if key in {"name", "address", "id_number", "phone"} else redact_text(value))
        for key, value in result.items()
    }
    logging.getLogger("audit").info("从页面文本解析到: %s", safe_result)
    return result


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
    return _process_images(images, system_data, scene=scene)


def run_audit_with_images(images: list[Path], system_data: dict, scene: str = "guobu") -> dict:
    """从图片路径列表执行审核"""
    return _process_images(images, system_data, scene=scene)


def _process_images(images: list[Path], system_data: dict, scene: str = "guobu") -> dict:
    """执行完整的审核流程

    Args:
        images: 图片路径列表
        system_data: 系统数据字典
        scene: 审核场景（guobu / no_coupon）

    Returns:
        审核结论字典
    """
    logger = logging.getLogger("audit")

    # 1. 判断产品类型
    is_home_appliance, is_3c_product = determine_product_type(system_data, scene_hint=scene)
    logger.info(f"场景={scene} 产品类型: 家电={is_home_appliance}, 3C={is_3c_product}")

    # 2. 初始化引擎
    ocr_engine = OCREngine()
    forensics_engine = ImageForensics()

    # 3. 对每张图片进行鉴伪 + OCR
    all_forensics = []
    all_ocr_texts = []

    for i, img_path in enumerate(images):
        logger.info("处理图片 [%s/%s]", i + 1, len(images))

        # 图像鉴伪
        forensics_result = forensics_engine.full_analysis(img_path)
        all_forensics.append(forensics_result)
        logger.info(f"  鉴伪: {forensics_result['status']} - {forensics_result['message']}")

        # OCR 通用识别
        ocr_texts = ocr_engine.extract_text(img_path)
        all_ocr_texts.append(ocr_texts)
        logger.info(f"  OCR: {len(ocr_texts)} 个文本块")

    # 4. 增强 OCR（所有图片都用增强模式提取 SN/IMEI）
    logger.info("提取 SN/IMEI 码...")
    all_enhanced_texts = []
    for img_path in images:
        enhanced = ocr_engine.extract_text_enhanced(img_path)
        all_enhanced_texts.append(enhanced)

    # 合并所有 OCR 结果
    combined_product_texts = []
    for et in all_enhanced_texts:
        combined_product_texts.extend(et)
    for ot in all_ocr_texts:
        combined_product_texts.extend(ot)

    # SN 匹配
    sn_result = CodeExtractor.match_system_sn(
        combined_product_texts, system_data.get("sn", "")
    )
    logger.info("  SN匹配状态: %s", bool(sn_result.get("sn_match")))

    # 分块 OCR 补扫
    if not sn_result["sn_match"] and system_data.get("sn"):
        logger.info("  SN 未匹配，触发分块 OCR 补扫...")
        for img_path in images:
            tiled = ocr_engine.extract_text_tiled(img_path)
            combined_product_texts.extend(tiled)
        sn_result = CodeExtractor.match_system_sn(
            combined_product_texts, system_data.get("sn", "")
        )
        logger.info("  分块补扫后SN匹配状态: %s", bool(sn_result.get("sn_match")))

    # IMEI 匹配
    imei_result = CodeExtractor.match_system_imei(
        combined_product_texts,
        system_data.get("imei1", ""),
        system_data.get("imei2", ""),
    )
    logger.info("  IMEI匹配状态: %s", imei_result.get("status"))

    # 5. 按场景走不同分支
    if scene == "guobu":
        # === 国补流程 ===
        # 5a. 地址检查（家电类）
        address_result = None
        if is_home_appliance:
            logger.info("检查地址合规（家电类）...")
            address_result = AddressChecker.validate_address(
                combined_product_texts, system_data.get("address")
            )
            logger.info("  地址精度状态: %s", address_result.get("status"))

        # 5c. 规则引擎（国补版）
        logger.info("执行国补规则引擎...")
        final_result = RuleEngine.evaluate_guobu(
            system_data=system_data,
            sn_result=sn_result,
            forensics_results=all_forensics,
            address_result=address_result,
            is_home_appliance=is_home_appliance,
        )

        # 国补输出
        output = {
            "decision": final_result["decision"],
            "skip_reason": final_result.get("skip_reason"),
            "scene": "guobu",
            "codes": {
                "sn": sn_result,
                "imei": imei_result,
            },
            "image_forensics": {
                "status": "pass" if all(f["status"] == "pass" for f in all_forensics) else "suspicious",
                "per_image": [
                    {
                        "name": images[i].name,
                        "status": all_forensics[i]["status"],
                        "message": all_forensics[i]["message"],
                    }
                    for i in range(len(images))
                ],
            },
            "address_check": address_result,
            "rules": final_result["details"],
        }

    else:
        # === 非发券流程（含身份证）===
        id_card_ocr_texts = all_ocr_texts[0] if all_ocr_texts else []

        # 身份证信息解析
        logger.info("解析身份证信息...")
        id_card_info = IDCardParser.parse(id_card_ocr_texts)
        logger.info("  身份证姓名已识别: %s", bool(id_card_info.get("name")))
        logger.info("  身份证号已识别: %s", bool(id_card_info.get("id_number")))
        logger.info("  身份证有效状态: %s", bool(id_card_info.get("is_valid")))

        # 地址检查（家电类）
        address_result = None
        if is_home_appliance:
            logger.info("检查地址合规（家电类）...")
            address_result = AddressChecker.validate_address(
                combined_product_texts, system_data.get("address")
            )
            logger.info("  地址精度状态: %s", address_result.get("status"))

        # 规则引擎（含身份证）
        logger.info("执行非发券规则引擎...")
        final_result = RuleEngine.evaluate(
            system_data=system_data,
            id_card_info=id_card_info,
            sn_result=sn_result,
            imei_result=imei_result,
            forensics_results=all_forensics,
            address_result=address_result,
            is_home_appliance=is_home_appliance,
            is_3c_product=is_3c_product,
            ocr_texts_per_image=all_enhanced_texts,
        )

        output = {
            "decision": final_result["decision"],
            "skip_reason": final_result.get("skip_reason"),
            "scene": "no_coupon",
            "id_card": {
                "name_recognized": bool(id_card_info.get("name")),
                "is_valid": id_card_info.get("is_valid"),
            },
            "codes": {
                "sn": sn_result,
                "imei": imei_result,
            },
            "image_forensics": {
                "status": "pass" if all(f["status"] == "pass" for f in all_forensics) else "suspicious",
                "per_image": [
                    {
                        "name": images[i].name,
                        "status": all_forensics[i]["status"],
                        "message": all_forensics[i]["message"],
                    }
                    for i in range(len(images))
                ],
            },
            "address_check": address_result,
            "rules": final_result["details"],
        }

    logger.info(f"最终判定: {output['decision']}")
    return output


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("main")

    try:
        args = parse_args()

        # === 获取请求数据 ===
        system_data = {}
        image_urls = []

        # 优先级1: --request_file (JSON文件，包含所有数据)
        if args.request_file:
            with open(args.request_file, "r", encoding="utf-8") as f:
                req = json.load(f)
            system_data = req.get("system_data", {})
            image_urls = req.get("image_urls", [])
            if not system_data and req.get("page_text"):
                system_data = parse_page_text(req["page_text"])

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

        if image_urls:
            logger.info(f"开始审核: {len(image_urls)} 张图片来自 URL, scene={args.scene}")
            images = download_images_from_urls(image_urls)
            try:
                result = run_audit_with_images(images, system_data, scene=args.scene)
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
                f"scene={args.scene}"
            )
            result = run_audit(args.images_dir, system_data, scene=args.scene)
        else:
            raise ValueError("请提供 --images_dir 或 --image_urls")

        if result.get("decision") != "engine_error":
            result["decision"] = normalize_decision(result.get("decision"))

        if args.report:
            append_report_row(args.report, {
                "jl_order_no": system_data.get("jl_order_no") or system_data.get("order_id", ""),
                "channel_order_no": system_data.get("channel_order_no", ""),
                "scene": result.get("scene", args.scene),
                "category": system_data.get("product_type", ""),
                "decision": result.get("decision"),
                "path": result.get("path", ""),
                "elapsed_sec": result.get("elapsed_sec", ""),
                "manual_reason": result.get("manual_reason") or result.get("skip_reason"),
                "sn_match": result.get("codes", {}).get("sn", {}).get("sn_match", ""),
                "image_roles_ok": "",
                "real_photo_pass": result.get("image_forensics", {}).get("status") == "pass",
                "id_name_match": "",
                "id_valid": result.get("id_card", {}).get("is_valid", ""),
                "address_detail_ok": result.get("address_check", {}).get("status") == "pass"
                if result.get("address_check")
                else "",
            })

        safe_result = sanitize_audit_output(result)
        output_json = json.dumps(safe_result, ensure_ascii=False, indent=2, default=str)
        print(output_json)

        # 写入输出文件（可选）
        if args.output_file:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(output_json)
            logger.info(f"结果已写入: {args.output_file}")

        # 退出码：pass=0, skip=0（影刀通过 JSON 判断）, 引擎异常=3
        if result["decision"] == "engine_error":
            sys.exit(3)
        sys.exit(0)

    except Exception as e:
        error_result = {
            "decision": "engine_error",
            "skip_reason": f"引擎异常: {redact_text(e)}",
        }
        print(json.dumps(error_result, ensure_ascii=True, default=str))
        logger.error("审核异常: %s", redact_text(e))
        logger.debug("审核异常详情", exc_info=True)
        sys.exit(3)


if __name__ == "__main__":
    main()
