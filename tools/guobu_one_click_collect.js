const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_CONFIG_PATH = path.join(PROJECT_ROOT, "config", "guobu_collect_one_click.json");
const SKILL_ROOT = path.join(process.env.USERPROFILE || "", ".codex", "skills", "guobu-examine-api-collector");
const COLLECTOR_WRAPPER = path.join(SKILL_ROOT, "scripts", "collect_guobu_filtered.ps1");
const REVIEW_URL = "https://approval.jhddsz.com/admin/#/digital/review";

const AUDIT_FIELD_WHITELIST = [
  "apply_id",
  "product_type",
  "cate_code",
  "cate_code_name",
  "goods_name",
  "brand",
  "model",
  "system_sn",
  "imei1",
  "imei2",
  "barcode",
  "is_home_appliance",
  "address",
  "status",
  "flow_status",
  "source_flow_status",
];
const IMAGE_GROUP_WHITELIST = ["商品照片", "拆封照片", "SN码采集/激活照片"];
const IMAGE_GROUP_ALIASES = {
  商品照片: ["商品照片"],
  拆封照片: ["拆封照片"],
  "SN码采集/激活照片": ["SN码采集/激活照片", "SN码采集 / 激活照片"],
};
const IMAGE_FIELD_WHITELIST = ["image_id", "title", "local_path", "source_url", "download_error"];
const SOURCE_FIELD_WHITELIST = [
  "collector",
  "source_url",
  "apply_id",
  "jl_pay_order",
  "api_status_param",
  "current_page",
  "expected_label",
  "collected_at",
  "image_role_rule",
  "detail_error",
];
const REQUIRED_OUTPUT_PATHS = [
  "channel_order_no",
  "fields.product_type",
  "fields.system_sn",
  "fields.address",
  "fields.flow_status",
  "image_groups.商品照片",
  "image_groups.拆封照片",
  "image_groups.SN码采集/激活照片",
];
const PENDING_OWNER_CONFIRMATION = [
  "price",
  "subsidy_price",
  "apply_status",
  "examine_status",
  "settle_status",
  "coupon_status",
  "check_name",
  "check_time",
];

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

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
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

function normalizeCollectionMode(value) {
  const mode = String(value || "shadow").trim().toLowerCase();
  if (!["off", "shadow", "enforce"].includes(mode)) {
    throw new Error(`Invalid collection mode: ${value}. Expected off, shadow, or enforce.`);
  }
  return mode;
}

function loadConfig(cli) {
  const configPath = cli.config ? path.resolve(PROJECT_ROOT, cli.config) : DEFAULT_CONFIG_PATH;
  const fileConfig = readJsonIfExists(configPath);
  const checkStartTime = cli["no-check-time"] ? "" : String(cli["check-start-time"] ?? fileConfig.checkStartTime ?? "");
  const checkEndTime = cli["no-check-time"] ? "" : String(cli["check-end-time"] ?? fileConfig.checkEndTime ?? "");
  return {
    configPath,
    status: normalizeStatus(cli.status ?? fileConfig.status ?? "failed"),
    collectionMode: normalizeCollectionMode(
      cli["collection-mode"] ?? process.env.COLLECTION_INTERFACE_MODE ?? fileConfig.collectionMode ?? "shadow"
    ),
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
      collectionMode: config.collectionMode,
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
    whitelist: {
      fields: AUDIT_FIELD_WHITELIST,
      imageGroups: IMAGE_GROUP_WHITELIST,
      pendingOwnerConfirmation: PENDING_OWNER_CONFIRMATION,
    },
    powershellArgs: buildPowerShellArgs(config).slice(5),
  };
}

