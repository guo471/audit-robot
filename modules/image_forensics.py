# -*- coding: utf-8 -*-
"""
图像鉴伪模块
多阶段流水线：EXIF → FFT 摩尔纹 → ELA → 噪点一致性 → 清晰度
所有分析均在本地完成，零 API 成本
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import piexif
from PIL import Image, ImageChops

from config import (
    FFT_MOIRE_THRESHOLD,
    ELA_THRESHOLD,
    NOISE_INCONSISTENCY_THRESHOLD,
    BLUR_THRESHOLD,
    SCREEN_UNIFORM_EDGE_CV,
    SCREEN_UNIFORM_HIGH_GRAD,
    SCREEN_HV_RATIO,
    SCREEN_HV_HIGH_GRAD,
)

logger = logging.getLogger("image_forensics")


class ImageForensics:
    """图像鉴伪引擎"""

    # 已知的编辑软件列表
    EDITING_SOFTWARE = [
        "photoshop", "lightroom", "snapseed", "picsart",
        "gimp", "affinity", "pixelmator", "canva",
        "美图秀秀", "醒图", "pixlr", "fotor",
    ]

    @staticmethod
    def _imread_unicode(path: str | Path) -> np.ndarray:
        """支持中文路径的 cv2.imread 替代方案"""
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)

    @classmethod
    def analyze_exif(cls, image_path: str | Path) -> dict:
        """阶段 1: EXIF 元数据分析"""
        flags = []
        info = {}

        try:
            img = Image.open(str(image_path))
            exif_data = img.getexif()
            from PIL.ExifTags import TAGS as EXIF_TAGS
            exif_dict = {EXIF_TAGS.get(k, k): v for k, v in exif_data.items()}

            # 检查编辑软件
            software = str(exif_dict.get("Software", "")).lower()
            for tool in cls.EDITING_SOFTWARE:
                if tool in software:
                    flags.append(f"检测到编辑软件: {software}")

            # 检查相机信息
            make = str(exif_dict.get("Make", ""))
            model = str(exif_dict.get("Model", ""))
            if not make and not model:
                flags.append("无相机信息（可能非原始拍摄）")
            else:
                info["camera"] = f"{make} {model}".strip()

            # 时间戳一致性
            dt_original = exif_dict.get("DateTimeOriginal", "")
            dt_digitized = exif_dict.get("DateTimeDigitized", "")
            dt_modified = exif_dict.get("DateTime", "")
            if dt_original and dt_modified and dt_original != dt_modified:
                flags.append(f"时间戳不一致: 原始={dt_original}, 修改={dt_modified}")

            # 低 dpi 检测（截图特征）
            x_res = exif_dict.get("XResolution", 0)
            if isinstance(x_res, tuple) and len(x_res) == 2:
                dpi = x_res[0] / x_res[1] if x_res[1] else 0
                if dpi <= 72:
                    flags.append(f"低分辨率({int(dpi)}dpi)，可能为屏幕截图")

            # 通过 piexif 获取更多元数据
            try:
                piexif_dict = piexif.load(str(image_path))
                if "0th" in piexif_dict:
                    ifth = piexif_dict["0th"]
                    if piexif.ImageIFD.Software in ifth:
                        sw = ifth[piexif.ImageIFD.Software].decode("utf-8", errors="ignore")
                        if any(t.lower() in sw.lower() for t in cls.EDITING_SOFTWARE):
                            flags.append(f"EXIF编辑记录: {sw}")
            except Exception:
                pass

        except Exception as e:
            flags.append(f"EXIF 读取异常: {e}")

        info["flags"] = flags
        info["flag_count"] = len(flags)
        info["suspicious"] = len(flags) > 0

        return info

    @classmethod
    def detect_moire_fft(cls, image_path: str | Path) -> dict:
        """阶段 2: FFT 摩尔纹检测（基于径向频谱分析）

        摩尔纹 vs 文本边缘的关键区别：
        - 摩尔纹：频谱中有**窄带周期性峰值**，在特定半径形成尖峰
        - 文本边缘：频谱能量**宽泛分布**，无规律性峰值

        算法：径向频谱分析 + 一维 FFT 周期性检测
        """
        try:
            img = cls._imread_unicode(image_path)
            if img is None:
                raise ValueError("无法读取图片")
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # 缩放到统一尺寸
            img = cv2.resize(img, (1024, 1024))
            h, w = img.shape

            # 汉宁窗减少边缘伪影
            hanning = np.outer(np.hanning(h), np.hanning(w))
            img_windowed = img.astype(np.float64) * hanning

            # 2D FFT
            f = np.fft.fft2(img_windowed)
            fshift = np.fft.fftshift(f)
            magnitude = np.abs(fshift)
            magnitude_norm = magnitude / (np.max(magnitude) + 1e-10)

            cy, cx = h // 2, w // 2
            y_grid, x_grid = np.ogrid[:h, :w]
            max_radius = min(h, w) // 2

            # 计算径向频谱分布（每个半径上的平均值）
            radial_means = []
            radial_stds = []
            radial_maxs = []

            for r in range(5, max_radius, 2):
                outer = ((y_grid - cy) ** 2 + (x_grid - cx) ** 2) <= (r + 1) ** 2
                inner = ((y_grid - cy) ** 2 + (x_grid - cx) ** 2) >= (r - 1) ** 2
                ring_mask = outer & inner
                if np.any(ring_mask):
                    vals = magnitude_norm[ring_mask]
                    radial_means.append(float(np.mean(vals)))
                    radial_stds.append(float(np.std(vals)))
                    radial_maxs.append(float(np.max(vals)))

            if len(radial_means) < 10:
                return {"moire_score": 0.0, "is_moire": False, "peak_evidence": 0}

            means = np.array(radial_means)
            maxs = np.array(radial_maxs)

            # 指标1: 峰值锐度（peak-to-background ratio）
            # 取中位数做背景估计（更鲁棒）
            background = np.median(means[5:])
            peak_ratio = maxs / (background + 1e-10)
            mean_peak_ratio = float(np.mean(peak_ratio[5:]))

            # 指标2: 一维径向FFT周期性检测
            radial_fft = np.abs(np.fft.fft(means))
            radial_fft_norm = radial_fft / (radial_fft[0] + 1e-10)
            if len(radial_fft_norm) > 5:
                periodicity = float(np.max(radial_fft_norm[3: len(radial_fft_norm)//4]))
            else:
                periodicity = 0.0

            # 指标3: 频谱熵（低熵 = 能量集中 = 可能摩尔纹）
            spec_norm = means / (np.sum(means) + 1e-10)
            spec_entropy = -np.sum(spec_norm * np.log(spec_norm + 1e-10))
            max_entropy = np.log(len(spec_norm) + 1)
            entropy_ratio = spec_entropy / max_entropy if max_entropy > 0 else 1.0

            # 综合评分
            pea = min(mean_peak_ratio / 4.0, 1.0) * 0.30
            per = min(periodicity * 5.0, 1.0) * 0.45
            ent = (1.0 - entropy_ratio) * 0.25
            moire_score = pea + per + ent

            is_moire = moire_score > FFT_MOIRE_THRESHOLD

            return {
                "moire_score": round(moire_score, 4),
                "periodicity": round(periodicity, 4),
                "entropy_ratio": round(entropy_ratio, 4),
                "is_moire": is_moire,
                "message": "检测到摩尔纹（疑似翻拍屏幕）" if is_moire else "未检测到摩尔纹",
            }

        except Exception:
            logger.error("FFT 摩尔纹检测失败")
            return {"moire_score": 0.0, "is_moire": False, "error": "FFT 摩尔纹检测失败"}

    @classmethod
    def analyze_ela(cls, image_path: str | Path, quality: int = 90) -> dict:
        """阶段 3: Error Level Analysis"""
        try:
            original = Image.open(str(image_path)).convert("RGB")
            temp_path = str(image_path) + "_ela_temp.jpg"

            original.save(temp_path, quality=quality)
            compressed = Image.open(temp_path)

            diff = ImageChops.difference(original, compressed)
            diff_array = np.array(diff, dtype=np.float32)

            ela_score = float(np.mean(diff_array) / 255.0)

            # 局部差异分析
            h, w = diff_array.shape[:2]
            block_size = 64
            high_diff_blocks = 0
            total_blocks = 0
            max_block_diff = 0.0

            for y in range(0, h - block_size, block_size // 2):
                for x in range(0, w - block_size, block_size // 2):
                    block = diff_array[y:y + block_size, x:x + block_size]
                    block_mean = float(np.mean(block) / 255.0)
                    total_blocks += 1
                    if block_mean > ela_score * 2:
                        high_diff_blocks += 1
                    max_block_diff = max(max_block_diff, block_mean)

            anomaly_ratio = high_diff_blocks / max(total_blocks, 1)

            try:
                os.remove(temp_path)
            except Exception:
                pass

            is_tampered = ela_score > ELA_THRESHOLD and anomaly_ratio > 0.1

            return {
                "ela_score": round(ela_score, 4),
                "max_block_diff": round(max_block_diff, 4),
                "anomaly_ratio": round(anomaly_ratio, 4),
                "is_tampered": is_tampered,
                "message": "ELA 检测到异常区域（疑似拼接/PS）" if is_tampered else "ELA 无异常",
            }

        except Exception:
            logger.error("ELA 分析失败")
            return {"ela_score": 0.0, "is_tampered": False, "error": "ELA 分析失败"}

    @classmethod
    def analyze_noise_consistency(cls, image_path: str | Path) -> dict:
        """阶段 4: 噪点一致性分析"""
        try:
            img = cls._imread_unicode(image_path)
            if img is None:
                raise ValueError("无法读取图片")
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            denoised = cv2.medianBlur(img, 5)
            noise = cv2.subtract(img.astype(np.float32), denoised.astype(np.float32))

            h, w = noise.shape
            block_size = 64
            local_vars = []

            for y in range(0, h - block_size, block_size // 2):
                for x in range(0, w - block_size, block_size // 2):
                    block = noise[y:y + block_size, x:x + block_size]
                    local_vars.append(float(np.var(block)))

            if not local_vars:
                return {"inconsistency_score": 0.0, "is_spliced": False}

            cv_score = float(np.std(local_vars) / np.mean(local_vars)) if np.mean(local_vars) > 0 else 0.0
            is_spliced = cv_score > NOISE_INCONSISTENCY_THRESHOLD

            return {
                "inconsistency_score": round(cv_score, 4),
                "is_spliced": is_spliced,
                "message": "噪点不一致（可能为拼接图片）" if is_spliced else "噪点分布一致",
            }

        except Exception:
            logger.error("噪点一致性分析失败")
            return {"inconsistency_score": 0.0, "is_spliced": False, "error": "噪点一致性分析失败"}

    @classmethod
    def detect_blur(cls, image_path: str | Path) -> dict:
        """阶段 5: 清晰度检测（Laplacian 方差法）"""
        try:
            img = cls._imread_unicode(image_path)
            if img is None:
                raise ValueError("无法读取图片")
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            laplacian = cv2.Laplacian(img, cv2.CV_64F)
            variance = float(laplacian.var())
            is_blurry = variance < BLUR_THRESHOLD

            return {
                "sharpness_score": round(variance, 2),
                "is_blurry": is_blurry,
                "message": f"图片模糊（评分: {variance:.1f})" if is_blurry else "图片清晰",
            }

        except Exception:
            logger.error("清晰度检测失败")
            return {"sharpness_score": 0.0, "is_blurry": False, "error": "清晰度检测失败"}

    @classmethod
    def detect_screen_uniformity(cls, image_path: str | Path) -> dict:
        """翻拍屏幕检测：边缘均匀度分析

        翻拍屏幕的关键特征：整张图都是屏幕内容，边缘/细节分布过于均匀。
        实拍照片有景深/背景虚化，边缘集中在主体上，分布不均匀。

        返回：uniform_score 越低表示越均匀（越可疑）
        """
        try:
            img = cls._imread_unicode(image_path)
            if img is None:
                raise ValueError("无法读取图片")
            if len(img.shape) == 3:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                gray = img

            h, w = gray.shape

            # 1. Sobel 边缘检测（水平 + 垂直）
            sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            grad_mag = np.sqrt(sobel_x**2 + sobel_y**2)

            h_edge = float(np.mean(np.abs(sobel_x)))
            v_edge = float(np.mean(np.abs(sobel_y)))
            hv_ratio = h_edge / (v_edge + 1e-6)

            # 2. 高梯度像素占比（翻拍屏幕通常处处清晰）
            high_grad_ratio = float(np.sum(grad_mag > 30)) / (h * w)

            # 3. 分块边缘强度变异系数
            block_size = 64
            block_means = []
            for y in range(0, h - block_size, block_size // 2):
                for x in range(0, w - block_size, block_size // 2):
                    block = grad_mag[y:y + block_size, x:x + block_size]
                    block_means.append(float(np.mean(block)))
            edge_cv = float(np.std(block_means)) / (float(np.mean(block_means)) + 1e-6)

            # 4. 综合判定
            # 特征 A：处处清晰均匀（边缘CV低 + 高梯度占比高）
            uniform_screen = (
                edge_cv < SCREEN_UNIFORM_EDGE_CV
                and high_grad_ratio > SCREEN_UNIFORM_HIGH_GRAD
            )
            # 特征 B：水平边缘异常多（屏幕扫描线）
            horizontal_bias = (
                hv_ratio > SCREEN_HV_RATIO
                and high_grad_ratio > SCREEN_HV_HIGH_GRAD
            )

            is_uniform = uniform_screen or horizontal_bias

            return {
                "uniform_score": round(edge_cv, 4),
                "high_grad_ratio": round(high_grad_ratio, 4),
                "hv_ratio": round(hv_ratio, 4),
                "is_screen_rephoto": is_uniform,
                "message": "边缘分布过于均匀（疑似翻拍屏幕）" if is_uniform else "边缘分布正常",
            }

        except Exception:
            logger.error("翻拍检测失败")
            return {
                "uniform_score": 1.0, "high_grad_ratio": 0.0,
                "hv_ratio": 1.0, "is_screen_rephoto": False,
                "error": "翻拍检测失败",
            }

    @classmethod
    def full_analysis(cls, image_path: str | Path) -> dict:
        """完整图像鉴伪流水线"""
        logger.info("开始图像鉴伪")

        exif_result = cls.analyze_exif(image_path)
        moire_result = cls.detect_moire_fft(image_path)
        ela_result = cls.analyze_ela(image_path)
        noise_result = cls.analyze_noise_consistency(image_path)
        blur_result = cls.detect_blur(image_path)
        uniform_result = cls.detect_screen_uniformity(image_path)

        issues = []
        if exif_result.get("suspicious"):
            issues.append(f"EXIF: {'; '.join(exif_result['flags'])}")
        if moire_result.get("is_moire"):
            issues.append(f"摩尔纹: {moire_result['moire_score']}")
        if ela_result.get("is_tampered"):
            issues.append(f"ELA异常: {ela_result['ela_score']}")
        if noise_result.get("is_spliced"):
            issues.append(f"噪点不一致: {noise_result['inconsistency_score']}")
        if blur_result.get("is_blurry"):
            issues.append(f"模糊: {blur_result['sharpness_score']}")
        if uniform_result.get("is_screen_rephoto"):
            issues.append(f"翻拍特征: 边缘CV={uniform_result['uniform_score']}")

        # 仅有 EXIF/模糊 异常而其他指标无异常 → 不判定为可疑
        # EXIF：用户上传的图片几乎都会丢失（微信/网页上传清空元数据）
        # 模糊：产品照片背景虚化很常见，不单独触发
        # 两者只有在和其他指标叠加时才作为辅助证据
        has_non_exif_issue = any([
            moire_result.get("is_moire"),
            ela_result.get("is_tampered"),
            noise_result.get("is_spliced"),
            uniform_result.get("is_screen_rephoto"),
        ])

        if has_non_exif_issue:
            status = "suspicious"
            message = "; ".join(issues)
        else:
            status = "pass"
            message = "所有检测通过"

        return {
            "status": status,
            "message": message,
            "details": {
                "exif": exif_result,
                "moire": moire_result,
                "ela": ela_result,
                "noise": noise_result,
                "blur": blur_result,
                "uniformity": uniform_result,
            },
        }
