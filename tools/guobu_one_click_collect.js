const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_CONFIG_PATH = path.join(PROJECT_ROOT, "config", "guobu_collect_one_click.json");
const SKILL_ROOT = path.join(process.env.USERPROFILE || "", ".codex", "skills", "guobu-examine-api-collector");
const COLLECTOR_WRAPPER = path.join(SKILL_ROOT, "scripts", "collect_guobu_filtered.ps1");

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) continue;
    const name = key.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      parsed[name] = true;
    } else {
      parsed[name] = next;
      index += 1;
    }
  }
  return parsed;
}

function readJsonIfExists(filePath) {
  if (!fs.existsSync(filePath)) return {};
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function numberValue(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`Invalid number: ${value}`);
  return parsed;
}

function normalizeStatus(value) {
  const raw = String(value || "failed").trim().toLowerCase();
  if (["failed", "fail", "3", "未通过"].includes(raw)) return "failed";
  if (["passed", "pass", "2", "已通过"].includes(raw)) return "passed";
  if (["pending", "0", "待审核"].includes(raw)) return "pending";
  if (["reviewing", "1", "审核中"].includes(raw)) return "reviewing";
  if (["all", "*", "全部"].includes(raw)) return "all";
  return String(value);
}

function loadConfig(cli) {
  const configPath = cli.config ? path.resolve(PROJECT_ROOT, cli.config) : DEFAULT_CONFIG_PATH;
  const fileConfig = readJsonIfExists(configPath);
  const checkStartTime = cli["no-check-time"] ? "" : String(cli["check-start-time"] ?? fileConfig.checkStartTime ?? "");
  const checkEndTime = cli["no-check-time"] ? "" : String(cli["check-end-time"] ?? fileConfig.checkEndTime ?? "");
  return {
    configPath,
    status: normalizeStatus(cli.status ?? fileConfig.status ?? "failed"),
    count: numberValue(cli.count ?? fileConfig.count, 19),
    expectTotal: numberValue(cli["expect-total"] ?? fileConfig.expectTotal, 19),
    currentPage: numberValue(cli["current-page"] ?? fileConfig.currentPage, 1),
    checkStartTime,
    checkEndTime,
    approvalStartTime: String(cli["approval-start-time"] ?? fileConfig.approvalStartTime ?? ""),
    approvalEndTime: String(cli["approval-end-time"] ?? fileConfig.approvalEndTime ?? ""),
    label: String(cli.label ?? fileConfig.label ?? "fail_20260724_19"),
    port: numberValue(cli.port ?? fileConfig.port, 9222),
    projectRoot: path.resolve(cli["project-root"] ?? fileConfig.projectRoot ?? PROJECT_ROOT),
  };
}

function buildPowerShellArgs(config, extra = {}) {
  const args = [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    COLLECTOR_WRAPPER,
    "-ProjectRoot",
    config.projectRoot,
    "-SkipPageFilter",
    "-Status",
    config.status,
    "-Count",
    String(config.count),
    "-CurrentPage",
    String(config.currentPage),
    "-Label",
    config.label,
  ];
  if (config.checkStartTime || config.checkEndTime) {
    args.push("-CheckStartTime", config.checkStartTime);
    args.push("-CheckEndTime", config.checkEndTime);
  }
  if (config.approvalStartTime || config.approvalEndTime) {
    args.push("-ApprovalStartTime", config.approvalStartTime);
    args.push("-ApprovalEndTime", config.approvalEndTime);
  }
  if (extra.probeOnly) args.push("-ProbeOnly");
  return args;
}

function sanitizedDryRun(config) {
  return {
    config: {
      status: config.status,
      count: config.count,
      expectTotal: config.expectTotal,
      currentPage: config.currentPage,
      checkStartTime: config.checkStartTime,
      checkEndTime: config.checkEndTime,
      approvalStartTime: config.approvalStartTime,
      approvalEndTime: config.approvalEndTime,
      label: config.label,
      port: config.port,
      projectRoot: config.projectRoot,
    },
    powershellArgs: buildPowerShellArgs(config).slice(5),
  };
}