function envQuote(value) {
  return `"${String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function hardenTokenEnvFile(filePath) {
  if (process.platform !== "win32") {
    fs.chmodSync(filePath, 0o600);
    return;
  }
  const account = process.env.USERDOMAIN && process.env.USERNAME
    ? `${process.env.USERDOMAIN}\\${process.env.USERNAME}`
    : process.env.USERNAME;
  if (!account) {
    fs.rmSync(filePath, { force: true });
    throw new Error("Cannot restrict token env ACL without current Windows user.");
  }
  const result = spawnSync("icacls", [filePath, "/inheritance:r", "/grant:r", `${account}:F`], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0) {
    fs.rmSync(filePath, { force: true });
    throw new Error("Failed to restrict token env ACL.");
  }
}

function saveTokenEnv(filePath, token) {
  if (!filePath) return;
  const resolved = path.resolve(filePath);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  const body = [
    `GUOBU_AUTH_TOKEN=${envQuote(token)}`,
    `MACHINE_APPROVAL_AUTH_TOKEN=${envQuote(token)}`,
    "",
  ].join("\n");
  fs.writeFileSync(resolved, body, "utf8");
  hardenTokenEnvFile(resolved);
  console.log(`后台登录态已写入：${resolved}`);
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function valueAt(task, key) {
  return firstDefined(task?.fields?.[key], task?.[key], task?.source?.[key]);
}

function compactObject(value) {
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (item !== undefined && item !== null) result[key] = item;
  }
  return result;
}

function sanitizeImage(image) {
  const result = {};
  for (const key of IMAGE_FIELD_WHITELIST) {
    if (image?.[key] !== undefined && image?.[key] !== null && image?.[key] !== "") result[key] = image[key];
  }
  return result;
}

function sanitizeImageGroups(imageGroups = {}) {
  const result = {};
  for (const groupName of IMAGE_GROUP_WHITELIST) {
    const images = (IMAGE_GROUP_ALIASES[groupName] || [groupName]).flatMap((alias) =>
      Array.isArray(imageGroups[alias]) ? imageGroups[alias] : []
    );
    result[groupName] = images.map(sanitizeImage);
  }
  return result;
}

function sanitizeTask(task) {
  const fields = {};
  for (const key of AUDIT_FIELD_WHITELIST) {
    const value = valueAt(task, key);
    if (value !== undefined && value !== null) fields[key] = value;
  }
  const source = {};
  for (const key of SOURCE_FIELD_WHITELIST) {
    const value = task?.source?.[key] ?? task?.[key];
    if (value !== undefined && value !== null && value !== "") source[key] = value;
  }
  return compactObject({
    task_id: task?.task_id,
    channel_order_no: firstDefined(task?.channel_order_no, task?.jl_pay_order, task?.wx_pay_order, task?.order_no, task?.source?.jl_pay_order),
    scene: task?.scene,
    expected_label: task?.expected_label,
    fields,
    image_groups: sanitizeImageGroups(task?.image_groups),
    source: compactObject(source),
  });
}

function fieldValueByPath(task, fieldPath) {
  const parts = fieldPath.split(".");
  let current = task;
  for (const part of parts) {
    current = current?.[part];
  }
  return current;
}

function isMissingRequiredValue(value) {
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || value === "";
}

function compareAndWriteWhitelistedTasks(options) {
  const tasksDir = path.resolve(options.tasksDir);
  const sanitizedTasksDir = path.resolve(options.sanitizedTasksDir || path.join(path.dirname(tasksDir), "tasks_whitelist_shadow"));
  const reportPath = path.resolve(
    options.reportPath || path.join(path.dirname(tasksDir), "field_whitelist_shadow_report.json")
  );
  if (!fs.existsSync(tasksDir)) throw new Error(`Missing tasks directory: ${tasksDir}`);

  fs.mkdirSync(sanitizedTasksDir, { recursive: true });
  const taskFiles = fs.readdirSync(tasksDir).filter((file) => file.toLowerCase().endsWith(".json")).sort();
  const oldTopLevelFields = new Set();
  const oldCandidateFields = new Set();
  const newTopLevelFields = new Set();
  const newFieldKeys = new Set();
  const missingRequiredCounts = Object.fromEntries(REQUIRED_OUTPUT_PATHS.map((item) => [item, 0]));
  const tasksWithMissingRequired = [];

  for (const file of taskFiles) {
    const task = JSON.parse(fs.readFileSync(path.join(tasksDir, file), "utf8"));
    const sanitized = sanitizeTask(task);
    writeJson(path.join(sanitizedTasksDir, file), sanitized);

    for (const key of Object.keys(task)) oldTopLevelFields.add(key);
    for (const key of Object.keys(task.fields || {})) oldCandidateFields.add(key);
    for (const key of Object.keys(task)) oldCandidateFields.add(key);
    for (const key of Object.keys(sanitized)) newTopLevelFields.add(key);
    for (const key of Object.keys(sanitized.fields || {})) newFieldKeys.add(key);

    const missing = [];
    for (const requiredPath of REQUIRED_OUTPUT_PATHS) {
      const value = fieldValueByPath(sanitized, requiredPath);
      if (isMissingRequiredValue(value)) {
        missingRequiredCounts[requiredPath] += 1;
        missing.push(requiredPath);
      }
    }
    if (missing.length) {
      tasksWithMissingRequired.push({
        task_id: sanitized.task_id || file.replace(/\.json$/i, ""),
        channel_order_no: sanitized.channel_order_no || "",
        missing,
      });
    }
  }

  const report = {
    mode: "shadow",
    sourceTasksDir: tasksDir,
    sanitizedTasksDir,
    sampleCount: taskFiles.length,
    oldTopLevelFieldCount: oldTopLevelFields.size,
    newTopLevelFieldCount: newTopLevelFields.size,
    oldFieldCount: oldCandidateFields.size,
    newFieldCount: newFieldKeys.size,
    oldTopLevelFields: [...oldTopLevelFields].sort(),
    newTopLevelFields: [...newTopLevelFields].sort(),
    removedTopLevelFields: [...oldTopLevelFields].filter((key) => !newTopLevelFields.has(key)).sort(),
    newFieldKeys: [...newFieldKeys].sort(),
    removedFieldKeys: [...oldCandidateFields]
      .filter((key) => !newFieldKeys.has(key) && !newTopLevelFields.has(key))
      .sort(),
    missingRequiredCounts,
    tasksWithMissingRequired,
    pendingOwnerConfirmation: PENDING_OWNER_CONFIRMATION,
  };
  writeJson(reportPath, report);
  return { ...report, reportPath };
}

function assertInside(parent, child) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Refusing to modify path outside ${parent}: ${child}`);
  }
}

