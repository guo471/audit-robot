# -*- coding: utf-8 -*-
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def normalize_decision(value: str | None) -> str:
    value = str(value or "").strip().lower()
    if value == "pass":
        return "pass"
    if value in {"engine_error", "error"}:
        return "error"
    return "manual"


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
    fields: Dict[str, Any] = field(default_factory=dict)
    images: List[AuditImage] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditRequest":
        return cls(
            jl_order_no=str(data.get("jl_order_no", "")),
            channel_order_no=str(data.get("channel_order_no", "")),
            scene_hint=str(data.get("scene_hint", "")),
            fields=dict(data.get("fields") or {}),
            images=[
                image if isinstance(image, AuditImage) else AuditImage.from_dict(image)
                for image in data.get("images", [])
            ],
        )

    def system_data(self) -> Dict[str, Any]:
        data = dict(self.fields)
        data["jl_order_no"] = self.jl_order_no
        data["order_id"] = self.jl_order_no
        data["channel_order_no"] = self.channel_order_no
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
