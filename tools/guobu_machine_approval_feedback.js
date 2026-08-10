const fs = require("fs");
const path = require("path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const ENDPOINTS = {
  test: "https://test-approval.jhddsz.com/api/cellPhone/26/apply/machineApproval",
  prod: "https://approval.jhddsz.com/api/cellPhone/26/apply/machineApproval",
};

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

function normalizeMode(value) {
  const mode = String(value || "shadow").trim().toLowerCase();
  if (!["off", "shadow", "enforce"].includes(mode)) {
    throw new Error(`Invalid mode: ${value}. Expected off, shadow, or enforce.`);
  }
  return mode;
}

function normalizeEnv(value) {
  const env = String(value || "test").trim().toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(ENDPOINTS, env)) {
    throw new Error(`Invalid env: ${value}. Expected test or prod.`);
  }
  return env;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function parseApplyId(value) {
  const applyId = Number(value);
  if (!Number.isInteger(applyId) || applyId <= 0) {
    throw new Error(`applyId must be a positive integer, got: ${value}`);
  }
  return applyId;
}

function statusFromDecision(value) {
  const decision = String(value || "").trim().toLowerCase();
  if (["1", "pass", "passed", "approve", "approved", "通过", "审核通过"].includes(decision)) return 1;
  if (["2", "reject", "rejected", "fail", "failed", "未通过", "审核未通过"].includes(decision)) return 2;
  throw new Error("decision/status is required: use pass or reject.");
}

function resolveTaskPath(cli) {
  if (cli["task-json"]) return path.resolve(PROJECT_ROOT, cli["task-json"]);
  if (!cli["tasks-dir"]) return "";
  const tasksDir = path.resolve(PROJECT_ROOT, cli["tasks-dir"]);
  const firstTask = fs
    .readdirSync(tasksDir)
    .filter((name) => name.toLowerCase().endsWith(".json"))
    .sort()[0];
  if (!firstTask) throw new Error(`No task JSON files found in ${tasksDir}`);
  return path.join(tasksDir, firstTask);
}

function readTask(cli) {
  const taskPath = resolveTaskPath(cli);
  if (!taskPath) return { task: {}, taskPath: "" };
  return { task: readJson(taskPath), taskPath };
}

function buildRequest({ cli, task }) {
  const applyId = parseApplyId(
    firstDefined(cli["apply-id"], task?.fields?.apply_id, task?.apply_id, task?.source?.apply_id, task?.id)
  );
  const status = cli.status ? statusFromDecision(cli.status) : statusFromDecision(cli.decision);
  const refuseMessage = String(cli["refuse-message"] || "").trim();
  if (status === 2 && !refuseMessage) {
    throw new Error("refuseMessage is required when status is 2/reject.");
  }
  const request = { applyId, status };
  if (status === 2) request.refuseMessage = refuseMessage;
  return request;
}

function resolveOrderNo(task, cli, applyId) {
  return String(
    firstDefined(
      cli["order-no"],
      task?.channel_order_no,
      task?.source?.jl_pay_order,
      task?.source?.wx_pay_order,
      task?.task_id,
      applyId
    )
  );
}

function safeFileStem(value) {
  return String(value || "order").replace(/[^\w.-]+/g, "_").slice(0, 120);
}

function timestampLabel() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(
    now.getMinutes()
  )}${pad(now.getSeconds())}`;
}

function writeReport({ cli, mode, env, endpoint, taskPath, orderNo, request, submitted, response, error }) {
  const outDir = path.resolve(
    PROJECT_ROOT,
    cli["out-dir"] || path.join("reports", "machine_approval_feedback", timestampLabel())
  );
  fs.mkdirSync(outDir, { recursive: true });
  const reportPath = path.join(outDir, `machine_approval_${safeFileStem(orderNo || request.applyId)}.json`);
  const report = {
    mode,
    env,
    endpoint,
    taskPath,
    orderNo,
    request,
    submitted,
    response,
    error,
    generatedAt: new Date().toISOString(),
  };
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");
  return reportPath;
}

function getAuthToken(cli, envVars = process.env) {
  const authEnv = String(cli["auth-env"] || "GUOBU_AUTH_TOKEN");
  const token = envVars[authEnv] || envVars.MACHINE_APPROVAL_AUTH_TOKEN || "";
  if (!token) throw new Error(`Missing Authorization token. Set ${authEnv} or MACHINE_APPROVAL_AUTH_TOKEN.`);
  return token;
}

function validateEnforceGuards({ cli, env, request }) {
  if (String(cli["confirm-apply-id"] || "") !== String(request.applyId)) {
    throw new Error("confirm applyId does not match request applyId.");
  }
  if (env === "prod" && cli["confirm-prod-write"] !== true) {
    throw new Error("prod enforce requires --confirm-prod-write.");
  }
}

async function postMachineApproval({ endpoint, request, token }) {
  const controller = new AbortController();
  const timeoutMs = Number(process.env.MACHINE_APPROVAL_TIMEOUT_MS || 30000);
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: token,
        "Content-Type": "application/json; charset=utf-8",
      },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    const text = await response.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { rawText: text };
    }
    return { httpStatus: response.status, ok: response.ok, body };
  } finally {
    clearTimeout(timeout);
  }
}

function callbackSucceeded(response) {
  if (!response || response.ok === false) return false;
  if (response.httpStatus && (response.httpStatus < 200 || response.httpStatus >= 300)) return false;
  const body = response.body;
  if (body && typeof body === "object") {
    const code = body.status ?? body.code;
    if (code !== undefined && !["0", "200", "success", "SUCCESS"].includes(String(code))) return false;
  }
  return true;
}

async function run(cli) {
  const mode = normalizeMode(cli.mode);
  const env = normalizeEnv(cli.env);
  const endpoint = ENDPOINTS[env];
  const { task, taskPath } = readTask(cli);
  const request = buildRequest({ cli, task });
  const orderNo = resolveOrderNo(task, cli, request.applyId);

  if (mode === "off") {
    return { mode, env, endpoint, orderNo, request, submitted: false, skipped: true };
  }

  if (mode === "shadow") {
    const reportPath = writeReport({ cli, mode, env, endpoint, taskPath, orderNo, request, submitted: false });
    return { mode, env, endpoint, orderNo, request, submitted: false, reportPath };
  }

  validateEnforceGuards({ cli, env, request });
  const token = getAuthToken(cli);
  const response = await postMachineApproval({ endpoint, request, token });
  const reportPath = writeReport({ cli, mode, env, endpoint, taskPath, orderNo, request, submitted: true, response });
  if (!callbackSucceeded(response)) {
    const error = `machineApproval failed: httpStatus=${response.httpStatus}`;
    writeReport({ cli, mode, env, endpoint, taskPath, orderNo, request, submitted: true, response, error });
    const failure = new Error(error);
    failure.reportPath = reportPath;
    throw failure;
  }
  return { mode, env, endpoint, orderNo, request, submitted: true, response, reportPath };
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  const result = await run(cli);
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error && error.message ? error.message : String(error));
    process.exit(1);
  });
}

module.exports = {
  ENDPOINTS,
  buildRequest,
  callbackSucceeded,
  normalizeMode,
  normalizeEnv,
  run,
};
