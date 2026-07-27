# -*- coding: utf-8 -*-
"""
PaddleOCR 封装引擎
适配 PaddleOCR 3.5+ 新 API（使用 predict() 替代废弃的 ocr()）
"""
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from paddleocr import PaddleOCR

from config import OCR_CONFIG, OCR_ENHANCED_CONFIG

logger = logging.getLogger("ocr_engine")


class OCREngine:
    """PaddleOCR 封装，提供通用文字识别和增强小字体识别"""

    _instance = None          # 通用中文（480）
    _enhanced_instance = None # 增强中文（640）
    _tiled_instance = None    # 分块专用（960）

    @classmethod
    def get_instance(cls, config: Optional[dict] = None) -> "PaddleOCR":
        """获取 OCR 实例（单例模式，避免重复加载模型）

        用 text_det_limit_side_len 区分：
        - 默认/480 → 通用中文实例
        - 640+     → 增强中文实例（更高分辨率读小字）
        """
        if config is None:
            config = OCR_CONFIG
        # 增强实例：分辨率 >= 640
        if config.get("text_det_limit_side_len", 480) >= 640:
            if cls._enhanced_instance is None:
                logger.info("初始化增强 OCR 引擎（中文 640 模式）")
                cls._enhanced_instance = PaddleOCR(**config)
            return cls._enhanced_instance
        else:
            if cls._instance is None:
                logger.info("初始化通用 OCR 引擎（中文模式）")
                cls._instance = PaddleOCR(**config)
            return cls._instance

    @classmethod
    def get_tiled_instance(cls, max_dim: int = 960) -> "PaddleOCR":
        """获取分块 OCR 专用实例（独立于增强模式，分辨率更高）"""
        if cls._tiled_instance is None:
            config = {**OCR_ENHANCED_CONFIG, "text_det_limit_side_len": max_dim}
            logger.info(f"初始化分块 OCR 引擎（{max_dim}px）")
            cls._tiled_instance = PaddleOCR(**config)
        return cls._tiled_instance

    @staticmethod
    def _parse_predict_result(result) -> list[dict]:
        texts = []
        for page_result in result:
            rec_texts = page_result.get("rec_texts", [])
            rec_scores = page_result.get("rec_scores", [])
            dt_polys = page_result.get("dt_polys", [])

            for i in range(len(rec_texts)):
                texts.append({
                    "text": rec_texts[i],
                    "confidence": float(rec_scores[i]) if i < len(rec_scores) else 0.0,
                    "box": dt_polys[i].tolist() if i < len(dt_polys) else None,
                })
        return texts

    @staticmethod
    def _resize_if_needed(image_path: str | Path, max_dim: int = 480) -> np.ndarray:
        """如果图片尺寸过大，先缩小到 max_dim 以内，防止 PaddleOCR 内存崩溃"""
        # 用 imdecode 支持中文路径
        img = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("无法读取图片")

        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            logger.info(f"图片过大 ({w}x{h})，已缩小至 {new_w}x{new_h}")
        return img

    def extract_text(self, image_path: str | Path) -> list[dict]:
        """通用文字提取

        Args:
            image_path: 图片路径

        Returns:
            [{"text": str, "confidence": float, "box": [[x,y],...]}, ...]
        """
        ocr = self.get_instance(OCR_CONFIG)
        img = self._resize_if_needed(image_path)
        result = ocr.predict(img)
        return self._parse_predict_result(result)

    def extract_text_enhanced(self, image_path: str | Path) -> list[dict]:
        """增强模式 - 针对小字体（SN/IMEI/产品标签）

        使用 640px 和宽松检测阈值，提高小字识别率（快速路径）
        不额外做 CLAHE/锐化预处理（实测 CLAHE + 2x upscale 反而降低准确率）
        """
        ocr = self.get_instance(OCR_ENHANCED_CONFIG)

        # 先缩小过大图片（640px 保留小字细节 + 保证速度）
        img = self._resize_if_needed(image_path, max_dim=640)

        # 使用预处理后的图像进行 OCR
        result = ocr.predict(img)
        return self._parse_predict_result(result)

    def extract_text_tiled(
        self, image_path: str | Path,
        tile_cols: int = 2, tile_rows: int = 2,
        overlap: float = 0.10, max_dim: int = 960
    ) -> list[dict]:
        """分块 OCR：大图切成小块分别识别，保留小字细节

        适合高分辨率图片（3000x4000），全局缩放后小字（SN 标签）消失的情况。
        使用 960px 分辨率和 2×2 分块，平衡速度和召回率。
        在增强模式（640px）未命中时触发，作为补扫路径。

        Args:
            image_path: 图片路径
            tile_cols: 水平切分数（默认 2）
            tile_rows: 垂直切分数（默认 2）
            overlap: 块间重叠比例（默认 0.10）
            max_dim: 每块最大边长（默认 960）

        Returns:
            [{"text": str, "confidence": float, "box": [[x,y],...]}, ...]
        """

        img = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("无法读取图片")

        h, w = img.shape[:2]
        # 分块使用独立的高分辨率实例（960px），保留小字细节
        ocr = self.get_tiled_instance(max_dim)
        all_texts = []
        seen = set()

        tile_w = w // tile_cols
        tile_h = h // tile_rows
        overlap_x = int(tile_w * overlap)
        overlap_y = int(tile_h * overlap)

        for row in range(tile_rows):
            for col in range(tile_cols):
                x1 = max(0, col * tile_w - overlap_x // 2)
                y1 = max(0, row * tile_h - overlap_y // 2)
                x2 = min(w, (col + 1) * tile_w + overlap_x // 2)
                y2 = min(h, (row + 1) * tile_h + overlap_y // 2)

                tile = img[y1:y2, x1:x2]
                if tile.shape[0] < 30 or tile.shape[1] < 30:
                    continue

                try:
                    result = ocr.predict(tile)
                    texts = self._parse_predict_result(result)
                    for item in texts:
                        if item["text"] not in seen:
                            seen.add(item["text"])
                            all_texts.append(item)
                except Exception:
                    pass

        return all_texts

    def extract_text_sn_regions(self, image_path: str | Path, max_dim: int = 960) -> list[dict]:
        """SN 区域补扫：优先扫底部 35%，并补扫 90/270 度旋转后的底部区域。"""
        img = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("无法读取图片")

        ocr = self.get_tiled_instance(max_dim)
        all_texts = []
        seen = set()

        def bottom_region(source: np.ndarray) -> np.ndarray:
            h = source.shape[0]
            y1 = int(h * 0.65)
            return source[y1:h, :]

        regions = [
            ("bottom_35", bottom_region(img)),
            ("bottom_35_rot90", bottom_region(cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE))),
            ("bottom_35_rot270", bottom_region(cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE))),
        ]

        for region_name, region in regions:
            if region.shape[0] < 30 or region.shape[1] < 30:
                continue
            try:
                prepared = self._preprocess_small_text_image(region)
                result = ocr.predict(prepared)
                texts = self._parse_predict_result(result)
                for item in texts:
                    key = (region_name, item.get("text"))
                    if key in seen:
                        continue
                    seen.add(key)
                    item["region"] = region_name
                    all_texts.append(item)
            except Exception as exc:
                logger.warning("SN 区域补扫失败: %s %s", region_name, exc)

        return all_texts

    @staticmethod
    def _preprocess_small_text_image(img: np.ndarray) -> np.ndarray:
        """对小字体图像进行预处理（放大 + 对比度增强 + 锐化）
        接收已缩放过后的图像，确保最终尺寸不超过 3000
        """
        h, w = img.shape[:2]

        # 如果原图已经够大，不做 2x 放大（防止最终超限导致崩溃）
        if max(h, w) >= 1200:
            scale = 1.0
        else:
            scale = 2.0
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

        # 转为灰度
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # CLAHE 对比度增强
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # 锐化
        sharpen_kernel = np.array([
            [-1, -1, -1],
            [-1,  9, -1],
            [-1, -1, -1],
        ], dtype=np.float32)
        sharpened = cv2.filter2D(enhanced, -1, sharpen_kernel)

        # 转回 BGR（PaddleOCR 需要 3 通道）
        result = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)
        return result

    @staticmethod
    def extract_text_from_image(img: np.ndarray) -> list[dict]:
        """直接从 numpy 图像数组提取文字（通用模式）"""
        ocr = OCREngine.get_instance(OCR_CONFIG)
        result = ocr.predict(img)
        return OCREngine._parse_predict_result(result)
