import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REASON_MAP = {
    "ADDRESS_TOO_COARSE": "地址不够精确",
    "SN_MISMATCH": "SN不一致",
    "SN_NOT_FOUND": "未识别到SN",
    "MODEL_UNCERTAIN": "模型无法确认",
    "PRODUCT_TYPE_MISMATCH": "商品类型不一致",
    "PRODUCT_PHOTO_INVALID": "商品照片不符合要求",
    "UNBOXING_PHOTO_INVALID": "拆封照片不符合要求",
    "ACTIVATION_PHOTO_INVALID": "激活/SN照片不符合要求",
    "IMAGE_STRONG_RISK": "图片存在强风险",
    "NON_REAL_PHOTO_REVIEW": "图片疑似非实拍",
    "DUPLICATE_IMAGE_EVIDENCE": "存在重复图片",
    "INVOICE_ORANGE_WARNING": "发票疑似已红冲",
}


def reason_text(code):
    code = str(code or "").strip()
    return REASON_MAP.get(code, code)


def load_rows(jsonl_path):
    rows = []
    if not jsonl_path.exists():
        return rows
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            row = obj.get("row") or {}
            task = obj.get("task") or {}
            fields = task.get("fields") or {}
            code = str(row.get("manual_reason_code") or "").strip()
            rows.append(
                {
                    "index": len(rows) + 1,
                    "order_id": row.get("id") or task.get("channel_order_no") or "",
                    "result": "通过" if not code else "转人工",
                    "reason_code": code,
                    "reason_cn": reason_text(code),
                    "manual_reason": row.get("manual_reason_cn") or row.get("manual_reason") or "",
                    "source_flow_status": row.get("source_flow_status") or fields.get("source_flow_status") or "",
                    "product_type": row.get("product_type") or fields.get("product_type") or "",
                    "system_sn": row.get("system_sn") or fields.get("system_sn") or "",
                    "observed_sn": row.get("observed_sn") or "",
                    "sn_match": row.get("sn_match"),
                    "activation_evidence_type": row.get("activation_evidence_type") or "",
                    "photo_authenticity": row.get("photo_authenticity_would_manual"),
                    "elapsed_sec": row.get("elapsed_sec") or "",
                    "model_calls": row.get("model_calls") or "",
                    "tokens": row.get("total_tokens") or "",
                }
            )
    return rows


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>国补审核实时结果</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #657181;
      --line: #d9dee7;
      --ok: #18794e;
      --manual: #b42318;
      --accent: #1d4ed8;
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
      font-weight: 700;
      letter-spacing: 0;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
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
      width: min(420px, 100%);
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
      max-height: calc(100vh - 190px);
    }
    table {
      width: 100%;
      min-width: 1500px;
      border-collapse: collapse;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
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
    .pass { color: var(--ok); font-weight: 700; }
    .manual { color: var(--manual); font-weight: 700; }
    .muted { color: var(--muted); }
    .reason { white-space: normal; min-width: 180px; max-width: 300px; }
    .mono { font-family: Consolas, "Microsoft YaHei", monospace; }
    .path {
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
      white-space: nowrap;
    }
    @media (max-width: 900px) {
      .summary { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .table-wrap { max-height: calc(100vh - 270px); }
    }
  </style>
</head>
<body>
  <header>
    <h1>国补审核实时结果</h1>
    <div class="summary">
      <div class="metric"><span>进度</span><strong id="progress">0/0</strong></div>
      <div class="metric"><span>自动通过</span><strong id="passCount">0</strong></div>
      <div class="metric"><span>转人工</span><strong id="manualCount">0</strong></div>
      <div class="metric"><span>最后更新</span><strong id="updated">-</strong></div>
      <div class="metric"><span>刷新状态</span><strong id="status">连接中</strong></div>
    </div>
    <div class="path" id="sourcePath"></div>
  </header>
  <main>
    <div class="toolbar">
      <input id="search" placeholder="搜索订单号、SN、原因码、品类" />
      <span id="visibleCount"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>序号</th>
            <th>渠道订单号</th>
            <th>结果</th>
            <th>原因码</th>
            <th>中文原因</th>
            <th>原始流程状态</th>
            <th>品类</th>
            <th>系统SN</th>
            <th>模型SN</th>
            <th>SN一致</th>
            <th>激活证据</th>
            <th>真实性</th>
            <th>耗时</th>
            <th>调用</th>
            <th>Token</th>
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

    function renderRows() {
      const keyword = search.value.trim().toLowerCase();
      const body = document.getElementById("rows");
      const rows = latestRows.filter((row) => {
        if (!keyword) return true;
        return JSON.stringify(row).toLowerCase().includes(keyword);
      });
      body.innerHTML = rows.map((row) => {
        const resultClass = row.result === "通过" ? "pass" : "manual";
        const snMatch = row.sn_match === true ? "是" : row.sn_match === false ? "否" : "";
        const auth = row.photo_authenticity === true ? "命中" : row.photo_authenticity === false ? "未命中" : "";
        return `<tr>
          <td>${row.index}</td>
          <td class="mono">${text(row.order_id)}</td>
          <td class="${resultClass}">${text(row.result)}</td>
          <td class="mono">${text(row.reason_code)}</td>
          <td class="reason">${text(row.reason_cn || row.manual_reason)}</td>
          <td>${text(row.source_flow_status)}</td>
          <td>${text(row.product_type)}</td>
          <td class="mono">${text(row.system_sn)}</td>
          <td class="mono">${text(row.observed_sn)}</td>
          <td>${snMatch}</td>
          <td>${text(row.activation_evidence_type)}</td>
          <td>${auth}</td>
          <td>${text(row.elapsed_sec)}s</td>
          <td>${text(row.model_calls)}</td>
          <td>${text(row.tokens)}</td>
        </tr>`;
      }).join("");
      document.getElementById("visibleCount").textContent = `显示 ${rows.length} / ${latestRows.length} 单`;
    }

    async function refresh() {
      try {
        const res = await fetch("/api/status?ts=" + Date.now());
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        latestRows = data.rows || [];
        const done = data.done || 0;
        const total = data.total || 0;
        document.getElementById("progress").textContent = `${done}/${total}`;
        document.getElementById("passCount").textContent = data.pass_count || 0;
        document.getElementById("manualCount").textContent = data.manual_count || 0;
        document.getElementById("updated").textContent = data.updated_at || "-";
        document.getElementById("status").textContent = "正常";
        document.getElementById("sourcePath").textContent = data.jsonl_path || "";
        renderRows();
      } catch (err) {
        document.getElementById("status").textContent = "等待";
      }
    }

    search.addEventListener("input", renderRows);
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def make_handler(jsonl_path, total):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send(self, status, content, content_type):
            encoded = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/" or path == "/index.html":
                self._send(200, HTML, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                rows = load_rows(jsonl_path)
                manual = sum(1 for row in rows if row["result"] != "通过")
                updated = "-"
                if jsonl_path.exists():
                    updated = time.strftime("%H:%M:%S", time.localtime(jsonl_path.stat().st_mtime))
                payload = {
                    "jsonl_path": str(jsonl_path),
                    "done": len(rows),
                    "total": total,
                    "pass_count": len(rows) - manual,
                    "manual_count": manual,
                    "updated_at": updated,
                    "rows": rows,
                }
                self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(404, "Not found", "text/plain; charset=utf-8")

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(jsonl_path, args.total))
    print(f"live dashboard: http://{args.host}:{args.port}/")
    print(f"jsonl: {jsonl_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
