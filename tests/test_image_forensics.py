# -*- coding: utf-8 -*-
from modules.image_forensics import ImageForensics


def test_forensics_detector_error_is_suspicious(monkeypatch):
    monkeypatch.setattr(ImageForensics, "analyze_exif", lambda path: {"flags": [], "suspicious": False})
    monkeypatch.setattr(
        ImageForensics,
        "detect_moire_fft",
        lambda path: {"is_moire": False, "error": "FFT 摩尔纹检测失败"},
    )
    monkeypatch.setattr(ImageForensics, "analyze_ela", lambda path: {"is_tampered": False})
    monkeypatch.setattr(ImageForensics, "analyze_noise_consistency", lambda path: {"is_spliced": False})
    monkeypatch.setattr(ImageForensics, "detect_blur", lambda path: {"is_blurry": False})
    monkeypatch.setattr(ImageForensics, "detect_screen_uniformity", lambda path: {"is_screen_rephoto": False})

    result = ImageForensics.full_analysis("C:/secret/SN001234.jpg")

    assert result["status"] == "suspicious"
    assert "鉴伪检测异常" in result["message"]
    assert "C:/secret" not in result["message"]
