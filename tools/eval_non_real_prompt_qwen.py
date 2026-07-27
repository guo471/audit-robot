from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL = "qwen3.7-plus"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _load_observation_contract():
    """Load the V4 observation validator without requiring optional ML artifacts."""
    if "joblib" not in sys.modules:
        shim = ModuleType("joblib")

        def _missing_load(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("joblib is unavailable in this evaluation runtime")

        shim.load = _missing_load  # type: ignore[attr-defined]
        sys.modules["joblib"] = shim
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tools.photo_authenticity_mainline import derive_v4_result, validate_image_observations

    return derive_v4_result, validate_image_observations


DERIVE_V4_RESULT, VALIDATE_IMAGE_OBSERVATIONS = _load_observation_contract()


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _post_json(base_url: str, api_key: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _build_request_body(prompt: str, sample_id: str, image_path: Path) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps({"sample_id": sample_id}, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_path), "detail": "high"}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    }


def _call_qwen(
    base_url: str,
    api_key: str,
    prompt: str,
    sample_id: str,
    image_path: Path,
    timeout: int,
) -> tuple[dict[str, Any], str, dict[str, Any], float]:
    body = _build_request_body(prompt, sample_id, image_path)
    started = time.time()
    response = _post_json(base_url, api_key, body, timeout)
    elapsed = time.time() - started
    text = response["choices"][0]["message"]["content"]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed, text, response.get("usage") or {}, elapsed


def _json_cache_path(cache_dir: Path, prompt: str, image_path: Path) -> Path:
    image_bytes = image_path.read_bytes()
    key = {
        "model": MODEL,
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "image_sha256": _sha256_bytes(image_bytes),
    }
    digest = _sha256_bytes(json.dumps(key, sort_keys=True).encode("utf-8"))
    return cache_dir / f"{digest}.json"


def _iter_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _select_items(items: list[Path], limit: int | None, offset: int) -> list[Path]:
    sliced = items[offset:]
    return sliced if limit is None else sliced[:limit]


def _normalized_observation_payload(parsed: dict[str, Any], sample_id: str) -> dict[str, Any]:
    if "photo_authenticity_by_image" in parsed:
        observations = parsed["photo_authenticity_by_image"]
        if not isinstance(observations, list) or len(observations) != 1:
            raise ValueError("photo_authenticity_by_image must contain exactly one observation")
        parsed = observations[0]
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return {
        "image_id": sample_id,
        "edges": parsed.get("edges"),
        "screen_owner": parsed.get("screen_owner"),
        "strong_evidence": parsed.get("strong_evidence"),
        "weak_evidence": parsed.get("weak_evidence"),
        "reason": parsed.get("reason"),
    }


def _derive_from_model_observations(parsed: dict[str, Any], sample_id: str) -> tuple[str, str, dict[str, Any]]:
    payload = _normalized_observation_payload(parsed, sample_id)
    observation = VALIDATE_IMAGE_OBSERVATIONS([payload], [sample_id])[sample_id]
    result, rule = DERIVE_V4_RESULT(observation)
    return result, rule, payload


def _failure_row(sample_id: str, error: Exception) -> tuple[str, str, dict[str, Any]]:
    payload = {
        "image_id": sample_id,
        "edges": {"top": "carrier_boundary", "right": "uncertain", "bottom": "uncertain", "left": "uncertain"},
        "screen_owner": "uncertain",
        "strong_evidence": [],
        "weak_evidence": [],
        "reason": f"schema_error: {type(error).__name__}",
    }
    observation = VALIDATE_IMAGE_OBSERVATIONS([payload], [sample_id])[sample_id]
    result, rule = DERIVE_V4_RESULT(observation)
    return result, rule, payload


def _is_intercept(derived_result: str) -> bool:
    return derived_result != "no_evidence"


