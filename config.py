# -*- coding: utf-8 -*-
"""审核机器人配置文件"""
from pathlib import Path

# 路径配置
PROJECT_DIR = Path("C:/audit_robot")
TEMP_DIR = PROJECT_DIR / "temp"
LOGS_DIR = PROJECT_DIR / "logs"
TEST_SAMPLES_DIR = PROJECT_DIR / "test_samples"

# PaddleOCR 配置
# 注意: 强制用 PP-OCRv4 mobile 模型（比 v5 server 快 20-50 倍）
#       关掉了文档展平（产品标签不需要），进一步提速
OCR_CONFIG = {
    "lang": "ch",
    "ocr_version": "PP-OCRv4",
    "device": "cpu",
    "use_doc_unwarping": False,
    "use_textline_orientation": True,
    "text_det_limit_side_len": 480,
    "text_det_box_thresh": 0.6,
    "text_det_thresh": 0.3,
}

# 增强 OCR（小字体 SN/IMEI 检测）
# 用中文模型（混合中英文+数字效果远好于纯英文模型）
OCR_ENHANCED_CONFIG = {
    "lang": "ch",
    "ocr_version": "PP-OCRv4",
    "device": "cpu",
    "use_doc_unwarping": False,
    "use_textline_orientation": True,
    "text_det_limit_side_len": 640,  # 640 保留小字细节，实测 480 下 SN 会误读
    "text_det_box_thresh": 0.3,
    "text_det_thresh": 0.2,
    "text_det_unclip_ratio": 2.0,
}

# --- 图像鉴伪阈值 ---

# FFT 摩尔纹检测
# 注意：合成测试图会导致高分。实际部署时用真实照片校准
FFT_MOIRE_THRESHOLD = 0.83  # > 此值判定为有摩尔纹（0.83 避免实拍误报）

# 翻拍检测：边缘均匀度阈值
# 翻拍屏幕的图像整体清晰度均匀（边缘CV低）+ 高梯度占比高
SCREEN_UNIFORM_EDGE_CV = 0.6     # 边缘CV低于此值 + 高梯度比高于阈值 → 翻拍
SCREEN_UNIFORM_HIGH_GRAD = 0.3   # 高梯度像素占比高于此值
# 水平边缘偏斜阈值（屏幕扫描线导致水平边缘异常多）
SCREEN_HV_RATIO = 2.5            # 水平/垂直边缘比高于此值 + 高梯度比高于阈值 → 翻拍
SCREEN_HV_HIGH_GRAD = 0.5        # 配合 HV 比的高梯度阈值

# ELA 检测
ELA_THRESHOLD = 0.10  # > 此值判定为可疑

# 噪点一致性检测
# 注意：JPEG压缩会天然导致较高的不一致性分数。真实拼接图通常 > 3.0
# 建议用真实翻拍/拼接照片校准此值
NOISE_INCONSISTENCY_THRESHOLD = 4.2  # > 此值判定为拼接（调高避免 JPEG 误报）

# 清晰度检测（Laplacian 方差）
BLUR_THRESHOLD = 80.0  # < 此值判定为模糊

# --- 地址合规 ---
HOME_APPLIANCE_KEYWORDS = [
    "村", "组", "屯", "队", "社",
    "号", "栋", "单元", "室", "房",
    "户", "庄", "寨", "坝",
]

# 地址最少字符数
ADDRESS_MIN_LENGTH = 15

# --- 规则引擎 ---
OCR_CONFIDENCE_THRESHOLD = 0.70  # OCR 置信度低于此值转人工

# 日志配置
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Audit service and report configuration
AUDIT_SERVICE_HOST = "127.0.0.1"
AUDIT_SERVICE_PORT = 8765
AUDIT_SERVICE_TOKEN_ENV = "AUDIT_SERVICE_TOKEN"
AUDIT_ORDER_TIMEOUT_SEC = 60
REPORTS_DIR = PROJECT_DIR / "reports"
AUDIT_REPORT_PATH = REPORTS_DIR / "audit_report.csv"
