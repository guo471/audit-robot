from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from .artifacts import build_release_bundle, preprocess_contract_hash, verify_release_bundle
from .benchmark import benchmark_orders
from .config import AppConfig, check_environment, load_config
from .contracts import ReasonCode
from .grouping import cluster_source_groups
from .hashing import sha256_file
from .incremental import PromotionPolicy, append_confirmed_samples, compare_releases
from .inference import ShadowPredictor, predict_order_isolated
from .manifest import MANIFEST_COLUMNS, ManifestRow, build_manifest, validate_manifest
from .onnx_export import export_onnx
from .reporting import PredictionRecord, evaluate_predictions, write_evaluation_report
from .splitting import ExploratoryFold, SplitPlan, create_splits
from .thresholds import select_thresholds
from .training import cross_validate


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "base.toml"


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    path.resolve().write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_manifest(path: Path, rows: Sequence[ManifestRow]) -> None:
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def _read_predictions(path: Path) -> list[PredictionRecord]:
    with path.resolve().open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            PredictionRecord(
                sample_id=row["sample_id"],
                label=row["label"],
                label_status=row["label_status"],
                score=float(row["score"]),
                decision=row["decision"],
                source_group=row["source_group"],
                scope=row["scope"],
                split=row["split"],
                manifest_sha256=row["manifest_sha256"],
                model_sha256=row["model_sha256"],
                threshold_sha256=row.get("threshold_sha256", ""),
            )
            for row in reader
        ]


def _split_payload(plan: SplitPlan) -> dict[str, object]:
    return {
        "manifest_sha256": plan.manifest_sha256,
        "formal_status": plan.formal_status,
        "exploratory": plan.exploratory,
        "shortage_counts": plan.shortage_counts,
        "seed": plan.seed,
        "folds": plan.folds,
        "formal_locked": [asdict(row) for row in plan.formal_locked],
        "development_rows": [asdict(row) for row in plan.development_rows],
        "exploratory_folds": [
            {
                "index": fold.index,
                "train_rows": [asdict(row) for row in fold.train_rows],
                "validation_rows": [asdict(row) for row in fold.validation_rows],
            }
            for fold in plan.exploratory_folds
        ],
    }


def _load_split(path: Path) -> SplitPlan:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    row = lambda value: ManifestRow(**value)
    folds = tuple(
        ExploratoryFold(
            int(item["index"]),
            tuple(row(value) for value in item["train_rows"]),
            tuple(row(value) for value in item["validation_rows"]),
        )
        for item in payload["exploratory_folds"]
    )
    return SplitPlan(
        exploratory_folds=folds,
        formal_locked=tuple(row(value) for value in payload["formal_locked"]),
        formal_status=payload["formal_status"],
        manifest_sha256=payload["manifest_sha256"],
        exploratory=bool(payload["exploratory"]),
        shortage_counts=dict(payload["shortage_counts"]),
        seed=int(payload["seed"]),
        folds=int(payload["folds"]),
        development_rows=tuple(row(value) for value in payload["development_rows"]),
    )


