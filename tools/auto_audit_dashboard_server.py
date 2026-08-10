import argparse
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from tools.guobu_linux_auto_audit import build_order_result_summary, sn_barcode_observability_from_audit_result
except ModuleNotFoundError:
    from guobu_linux_auto_audit import build_order_result_summary, sn_barcode_observability_from_audit_result


PASS_TEXT = "通过"
REJECT_TEXT = "不通过"
NOT_SENT_TEXT = "未回显"


def load_json(value):
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def callback_label(request):
    status = request.get("status")
    if status == 1:
        return PASS_TEXT
    if status == 2:
        return REJECT_TEXT
    return NOT_SENT_TEXT


def load_status(db_path):
    path = Path(db_path)
    payload = {
        "db_path": str(path),
        "db_exists": path.exists(),
        "updated_at": time.strftime("%H:%M:%S"),
        "total": 0,
        "status_counts": {},
        "feedback_counts": {},
        "cumulative_inserted_count": 0,
        "pending_to_audit_count": 0,
        "pending_feedback_count": 0,
        "feedback_retry_count": 0,
        "manual_dead_letter_count": 0,
        "callback_success_count": 0,
        "callback_failed_count": 0,
        "last_run": {},
        "rows": [],
        "error": "",
    }
    if not path.exists():
        payload["error"] = "state db not found"
        return payload

    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in con.execute(
                """
                select apply_id, channel_order_no, status, payload_json, task_json,
                       audit_result_json, callback_request_json, callback_response_json,
                       error_text, retry_count, created_at, updated_at, audited_at,
                       feedback_done_at, manual_required_at
                from orders
                order by created_at asc, apply_id asc
                """
            ).fetchall()
        ]
        has_audit_runs = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_runs'"
        ).fetchone()
        if has_audit_runs:
            run_columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(audit_runs)").fetchall()
            }
            run_status_expr = "run_status" if "run_status" in run_columns else "'FINISHED' AS run_status"
            run_row = con.execute(
                f"""
                SELECT {run_status_expr}, started_at, finished_at, next_loop_at, heartbeat_only,
                       pending_before, fetched_count, recovered_count, reserved_count,
                       skipped_duplicate_count, skipped_non_pending_machine_status_count,
                       processed_count, feedback_done_count, callback_failed_count,
                       manual_feedback_required_count, errors_json, summary_json
                FROM audit_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if run_row:
                latest = dict(run_row)
                latest["heartbeat_only"] = bool(latest.get("heartbeat_only"))
                latest["errors"] = json.loads(latest.get("errors_json") or "[]")
                latest.pop("errors_json", None)
                latest.pop("summary_json", None)
                payload["last_run"] = latest
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return payload
    finally:
        try:
            con.close()
        except Exception:
            pass

    result_rows = []
    for index, row in enumerate(rows, start=1):
        request = load_json(row.get("callback_request_json"))
        response = load_json(row.get("callback_response_json"))
        audit = load_json(row.get("audit_result_json"))
        task = load_json(row.get("task_json"))
        fields = task.get("fields") if isinstance(task.get("fields"), dict) else {}
        feedback = callback_label(request)
        response_body = response.get("body") if isinstance(response.get("body"), dict) else {}
        callback_ok = response.get("ok")
        if callback_ok is True:
            payload["callback_success_count"] += 1
        elif response:
            payload["callback_failed_count"] += 1

        local_status = row.get("status") or ""
        payload["status_counts"][local_status] = payload["status_counts"].get(local_status, 0) + 1
        payload["feedback_counts"][feedback] = payload["feedback_counts"].get(feedback, 0) + 1
        if local_status in {"NEW", "AUDITING"}:
            payload["pending_to_audit_count"] += 1
        if local_status == "AUDIT_DONE":
            payload["pending_feedback_count"] += 1
        if local_status == "FEEDBACK_RETRY_PENDING":
            payload["feedback_retry_count"] += 1
        if local_status == "MANUAL_FEEDBACK_REQUIRED":
            payload["manual_dead_letter_count"] += 1

        raw = audit.get("_raw") if isinstance(audit.get("_raw"), dict) else {}
        final_summary = build_order_result_summary(
            apply_id=row.get("apply_id") or "",
            audit_result=audit,
            order_context=task,
        )
        barcode = audit.get("barcode_result") or audit.get("sn_barcode_result") or raw.get("sn_barcode_result") or ""
        barcode_fields = sn_barcode_observability_from_audit_result(audit)
        if isinstance(barcode, (dict, list)):
            barcode = json.dumps(barcode, ensure_ascii=False, sort_keys=True)
        elif not barcode and barcode_fields["barcode_attempted"]:
            values = ",".join(str(value) for value in barcode_fields["barcode_values"])
            if barcode_fields["barcode_matched"]:
                barcode = f"matched:{values}"
            elif barcode_fields["barcode_error"]:
                barcode = f"error:{barcode_fields['barcode_error']}"
            else:
                barcode = f"not_matched:{values}"

        result_rows.append(
            {
                "index": index,
                "apply_id": row.get("apply_id") or "",
                "channel_order_no": row.get("channel_order_no") or "",
                "local_status": local_status,
                "feedback": feedback,
                "callback_ok": callback_ok,
                "callback_http_status": response.get("http_status") or response.get("httpStatus") or "",
                "callback_body_status": response_body.get("status", ""),
                "refuse_message": request.get("refuseMessage", ""),
                "final_result": final_summary["final_result"],
                "final_reason": final_summary["final_reason"],
                "manual_flag": audit.get("manual_flag", ""),
                "reason_code": audit.get("manual_reason_code", ""),
                "reason_cn": audit.get("manual_reason_cn", ""),
                "product_type": audit.get("product_type") or fields.get("product_type") or "",
                "system_sn": audit.get("system_sn") or fields.get("system_sn") or "",
                "model_sn": audit.get("model_sn") or audit.get("observed_sn") or "",
                "sn_match": audit.get("sn_match", ""),
                "barcode_mode": audit.get("sn_barcode_mode", ""),
                "barcode_attempted": barcode_fields["barcode_attempted"],
                "barcode_matched": barcode_fields["barcode_matched"],
                "barcode_values": barcode_fields["barcode_values"],
                "barcode_error": barcode_fields["barcode_error"],
                "barcode_rescued": barcode_fields["barcode_rescued"],
                "barcode_result": barcode,
                "activation_evidence_type": audit.get("activation_evidence_type", ""),
                "photo_authenticity": audit.get("photo_authenticity_would_manual", ""),
                "elapsed_sec": audit.get("elapsed_sec", ""),
                "model_calls": audit.get("model_calls", ""),
                "tokens": audit.get("total_tokens", ""),
                "retry_count": row.get("retry_count") or 0,
                "error_text": row.get("error_text") or "",
                "created_at": row.get("created_at") or "",
                "updated_at": row.get("updated_at") or "",
                "audited_at": row.get("audited_at") or "",
                "feedback_done_at": row.get("feedback_done_at") or "",
                "manual_required_at": row.get("manual_required_at") or "",
            }
        )

    payload["total"] = len(result_rows)
    payload["cumulative_inserted_count"] = len(result_rows)
    payload["rows"] = result_rows
    return payload


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自动审核实时看板</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d7dde7;
      --ok: #18794e;
      --bad: #b42318;
      --active: #1d4ed8;
      --wait: #936600;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      font-size: 14px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 5;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 14px 18px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 20px;
      letter-spacing: 0;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(8, minmax(110px, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      background: #fbfcfe;
      min-height: 62px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }
    .metric strong {
      font-size: 19px;
      font-weight: 700;
    }
    main { padding: 16px 18px 24px; }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
      color: var(--muted);
      flex-wrap: wrap;
    }
    input {
      width: min(460px, 100%);
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font-size: 14px;
    }
    .table-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      max-height: calc(100vh - 198px);
    }
    table {
      width: 100%;
      min-width: 1850px;
      border-collapse: collapse;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: #eef2f7;
      z-index: 2;
      font-weight: 700;
    }
    tr:hover td { background: #f8fafc; }
    .ok { color: var(--ok); font-weight: 700; }
    .bad { color: var(--bad); font-weight: 700; }
    .active { color: var(--active); font-weight: 700; }
    .wait { color: var(--wait); font-weight: 700; }
    .muted { color: var(--muted); }
    .reason { white-space: normal; min-width: 220px; max-width: 360px; }
    .mono { font-family: Consolas, "Microsoft YaHei", monospace; }
    .path {
      margin-top: 8px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    @media (max-width: 1200px) {
      .summary { grid-template-columns: repeat(4, minmax(110px, 1fr)); }
      .table-wrap { max-height: calc(100vh - 270px); }
    }
  </style>
</head>
<body>
  <header>
    <h1>自动审核实时看板</h1>
    <div class="summary">
      <div class="metric"><span>本地入库</span><strong id="total">0</strong></div>
      <div class="metric"><span>待审核</span><strong id="newCount">0</strong></div>
      <div class="metric"><span>正在审核</span><strong id="auditingCount">0</strong></div>
      <div class="metric"><span>回显成功</span><strong id="feedbackDone">0</strong></div>
      <div class="metric"><span>通过回显</span><strong id="passCount">0</strong></div>
      <div class="metric"><span>不通过回显</span><strong id="rejectCount">0</strong></div>
      <div class="metric"><span>转人工/失败</span><strong id="manualCount">0</strong></div>
      <div class="metric"><span>最后刷新</span><strong id="updated">-</strong></div>
      <div class="metric"><span>循环状态</span><strong id="loopMode">-</strong></div>
      <div class="metric"><span>上轮抓取</span><strong id="lastFetched">0</strong></div>
      <div class="metric"><span>上轮入库</span><strong id="lastReserved">0</strong></div>
      <div class="metric"><span>上轮审核</span><strong id="lastProcessed">0</strong></div>
      <div class="metric"><span>下轮时间</span><strong id="nextLoop">-</strong></div>
    </div>
    <div class="path" id="sourcePath"></div>
  </header>
  <main>
    <div class="toolbar">
      <input id="search" placeholder="搜索渠道订单号、applyId、SN、原因、状态" />
      <span id="visibleCount"></span>
      <span id="statusText"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>序号</th>
            <th>渠道订单号</th>
            <th>applyId</th>
            <th>本地状态</th>
            <th>回显</th>
            <th>回显接口</th>
            <th>最终结论</th>
            <th>最终中文原因</th>
            <th>拒绝原因</th>
            <th>原因码</th>
            <th>中文原因</th>
            <th>品类</th>
            <th>系统SN</th>
            <th>模型SN</th>
            <th>SN一致</th>
            <th>条码结果</th>
            <th>真实性</th>
            <th>耗时</th>
            <th>调用</th>
            <th>采集时间</th>
            <th>更新时间</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </main>
  <script>
    let latestRows = [];
    const search = document.getElementById("search");

    function text(value) {
      if (value === null || value === undefined) return "";
      return String(value);
    }

    function classForStatus(value) {
      if (value === "FEEDBACK_DONE" || value === "通过") return "ok";
      if (value === "AUDITING") return "active";
      if (value === "NEW" || value === "未回显") return "wait";
      if (value === "不通过" || value === "MANUAL_FEEDBACK_REQUIRED") return "bad";
      return "";
    }

    function renderRows() {
      const keyword = search.value.trim().toLowerCase();
      const rows = latestRows.filter((row) => {
        if (!keyword) return true;
        return JSON.stringify(row).toLowerCase().includes(keyword);
      });
      document.getElementById("rows").innerHTML = rows.map((row) => {
        const snMatch = row.sn_match === true ? "是" : row.sn_match === false ? "否" : "";
        const auth = row.photo_authenticity === true ? "命中" : row.photo_authenticity === false ? "未命中" : "";
        const cb = row.callback_ok === true ? "成功" : row.callback_ok === false ? "失败" : "";
        const feedbackClass = classForStatus(row.feedback);
        const statusClass = classForStatus(row.local_status);
        return `<tr>
          <td>${row.index}</td>
          <td class="mono">${text(row.channel_order_no)}</td>
          <td class="mono">${text(row.apply_id)}</td>
          <td class="${statusClass}">${text(row.local_status)}</td>
          <td class="${feedbackClass}">${text(row.feedback)}</td>
          <td>${cb} ${text(row.callback_http_status || "")}</td>
          <td class="${classForStatus(row.final_result)}">${text(row.final_result)}</td>
          <td class="reason">${text(row.final_reason)}</td>
          <td class="reason">${text(row.refuse_message)}</td>
          <td class="mono">${text(row.reason_code)}</td>
          <td class="reason">${text(row.reason_cn)}</td>
          <td>${text(row.product_type)}</td>
          <td class="mono">${text(row.system_sn)}</td>
          <td class="mono">${text(row.model_sn)}</td>
          <td>${snMatch}</td>
          <td class="mono">${text(row.barcode_result)}</td>
          <td>${auth}</td>
          <td>${text(row.elapsed_sec)}${row.elapsed_sec === "" ? "" : "s"}</td>
          <td>${text(row.model_calls)}</td>
          <td>${text(row.created_at)}</td>
          <td>${text(row.updated_at)}</td>
          <td class="reason">${text(row.error_text)}</td>
        </tr>`;
      }).join("");
      document.getElementById("visibleCount").textContent = `显示 ${rows.length} / ${latestRows.length} 单`;
    }

    async function refresh() {
      try {
        const res = await fetch("/api/auto-audit/status?ts=" + Date.now());
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        latestRows = data.rows || [];
        const statusCounts = data.status_counts || {};
        const feedbackCounts = data.feedback_counts || {};
        const lastRun = data.last_run || {};
        document.getElementById("total").textContent = data.total || 0;
        document.getElementById("newCount").textContent = data.pending_to_audit_count || 0;
        document.getElementById("auditingCount").textContent = statusCounts.AUDITING || 0;
        document.getElementById("feedbackDone").textContent = statusCounts.FEEDBACK_DONE || 0;
        document.getElementById("passCount").textContent = feedbackCounts["通过"] || 0;
        document.getElementById("rejectCount").textContent = feedbackCounts["不通过"] || 0;
        document.getElementById("manualCount").textContent = statusCounts.MANUAL_FEEDBACK_REQUIRED || 0;
        document.getElementById("updated").textContent = data.updated_at || "-";
        document.getElementById("loopMode").textContent = lastRun.run_status === "RUNNING" ? "运行中" : (lastRun.heartbeat_only ? "心跳" : (lastRun.started_at ? "正常" : "-"));
        document.getElementById("lastFetched").textContent = lastRun.fetched_count || 0;
        document.getElementById("lastReserved").textContent = lastRun.reserved_count || 0;
        document.getElementById("lastProcessed").textContent = lastRun.processed_count || 0;
        document.getElementById("nextLoop").textContent = lastRun.next_loop_at ? lastRun.next_loop_at.slice(11, 19) : "-";
        document.getElementById("sourcePath").textContent = data.db_path || "";
        document.getElementById("statusText").textContent = data.error ? `异常：${data.error}` : "连接正常";
        renderRows();
      } catch (err) {
        document.getElementById("statusText").textContent = "连接失败：" + err.message;
      }
    }

    search.addEventListener("input", renderRows);
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def send_content(self, status, content, content_type):
            body = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in {"/", "/auto-audit", "/index.html"}:
                self.send_content(200, HTML, "text/html; charset=utf-8")
                return
            if path == "/api/auto-audit/status":
                payload = load_status(db_path)
                self.send_content(
                    200,
                    json.dumps(payload, ensure_ascii=False),
                    "application/json; charset=utf-8",
                )
                return
            self.send_content(404, "Not found", "text/plain; charset=utf-8")

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Auto audit SQLite dashboard.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.db))
    print(f"auto audit dashboard: http://{args.host}:{args.port}/auto-audit", flush=True)
    print(f"db: {args.db}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
