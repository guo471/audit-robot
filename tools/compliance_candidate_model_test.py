# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_PYTHON = (3, 14)
REQUIRED_DEPENDENCIES = ("joblib", "numpy", "PIL", "cv2")
WINDOWS_MAX_LEGACY_PATH = 260
ATOMIC_CACHE_PREFIX = "a" * 64 + ".json."


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def check_runtime_dependencies() -> dict[str, str]:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        raise RuntimeError(
            "compliance model tests require Python 3.14; "
            f"received {sys.version_info.major}.{sys.version_info.minor}"
        )
    if "HUAWEI" in sys.executable.upper():
        raise RuntimeError("legacy HUAWEI interpreter paths are forbidden")
    versions: dict[str, str] = {}
    for name in REQUIRED_DEPENDENCIES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"missing runtime dependency: {name}") from exc
        versions[name] = str(getattr(module, "__version__", "available"))
    return versions


def _predicted_atomic_path_length(directory: Path, prefix: str) -> int:
    return len(str(directory.resolve() / f"{prefix}{'b' * 8}.tmp"))


def _probe_directory(directory: Path, *, prefix: str, label: str) -> int:
    predicted_length = _predicted_atomic_path_length(directory, prefix)
    if os.name == "nt" and predicted_length >= WINDOWS_MAX_LEGACY_PATH:
        raise RuntimeError(
            f"{label} temporary path is too long: {predicted_length} characters; "
            "use the fixed short cache directory under TEMP"
        )
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    final: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=prefix,
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(b"audit-robot-preflight")
            handle.flush()
            os.fsync(handle.fileno())
        final = temporary.with_suffix(".probe")
        os.replace(temporary, final)
        temporary = None
        if final.read_bytes() != b"audit-robot-preflight":
            raise RuntimeError(f"{label} read/write probe returned different bytes")
    except OSError as exc:
        raise RuntimeError(f"{label} read/write probe failed: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if final is not None:
            final.unlink(missing_ok=True)
    return predicted_length


def _validate_input_images(records: list[dict[str, Any]]) -> None:
    image_fields = ("商品照片", "拆封/安装照片", "激活/SN照片")
    for record in records:
        order_id = str(record["渠道订单号"])
        for field in image_fields:
            images = record[field]
            if not images:
                raise RuntimeError(f"required image group is empty: {order_id} / {field}")
            for image in images:
                local_path = str(image.get("local_path") or "").strip()
                source_url = str(image.get("source_url") or "").strip()
                if not source_url and (not local_path or not Path(local_path).is_file()):
                    raise RuntimeError(
                        f"image is unavailable to the model: {order_id} / {field}"
                    )


def run_preflight(
    *,
    dataset_path: Path,
    output_dir: Path,
    cache_dir: Path,
    expected_order_count: int,
) -> dict[str, Any]:
    dependency_versions = check_runtime_dependencies()
    from tools import compliance_baseline as baseline
    from tools import compliance_candidate_rules as candidate_rules

    dataset_path = Path(dataset_path).resolve()
    output_dir = Path(output_dir).resolve()
    cache_dir = Path(cache_dir).resolve()
    if not dataset_path.is_file():
        raise RuntimeError(f"dataset is missing: {dataset_path}")
    records = baseline.load_dataset(dataset_path)
    if len(records) != expected_order_count:
        raise RuntimeError(
            f"dataset order count mismatch: expected {expected_order_count}, "
            f"received {len(records)}"
        )
    _validate_input_images(records)
    output_probe_length = _probe_directory(
        output_dir,
        prefix="results.jsonl.",
        label="output",
    )
    cache_probe_length = _probe_directory(
        cache_dir,
        prefix=ATOMIC_CACHE_PREFIX,
        label="cache",
    )
    prompt_hashes = {
        category: _sha256_bytes(
            candidate_rules.prompt_for_category(category).encode("utf-8")
        )
        for category in candidate_rules.PROMPTS
    }
    return {
        "status": "ready",
        "model_calls": 0,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "dependency_versions": dependency_versions,
        "cwd": str(Path.cwd()),
        "dataset_path": str(dataset_path),
        "dataset_sha256": baseline._sha256_file(dataset_path),
        "dataset_order_count": len(records),
        "output_dir": str(output_dir),
        "cache_dir": str(cache_dir),
        "output_probe_path_length": output_probe_length,
        "cache_probe_path_length": cache_probe_length,
        "candidate_version": candidate_rules.CANDIDATE_VERSION,
        "candidate_stage": candidate_rules.CANDIDATE_STAGE,
        "candidate_prompt_sha256": prompt_hashes,
        "vision_api_configured": bool(os.environ.get("VISION_API_BASE_URL")),
        "vision_key_configured": bool(os.environ.get("VISION_API_KEY")),
    }


def run_candidate(
    *,
    dataset_path: Path,
    output_dir: Path,
    cache_dir: Path,
    expected_order_count: int,
    retry_service_failures: bool,
) -> dict[str, Any]:
    preflight = run_preflight(
        dataset_path=dataset_path,
        output_dir=output_dir,
        cache_dir=cache_dir,
        expected_order_count=expected_order_count,
    )
    base_url = os.environ.get("VISION_API_BASE_URL", "").strip()
    api_key = os.environ.get("VISION_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("local vision secrets were not loaded")
    from tools import compliance_baseline as baseline

    summary = baseline.run_baseline(
        dataset_path=Path(dataset_path),
        output_dir=Path(output_dir),
        cache_dir=Path(cache_dir),
        base_url=base_url,
        api_key=api_key,
        model="qwen3.7-plus",
        workers=1,
        timeout_sec=60.0,
        retry_service_failures=retry_service_failures,
        ruleset="candidate",
    )
    return {"preflight": preflight, "summary": summary}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("self-check", "run"))
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--expected-order-count", required=True, type=int)
    parser.add_argument("--retry-service-failures", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action == "self-check":
        result = run_preflight(
            dataset_path=args.dataset,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            expected_order_count=args.expected_order_count,
        )
    else:
        result = run_candidate(
            dataset_path=args.dataset,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            expected_order_count=args.expected_order_count,
            retry_service_failures=args.retry_service_failures,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