def _record_operation(command: str, code: int, payload: object) -> None:
    path = Path(os.environ.get("PA_OPERATION_LOG", ROOT / "reports" / "logs" / "operations.jsonl"))
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    with path.resolve().open("a", encoding="utf-8", newline="") as handle:
        handle.write(
            json.dumps(
                {"schema_version": 1, "command": command, "exit_code": code, "result": _jsonable(payload)},
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n"
        )


def _handle(args: argparse.Namespace, config: AppConfig) -> tuple[object, int]:
    command = args.command
    if command == "check-env":
        check = check_environment(tuple(sys.version_info[:3]), config.mode, False)
        return {"ok": check.ok, "mode": config.mode, "reason_code": check.reason_code}, 0 if check.ok else 2
    if command == "build-manifest":
        result = build_manifest(args.non_real_dir, args.real_candidates, args.output)
        return {"output": result.output_path, "rows": len(result.rows), "errors": result.errors}, 0
    if command == "group-sources":
        validation = validate_manifest(args.manifest)
        if not validation.ok:
            raise ValueError("; ".join(validation.errors))
        result = cluster_source_groups(validation.rows)
        _write_manifest(args.output, result.rows)
        _write_json(args.evidence, result.evidence)
        return {"output": args.output.resolve(), "evidence": args.evidence.resolve(), "rows": len(result.rows)}, 0
    if command == "split":
        validation = validate_manifest(args.manifest)
        if not validation.ok:
            raise ValueError("; ".join(validation.errors))
        plan = create_splits(validation.rows, seed=config.seed)
        _write_json(args.output, _split_payload(plan))
        return {"output": args.output.resolve(), "formal_status": plan.formal_status, "exploratory": plan.exploratory}, 0
    if command == "train":
        plan = _load_split(args.split)
        result = cross_validate(plan, config, args.run_dir)
        return result, 0
    if command == "freeze-thresholds":
        records = _read_predictions(args.predictions)
        result = select_thresholds(records, config.thresholds)
        _write_json(args.output, result.thresholds)
        return {"output": args.output.resolve(), "selection": result}, 0
    if command == "evaluate":
        records = _read_predictions(args.run_dir / "oof-predictions.csv")
        result = evaluate_predictions(records, "exploratory_cv")
        paths = write_evaluation_report(
            result, args.output_dir / "evaluation.json", args.output_dir / "evaluation.md"
        )
        return {"formal_status": _load_split(args.split).formal_status, "reports": paths}, 0
    if command == "export-onnx":
        checkpoint = args.run_dir / "final.pt"
        if not checkpoint.is_file():
            checkpoint = args.run_dir / "fold-1" / "best.pt"
        exported = export_onnx(checkpoint, args.run_dir / "model.onnx", config.preprocess)
        threshold_data = json.loads((args.run_dir / "thresholds.json").read_text(encoding="utf-8"))
        threshold_data["model_sha256"] = exported.model_sha256
        bound_thresholds = args.run_dir / "release-thresholds.json"
        _write_json(bound_thresholds, threshold_data)
        run_metadata = json.loads((args.run_dir / "run-metadata.json").read_text(encoding="utf-8"))
        import numpy as np
        import onnxruntime as ort

        session = ort.InferenceSession(str(exported.output_path), providers=["CPUExecutionProvider"])
        logits = session.run(
            None,
            {session.get_inputs()[0].name: np.zeros((1, 3, config.preprocess.image_size, config.preprocess.image_size), dtype=np.float32)},
        )[0]
        shifted = logits[0] - np.max(logits[0])
        expected = float(np.exp(shifted)[1] / np.exp(shifted).sum())
        metadata = {
            "manifest_sha256": run_metadata["manifest_sha256"],
            "preprocessing_contract_hash": preprocess_contract_hash(config.preprocess),
            "preprocess": asdict(config.preprocess),
            "model_version": args.release_dir.name,
            "output_order": ["real", "non_real"],
            "mode": "offline_shadow",
            "exploratory": bool(run_metadata["exploratory"]),
            "self_test": {"expected_non_real_risk": expected, "absolute_tolerance": 1e-5},
        }
        metadata_path = args.run_dir / "release-metadata.json"
        _write_json(metadata_path, metadata)
        release = build_release_bundle(exported.output_path, bound_thresholds, metadata_path, args.release_dir)
        return release, 0
    if command == "verify-release":
        result = verify_release_bundle(args.release)
        return result, 0 if result.ok else 2
    if command == "infer-image":
        startup = ShadowPredictor.start(args.release, config.runtime.intra_op_threads)
        if not startup.ok or startup.predictor is None:
            return {"mode": "offline_shadow", "decision": "manual_review", "reason_code": startup.reason_code}, 3
        result = startup.predictor.predict_image(args.image)
        return {"mode": "offline_shadow", **_jsonable(result)}, 0 if result.decision == "low_risk_candidate" else 3
    if command == "infer-order":
        log = args.log or (args.release / "inference.jsonl")
        result = predict_order_isolated(
            args.release,
            args.images,
            args.timeout or config.runtime.max_order_seconds,
            log,
            intra_op_threads=config.runtime.intra_op_threads,
        )
        return result, 0 if result.decision == "low_risk_candidate" else 3
    if command == "benchmark":
        with args.orders.resolve().open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        orders = [tuple(Path(row[f"image{index}"]) for index in (1, 2, 3)) for row in rows]
        release_hash = sha256_file(args.release / "release.json")

        class Predictor:
            release_sha256 = release_hash
            intra_op_threads = config.runtime.intra_op_threads

            def predict_order(self, order):
                return predict_order_isolated(
                    args.release,
                    order,
                    config.runtime.max_order_seconds,
                    args.output.parent / "benchmark-inference.jsonl",
                    intra_op_threads=self.intra_op_threads,
                )

        result = benchmark_orders(Predictor, orders, args.warmup, args.repetitions)
        _write_json(args.output, result)
        return {"output": args.output.resolve(), "benchmark": result}, 0
    if command == "append-samples":
        return append_confirmed_samples(args.previous_manifest, args.additions, args.output), 0
    if command == "compare-releases":
        policy = PromotionPolicy(args.max_real_manual_rate, args.max_p95_ms, args.max_ms)
        return compare_releases(args.old_report, args.new_report, policy), 0
    raise ValueError(f"unsupported command: {command}")


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photo-authenticity")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-env"); _add_config(check)
    build = commands.add_parser("build-manifest"); _add_config(build)
    build.add_argument("--non-real-dir", type=Path, required=True); build.add_argument("--real-candidates", type=Path, required=True); build.add_argument("--output", type=Path, required=True)
    group = commands.add_parser("group-sources"); _add_config(group)
    group.add_argument("--manifest", type=Path, required=True); group.add_argument("--output", type=Path, required=True); group.add_argument("--evidence", type=Path, required=True)
    split = commands.add_parser("split"); _add_config(split)
    split.add_argument("--manifest", type=Path, required=True); split.add_argument("--output", type=Path, required=True)
    train = commands.add_parser("train"); _add_config(train)
    train.add_argument("--split", type=Path, required=True); train.add_argument("--run-dir", type=Path, required=True)
    freeze = commands.add_parser("freeze-thresholds"); _add_config(freeze)
    freeze.add_argument("--predictions", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate"); _add_config(evaluate)
    evaluate.add_argument("--run-dir", type=Path, required=True); evaluate.add_argument("--split", type=Path, required=True); evaluate.add_argument("--output-dir", type=Path, required=True)
    export = commands.add_parser("export-onnx"); _add_config(export)
    export.add_argument("--run-dir", type=Path, required=True); export.add_argument("--release-dir", type=Path, required=True)
    verify = commands.add_parser("verify-release"); _add_config(verify); verify.add_argument("--release", type=Path, required=True)
    image = commands.add_parser("infer-image"); _add_config(image); image.add_argument("--release", type=Path, required=True); image.add_argument("image", type=Path)
    order = commands.add_parser("infer-order"); _add_config(order); order.add_argument("--release", type=Path, required=True); order.add_argument("images", type=Path, nargs=3); order.add_argument("--timeout", type=float); order.add_argument("--log", type=Path)
    benchmark = commands.add_parser("benchmark"); _add_config(benchmark); benchmark.add_argument("--release", type=Path, required=True); benchmark.add_argument("--orders", type=Path, required=True); benchmark.add_argument("--output", type=Path, required=True); benchmark.add_argument("--warmup", type=int, default=1); benchmark.add_argument("--repetitions", type=int, default=10)
    append = commands.add_parser("append-samples"); _add_config(append); append.add_argument("--previous-manifest", type=Path, required=True); append.add_argument("--additions", type=Path, required=True); append.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare-releases"); _add_config(compare); compare.add_argument("--old-report", type=Path, required=True); compare.add_argument("--new-report", type=Path, required=True); compare.add_argument("--max-real-manual-rate", type=float, required=True); compare.add_argument("--max-p95-ms", type=float, required=True); compare.add_argument("--max-ms", type=float, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    inference_command = args.command in {"infer-image", "infer-order"}
    try:
        config = load_config(args.config)
        environment = check_environment(tuple(sys.version_info[:3]), config.mode, False)
        if not environment.ok:
            raise ValueError(environment.detail)
        payload, code = _handle(args, config)
    except Exception as exc:
        if inference_command:
            payload = {"mode": "offline_shadow", "decision": "manual_review", "reason_code": ReasonCode.INFERENCE_ERROR, "detail": str(exc)}
            code = 3
        else:
            payload = {"ok": False, "reason_code": ReasonCode.ENVIRONMENT_INVALID, "detail": str(exc)}
            code = 2
    try:
        _record_operation(args.command, code, payload)
    except Exception:
        if inference_command:
            payload = {"mode": "offline_shadow", "decision": "manual_review", "reason_code": ReasonCode.LOG_WRITE_FAILED}
            code = 3
        else:
            payload = {"ok": False, "reason_code": ReasonCode.LOG_WRITE_FAILED}
            code = 2
    print(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