def _run_group(
    *,
    label: str,
    expected_intercept: bool,
    images: list[Path],
    base_url: str,
    api_key: str,
    prompt: str,
    cache_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    rows = []
    cache_dir.mkdir(parents=True, exist_ok=True)
    for index, image_path in enumerate(images, start=1):
        sample_id = f"sample_{index:06d}"
        cache_path = _json_cache_path(cache_dir, prompt, image_path)
        cached = False
        if cache_path.exists():
            record = json.loads(cache_path.read_text(encoding="utf-8"))
            parsed = record["parsed"]
            usage = record.get("usage") or {}
            elapsed = 0.0
            cached = True
        else:
            parsed, text, usage, elapsed = _call_qwen(base_url, api_key, prompt, sample_id, image_path, timeout)
            try:
                derived_result, derived_rule, normalized_observation = _derive_from_model_observations(parsed, sample_id)
            except Exception as exc:
                derived_result, derived_rule, normalized_observation = _failure_row(sample_id, exc)
            record = {
                "model": MODEL,
                "image_sha256": _sha256_bytes(image_path.read_bytes()),
                "sample_id": sample_id,
                "parsed": parsed,
                "normalized_observation": normalized_observation,
                "derived_result": derived_result,
                "derived_rule": derived_rule,
                "content_text": text,
                "usage": usage,
                "elapsed_sec": elapsed,
            }
            cache_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            derived_result, derived_rule, normalized_observation = _derive_from_model_observations(parsed, sample_id)
        except Exception as exc:
            derived_result, derived_rule, normalized_observation = _failure_row(sample_id, exc)
        intercepted = _is_intercept(derived_result)
        ok = intercepted == expected_intercept
        rows.append({
            "group": label,
            "index": index,
            "sample_id": sample_id,
            "image": str(image_path.relative_to(PROJECT_ROOT)),
            "model_result": parsed.get("result"),
            "derived_result": derived_result,
            "derived_rule": derived_rule,
            "intercepted": intercepted,
            "ok": ok,
            "cached": cached,
            "elapsed_sec": elapsed,
            "usage": usage,
            "reason": parsed.get("reason"),
            "normalized_observation": normalized_observation,
            "parsed": parsed,
        })
        print(f"{label} {index}/{len(images)} sample_id={sample_id} result={derived_result} rule={derived_rule} ok={ok} cached={cached}", flush=True)
    failures = [row for row in rows if not row["ok"]]
    return {
        "label": label,
        "total": len(rows),
        "expected_intercept": expected_intercept,
        "errors": len(failures),
        "error_rate": (len(failures) / len(rows)) if rows else 0.0,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--a-dir", default=str(PROJECT_ROOT / "\u975e\u5b9e\u62cd\u6837\u672c"))
    parser.add_argument("--b-dir", default=str(PROJECT_ROOT / "\u5b9e\u62cd\u56fe\u6837\u672c"))
    parser.add_argument("--limit-a", type=int)
    parser.add_argument("--limit-b", type=int)
    parser.add_argument("--offset-a", type=int, default=0)
    parser.add_argument("--offset-b", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    base_url = os.environ.get("VISION_API_BASE_URL", "").strip()
    api_key = os.environ.get("VISION_API_KEY", "").strip()
    if not base_url or not api_key:
        raise SystemExit("VISION_API_BASE_URL and VISION_API_KEY are required")

    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    a_images = _select_items(_iter_images(Path(args.a_dir)), args.limit_a, args.offset_a)
    b_images = _select_items(_iter_images(Path(args.b_dir)), args.limit_b, args.offset_b)
    cache_dir = Path(args.cache_dir)
    result = {
        "model": MODEL,
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "groups": [],
    }
    result["groups"].append(_run_group(
        label="A",
        expected_intercept=True,
        images=a_images,
        base_url=base_url,
        api_key=api_key,
        prompt=prompt,
        cache_dir=cache_dir,
        timeout=args.timeout,
    ))
    result["groups"].append(_run_group(
        label="B",
        expected_intercept=False,
        images=b_images,
        base_url=base_url,
        api_key=api_key,
        prompt=prompt,
        cache_dir=cache_dir,
        timeout=args.timeout,
    ))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for group in result["groups"]:
        print(f"SUMMARY {group['label']} total={group['total']} errors={group['errors']} error_rate={group['error_rate']:.4f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
