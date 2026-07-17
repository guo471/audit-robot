"""Business display helpers for the Guobu audit report."""

from __future__ import annotations


_UNKNOWN_REASON = "图片信息无法确认"

_STANDARD_REASONS = {
    "PRODUCT_TYPE_MISMATCH": "商品类型不一致",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "UNBOXING_PHOTO_INVALID": "拆封/安装照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活照片不符合要求",
    "SN_MISSING_IN_ACTIVATION_PHOTO": "激活照片不符合要求",
    "ADDRESS_TOO_COARSE": "收货地址不符合要求",
    "DUPLICATE_IMAGE_EVIDENCE": "存在重复图片，不符合要求",
    "NON_REAL_PHOTO_REVIEW": "图片疑似非实拍",
    "NON_REAL_PHOTO_STRONG_RISK": "图片疑似非实拍",
    "IMAGE_STRONG_RISK": "图片疑似非实拍",
    "SN_MISMATCH": "SN不一致",
    "INVOICE_ORANGE_WARNING": "发票疑似已红冲",
    "MODEL_UNCERTAIN": _UNKNOWN_REASON,
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "审核服务异常",
    "ARTIFACT_LOAD_FAILURE": "审核服务异常",
    "FFT_FAILURE": "审核服务异常",
    "SN_TRUNCATED_OBSCURED": "SN不完整，无法识别",
    "SN_NOT_FOUND": "SN无法识别",
    "SYSTEM_SN_MISSING": "系统SN缺失",
    "IMAGE_MISSING": "图片缺失",
    "FIELD_MISSING": "订单信息缺失",
    "PRODUCT_TYPE_MISSING": "商品类型信息缺失",
    "NON_REAL_PHOTO_FFT_RESCUE": "图片疑似非实拍",
}


def standard_reason(code: str) -> str:
    """Return the approved business wording for a primary reason code."""
    if not code:
        return ""
    return _STANDARD_REASONS.get(code, _UNKNOWN_REASON)


def parse_manual_flag(value: object) -> bool:
    """Parse the only explicit values permitted for a final manual flag."""
    if type(value) is bool:
        return value
    if value == "是":
        return True
    if value == "否":
        return False
    raise ValueError(f"invalid manual_flag: {value!r}")


def _sn_value(value: object) -> str:
    return "" if value is None else str(value)


def _adjacent_transposition(system: str, observed: str) -> int | None:
    differences = [i for i, pair in enumerate(zip(system, observed)) if pair[0] != pair[1]]
    if len(differences) != 2:
        return None
    first, second = differences
    if second == first + 1 and system[first] == observed[second] and system[second] == observed[first]:
        return first
    return None


def _single_edit(longer: str, shorter: str) -> tuple[int, str] | None:
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return index, longer[index]
    return None


def _sn_difference(system: str, observed: str) -> str:
    if not observed:
        return "模型未读取到SN"
    if system == observed:
        return ""

    transposition = _adjacent_transposition(system, observed)
    if transposition is not None:
        end = transposition + 2
        return f"字符顺序不同：系统{system[transposition:end]}，模型{observed[transposition:end]}"

    if len(system) == len(observed):
        differences = [i for i, pair in enumerate(zip(system, observed)) if pair[0] != pair[1]]
        if len(differences) == 1:
            index = differences[0]
            return f"第{index + 1}位不同：系统{system[index]}，模型{observed[index]}"
        return "SN存在多处差异"

    if system.startswith(observed):
        return f"模型末尾少读{system[len(observed):]}"
    if system.endswith(observed):
        return f"模型开头少读{system[:-len(observed)]}"

    if len(observed) == len(system) + 1:
        edit = _single_edit(observed, system)
        if edit is not None:
            index, character = edit
            return f"模型第{index + 1}位多读{character}"
    if len(system) == len(observed) + 1:
        edit = _single_edit(system, observed)
        if edit is not None:
            index, character = edit
            return f"模型第{index + 1}位少读{character}"
    return "SN存在多处差异"


def sn_display(row: dict) -> tuple[str, str]:
    """Return the fixed status and raw-character difference for an audit row."""
    system = _sn_value(row.get("system_sn"))
    observed = _sn_value(row.get("observed_sn"))
    if row.get("sn_match") is True:
        return "是", ""
    if not system:
        return "无系统SN", ""
    if not observed:
        return "未读取", _sn_difference(system, observed)
    return "否", _sn_difference(system, observed)
