"""Business display helpers for the Guobu audit report."""

from __future__ import annotations


_UNKNOWN_REASON = "鍥剧墖淇℃伅鏃犳硶纭"

_STANDARD_REASONS = {
    "PRODUCT_TYPE_MISMATCH": "鍟嗗搧绫诲瀷涓嶄竴鑷碻",
    "PRODUCT_PHOTO_INVALID": "鍟嗗搧鐓х墖涓嶇鍚堣姹俙",
    "UNBOXING_PHOTO_INVALID": "鎷嗗皝/瀹夎鐓х墖涓嶇鍚堣姹俙",
    "ACTIVATION_PHOTO_INVALID": "婵€娲荤収鐗囦笉绗﹀悎瑕佹眰",
    "SN_MISSING_IN_ACTIVATION_PHOTO": "婵€娲荤収鐗囦笉绗﹀悎瑕佹眰",
    "ADDRESS_TOO_COARSE": "鏀惰揣鍦板潃涓嶇鍚堣姹俙",
    "DUPLICATE_IMAGE_EVIDENCE": "瀛樺湪閲嶅鍥剧墖锛屼笉绗﹀悎瑕佹眰",
    "NON_REAL_PHOTO_REVIEW": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "NON_REAL_PHOTO_STRONG_RISK": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "IMAGE_STRONG_RISK": "鍥剧墖鐤戜技闈炲疄鎷峘",
    "SN_MISMATCH": "SN涓嶄竴鑷碻",
    "INVOICE_ORANGE_WARNING": "鍙戠エ鐤戜技宸茬孩鍐瞏",
    "MODEL_UNCERTAIN": _UNKNOWN_REASON,
    "PHOTO_AUTHENTICITY_SERVICE_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "ARTIFACT_LOAD_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "FFT_FAILURE": "瀹℃牳鏈嶅姟寮傚父",
    "SN_TRUNCATED_OBSCURED": "SN涓嶅畬鏁达紝鏃犳硶璇嗗埆",
    "SN_NOT_FOUND": "SN鏃犳硶璇嗗埆",
    "SYSTEM_SN_MISSING": "绯荤粺SN缂哄け",
    "IMAGE_MISSING": "鍥剧墖缂哄け",
    "FIELD_MISSING": "璁㈠崟淇℃伅缂哄け",
    "PRODUCT_TYPE_MISSING": "鍟嗗搧绫诲瀷淇℃伅缂哄け",
    "NON_REAL_PHOTO_FFT_RESCUE": "鍥剧墖鐤戜技闈炲疄鎷峘",
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
    if value == "鏄痐":
        return True
    if value == "鍚":
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
        return "妯″瀷鏈鍙栧埌SN"
    if system == observed:
        return ""

    transposition = _adjacent_transposition(system, observed)
    if transposition is not None:
        end = transposition + 2
        return (
            "瀛楃椤哄簭涓嶅悓锛氱郴缁"
            + system[transposition:end]
            + "锛屾ā鍨"
            + observed[transposition:end]
        )

    if len(system) == len(observed):
        differences = [i for i, pair in enumerate(zip(system, observed)) if pair[0] != pair[1]]
        if len(differences) == 1:
            index = differences[0]
            return f"绗?浣嶄笉鍚岋細绯荤粺{system[index]}锛屾ā鍨?"
        return "SN瀛樺湪澶氬宸紓"

    if system.startswith(observed):
        return f"妯″瀷鏈熬灏戣{system[len(observed):]}"
    if system.endswith(observed):
        return "妯″瀷寮€澶村皯璇籎"

    if len(observed) == len(system) + 1:
        edit = _single_edit(observed, system)
        if edit is not None:
            return "妯″瀷绗?浣嶅璇籗"
    if len(system) == len(observed) + 1:
        edit = _single_edit(system, observed)
        if edit is not None:
            return "妯″瀷绗?浣嶅皯璇籗"
    return "SN瀛樺湪澶氬宸紓"


def sn_display(row: dict) -> tuple[str, str]:
    """Return the fixed status and raw-character difference for an audit row."""
    system = _sn_value(row.get("system_sn"))
    observed = _sn_value(row.get("observed_sn"))
    if row.get("sn_match") is True:
        return "鏄痐", ""
    if not system:
        return "鏃犵郴缁烻N", ""
    if not observed:
        return "鏈鍙朻", _sn_difference(system, observed)
    return "鍚", _sn_difference(system, observed)
