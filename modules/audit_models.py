# -*- coding: utf-8 -*-
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


ORDER_NO_KEYS = ("jl_order_no", "order_id", "channel_order_no", "id", "渠道订单号", "嘉联订单号")


def normalize_decision(value: str | None) -> str:
    value = str(value or "").strip().lower()
    if value == "pass":
        return "pass"
    if value in {"engine_error", "error"}:
        return "error"
    return "manual"


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def resolve_order_no(jl_order_no: Any = "", channel_order_no: Any = "", fields: Dict[str, Any] | None = None) -> str:
    field_values = [dict(fields or {}).get(key) for key in ORDER_NO_KEYS]
    return first_non_empty(jl_order_no, channel_order_no, *field_values)


def resolve_channel_order_no(channel_order_no: Any = "", fields: Dict[str, Any] | None = None) -> str:
    return first_non_empty(channel_order_no, dict(fields or {}).get("channel_order_no"), dict(fields or {}).get("渠道订单号"))


@dataclass(frozen=True)
class AuditImage:
    title: str
    path: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditImage":
        return cls(
            title=str(data.get("title", "")),
            path=str(data.get("path", "")),
        )


@dataclass(frozen=True)
class AuditRequest:
    jl_order_no: str
    channel_order_no: str = ""
    scene_hint: str = ""
    page_text: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)
    images: List[AuditImage] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditRequest":
        fields = dict(data.get("fields") or {})
        channel_order_no = resolve_channel_order_no(data.get("channel_order_no", ""), fields)
        jl_order_no = resolve_order_no(data.get("jl_order_no", ""), channel_order_no, fields)
        return cls(
            jl_order_no=jl_order_no,
            channel_order_no=channel_order_no,
            scene_hint=str(data.get("scene_hint", "")),
            page_text=str(data.get("page_text", "")),
            fields=fields,
            images=[
                image if isinstance(image, AuditImage) else AuditImage.from_dict(image)
                for image in data.get("images", [])
            ],
        )

    def system_data(self) -> Dict[str, Any]:
        data = dict(self.fields)
        jl_order_no = resolve_order_no(self.jl_order_no, self.channel_order_no, data)
        data["jl_order_no"] = jl_order_no
        data["order_id"] = jl_order_no
        data["channel_order_no"] = resolve_channel_order_no(self.channel_order_no, data)
        if self.page_text:
            data["page_text"] = self.page_text
        return data


@dataclass(frozen=True)
class AuditResponse:
    decision: str
    action: str
    jl_order_no: str
    scene: str
    path: str
    elapsed_sec: float
    manual_reason: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def pass_(
        cls,
        jl_order_no: str,
        scene: str,
        path: str,
        elapsed_sec: float,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "AuditResponse":
        return cls(
            decision="pass",
            action="approve",
            jl_order_no=jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=elapsed_sec,
            evidence=dict(evidence or {}),
        )

    @classmethod
    def manual(
        cls,
        jl_order_no: str,
        scene: str,
        path: str,
        elapsed_sec: float,
        manual_reason: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "AuditResponse":
        return cls(
            decision="manual",
            action="next",
            jl_order_no=jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=elapsed_sec,
            manual_reason=manual_reason,
            evidence=dict(evidence or {}),
        )

    @classmethod
    def error(
        cls,
        jl_order_no: str,
        scene: str,
        path: str,
        elapsed_sec: float,
        manual_reason: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "AuditResponse":
        return cls(
            decision="error",
            action="next",
            jl_order_no=jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=elapsed_sec,
            manual_reason=manual_reason,
            evidence=dict(evidence or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