function applyCollectedOutputMode(config) {
  if (config.collectionMode === "off") return null;
  const dataRoot = path.join(config.projectRoot, "data", config.label);
  const tasksDir = path.join(dataRoot, "tasks");
  const collectorReportDir = path.join(config.projectRoot, "reports", "collector_api", config.label);
  const reportPath = path.join(collectorReportDir, "field_whitelist_shadow_report.json");
  const sanitizedTasksDir =
    config.collectionMode === "shadow" ? path.join(dataRoot, "tasks_whitelist_shadow") : path.join(dataRoot, "tasks_whitelist_enforce");
  let report = compareAndWriteWhitelistedTasks({ tasksDir, sanitizedTasksDir, reportPath });
  report = {
    ...report,
    rawResponse: handleRawResponseFile({
      collectorReportDir,
      mode: config.collectionMode,
      whitelistReportPath: reportPath,
    }),
  };
  writeJson(reportPath, report);
  if (config.collectionMode !== "enforce") return report;

  assertInside(dataRoot, tasksDir);
  assertInside(dataRoot, sanitizedTasksDir);
  const backupDir = path.join(dataRoot, `tasks_before_whitelist_${new Date().toISOString().replace(/[:.]/g, "-")}`);
  fs.cpSync(tasksDir, backupDir, { recursive: true });
  fs.rmSync(tasksDir, { recursive: true, force: true });
  fs.renameSync(sanitizedTasksDir, tasksDir);
  return { ...report, mode: "enforce", backupTasksDir: backupDir, sanitizedTasksDir: tasksDir };
}

