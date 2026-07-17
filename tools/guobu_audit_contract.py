from __future__ import annotations
import argparse, json, re
from pathlib import Path

NETWORK_FAILURE_MARKERS = ("timeouterror", "timed out", "modelconnectionerror", "connect failed",
                           "winerror 10060", "http error 500",
                           "remote end closed connection without response")
REMOTE_DISCONNECTED_RE = re.compile(r"(?<![A-Za-z])RemoteDisconnected(?![A-Za-z])", re.I)

def network_failure(item: dict) -> bool:
    row = item.get("row") or {}
    text = "\n".join(str(x) for x in [item.get("_error"), row.get("manual_reason"),
        row.get("manual_reason_cn"), row.get("strategy"), row.get("error")] if x is not None)
    return any(x in text.lower() for x in NETWORK_FAILURE_MARKERS) or bool(REMOTE_DISCONNECTED_RE.search(text))

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]

def item_order_id(item: dict) -> str:
    return str((item.get("row") or {}).get("id") or (item.get("task") or {}).get("channel_order_no") or "").strip()

def validate_first_run(tasks_dir: Path, first_jsonl: Path) -> None:
    expected = []
    for path in sorted(tasks_dir.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8-sig"))
        value = str(task.get("channel_order_no") or task.get("task_id") or (task.get("row") or {}).get("id") or "").strip()
        if not value: raise ValueError(f"task has no order ID: {path}")
        expected.append(value)
    actual = [item_order_id(x) for x in load_jsonl(first_jsonl)]
    if any(not x for x in actual): raise ValueError("first JSONL contains an empty order ID")
    if len(expected) != len(set(expected)) or len(actual) != len(set(actual)):
        raise ValueError("duplicate order ID in tasks or first JSONL")
    if set(expected) != set(actual):
        raise ValueError(f"first JSONL ID mismatch: missing={sorted(set(expected)-set(actual))}, extra={sorted(set(actual)-set(expected))}")

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--tasks-dir", required=True); p.add_argument("--first-jsonl", required=True)
    a = p.parse_args(); validate_first_run(Path(a.tasks_dir), Path(a.first_jsonl)); print('{"validated": true}')

if __name__ == "__main__": main()