async function cdpRequest(ws, pending, nextIdRef, method, params = {}, sessionId = undefined, timeoutMs = 10000) {
  const id = nextIdRef.value;
  nextIdRef.value += 1;
  const message = sessionId ? { id, sessionId, method, params } : { id, method, params };
  const promise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP request timed out: ${method}`));
    }, timeoutMs);
    pending.set(id, {
      resolve: (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      reject: (error) => {
        clearTimeout(timer);
        reject(error);
      },
    });
  });
  ws.send(JSON.stringify(message));
  return promise;
}

async function getTokenFromFreshTarget(port) {
  const versionResponse = await fetch(`http://127.0.0.1:${port}/json/version`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!versionResponse.ok) throw new Error(`CDP version failed: ${versionResponse.status}`);
  const version = await versionResponse.json();
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  const pending = new Map();
  const nextIdRef = { value: 1 };
  let targetId = "";

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timed out opening browser CDP")), 10000);
    ws.onopen = () => {
      clearTimeout(timer);
      resolve();
    };
    ws.onerror = () => reject(new Error("Browser CDP websocket error"));
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !pending.has(message.id)) return;
      const item = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) item.reject(new Error(JSON.stringify(message.error)));
      else item.resolve(message.result);
    };
  });

  try {
    const created = await cdpRequest(ws, pending, nextIdRef, "Target.createTarget", {
      url: "https://approval.jhddsz.com/admin/#/digital/review",
    });
    targetId = created.targetId;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const attached = await cdpRequest(ws, pending, nextIdRef, "Target.attachToTarget", {
      targetId,
      flatten: true,
    });
    const evaluated = await cdpRequest(
      ws,
      pending,
      nextIdRef,
      "Runtime.evaluate",
      {
        expression: "localStorage.getItem('token') || ''",
        returnByValue: true,
        awaitPromise: false,
      },
      attached.sessionId
    );
    const token = evaluated?.result?.value || "";
    if (!token) throw new Error("没有在临时页面读取到登录态，请确认后台调试浏览器已登录。");
    return token;
  } finally {
    if (targetId) {
      try {
        await cdpRequest(ws, pending, nextIdRef, "Target.closeTarget", { targetId }, undefined, 3000);
      } catch {
        // Temporary target cleanup failure is not collection failure.
      }
    }
    ws.close();
  }
}

function runPowerShell(config, token, extra = {}) {
  const result = spawnSync("powershell.exe", buildPowerShellArgs(config, extra), {
    cwd: config.projectRoot,
    encoding: "utf8",
    env: { ...process.env, GUOBU_AUTH_TOKEN: token },
    windowsHide: true,
  });
  return result;
}

function parseLastJson(stdout) {
  const text = String(stdout || "").trim();
  const start = text.lastIndexOf("\n{");
  const candidate = start >= 0 ? text.slice(start + 1) : text;
  return JSON.parse(candidate);
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  const config = loadConfig(cli);

  if (cli["dry-run"]) {
    process.stdout.write(JSON.stringify(sanitizedDryRun(config), null, 2) + "\n");
    return;
  }

  if (!fs.existsSync(COLLECTOR_WRAPPER)) {
    throw new Error(`Missing collector wrapper: ${COLLECTOR_WRAPPER}`);
  }

  const timeText = config.approvalStartTime || config.approvalEndTime
    ? `approval=${config.approvalStartTime} 到 ${config.approvalEndTime}`
    : `check=${config.checkStartTime} 到 ${config.checkEndTime}`;
  console.log(`准备采集：状态=${config.status}，时间条件=${timeText}，预期=${config.expectTotal}单`);
  const token = await getTokenFromFreshTarget(config.port);

  console.log("正在校验后台 API 总数...");
  const probe = runPowerShell(config, token, { probeOnly: true });
  if (probe.status !== 0) {
    process.stderr.write(probe.stderr || probe.stdout || "");
    process.exit(probe.status || 1);
  }
  const probeJson = parseLastJson(probe.stdout);
  console.log(`API 返回总数：${probeJson.total}`);
  if (config.expectTotal > 0 && Number(probeJson.total) !== Number(config.expectTotal)) {
    throw new Error(`总数不一致：你说当前是 ${config.expectTotal} 单，但 API 返回 ${probeJson.total} 单。为避免采错，已停止。`);
  }
  if (cli["probe-only"]) {
    process.stdout.write(JSON.stringify(probeJson, null, 2) + "\n");
    return;
  }

  console.log("总数一致，开始正式采集...");
  const collect = runPowerShell(config, token);
  process.stdout.write(collect.stdout || "");
  if (collect.status !== 0) {
    process.stderr.write(collect.stderr || "");
    process.exit(collect.status || 1);
  }
}

main().catch((error) => {
  console.error(error && error.message ? error.message : String(error));
  process.exit(1);
});