function handleRawResponseFile({ collectorReportDir, mode, whitelistReportPath }) {
  const rawResponsePath = path.join(collectorReportDir, "raw_response.json");
  if (!fs.existsSync(rawResponsePath)) {
    return { fullRawResponseRedacted: false, rawResponsePath, status: "not_found" };
  }
  if (mode !== "enforce") {
    return {
      fullRawResponseRedacted: false,
      rawResponsePath,
      status: "shadow_detected",
      note: "shadow mode keeps the original collector artifact unchanged; enforce mode replaces it with a redacted marker.",
    };
  }

  const backupPath = path.join(
    collectorReportDir,
    `raw_response_before_whitelist_${new Date().toISOString().replace(/[:.]/g, "-")}.json`
  );
  fs.renameSync(rawResponsePath, backupPath);
  writeJson(rawResponsePath, {
    fullRawResponseRedacted: true,
    collectionMode: "enforce",
    backupPath,
    whitelistReportPath,
    message: "Full raw API response was removed from enforced whitelist output.",
  });
  return {
    fullRawResponseRedacted: true,
    rawResponsePath,
    backupPath,
    status: "redacted",
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
      url: REVIEW_URL,
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
    if (!token) throw new Error("后台页已打开，但没有读到登录态；请确认 approval.jhddsz.com 已登录后重试。");
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

async function tokenCandidatesFromBrowser(config) {
  try {
    return [await getTokenFromFreshTarget(config.port)];
  } catch (error) {
    console.log(`CDP token read failed: ${error.message}`);
    return [];
  }
}

function runPowerShell(config, token, extra = {}) {
  return spawnSync("powershell.exe", buildPowerShellArgs(config, extra), {
    cwd: config.projectRoot,
    encoding: "utf8",
    env: { ...process.env, GUOBU_AUTH_TOKEN: token },
    windowsHide: true,
  });
}

function parseLastJson(stdout) {
  const text = String(stdout || "").trim();
  const start = text.lastIndexOf("\n{");
  const candidate = start >= 0 ? text.slice(start + 1) : text;
  return JSON.parse(candidate);
}

async function findUsableToken(config) {
  const candidates = await tokenCandidatesFromBrowser(config);
  if (!candidates.length) {
    throw new Error("没有找到可用的后台登录态；请在 Edge/Chrome 登录 approval.jhddsz.com 后重试，或启动 9222 调试浏览器。");
  }

  for (const candidate of candidates) {
    const variants = String(candidate).startsWith("Bearer ") ? [candidate] : [candidate, `Bearer ${candidate}`];
    for (const variant of variants) {
      const probe = runPowerShell(config, variant, { probeOnly: true });
      if (probe.status !== 0) continue;
      try {
        const probeJson = parseLastJson(probe.stdout);
        if (Number.isFinite(Number(probeJson.total))) return { token: variant, probeJson };
      } catch {
        // Try the next candidate without logging secrets.
      }
    }
  }
  throw new Error("找到过浏览器登录态候选，但没有任何一个通过后台 API 校验；请重新登录 approval.jhddsz.com 后重试。");
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  const config = loadConfig(cli);

  if (cli["dry-run"]) {
    process.stdout.write(JSON.stringify(sanitizedDryRun(config), null, 2) + "\n");
    return;
  }

  if (cli["shadow-compare-tasks-dir"]) {
    const report = compareAndWriteWhitelistedTasks({
      tasksDir: cli["shadow-compare-tasks-dir"],
      reportPath: cli["shadow-report-path"],
      sanitizedTasksDir: cli["sanitized-tasks-dir"],
    });
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    return;
  }

  if (!fs.existsSync(COLLECTOR_WRAPPER)) {
    throw new Error(`Missing collector wrapper: ${COLLECTOR_WRAPPER}`);
  }

  const timeText =
    config.approvalStartTime || config.approvalEndTime
      ? `approval=${config.approvalStartTime} 到 ${config.approvalEndTime}`
      : `check=${config.checkStartTime} 到 ${config.checkEndTime}`;
  console.log(
    `准备采集：状态=${config.status}，时间条件=${timeText}，预期=${config.expectTotal}单，采集字段模式=${config.collectionMode}`
  );

  console.log("正在校验后台 API 总数...");
  const { token, probeJson } = await findUsableToken(config);
  console.log(`API 返回总数：${probeJson.total}`);
  if (cli["save-token-env"]) {
    saveTokenEnv(cli["save-token-env"], token);
  }
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

  const postProcessReport = applyCollectedOutputMode(config);
  if (postProcessReport) {
    console.log(`字段白名单 ${postProcessReport.mode} 报告：${postProcessReport.reportPath}`);
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error && error.message ? error.message : String(error));
    process.exit(1);
  });
}

module.exports = {
  AUDIT_FIELD_WHITELIST,
  IMAGE_GROUP_WHITELIST,
  sanitizeTask,
  compareAndWriteWhitelistedTasks,
  loadConfig,
};
