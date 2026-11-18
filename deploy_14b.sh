#!/usr/bin/env bash
# deploy_14b.sh — 在 3090 主机部署 Qwen2.5-14B-Instruct 量化版 + vLLM，并执行 A1 合法率实测
#
# 运行位置：仅在 3090 主机执行（当前开发机无 GPU，禁止在此运行）
# 上游依据：W1-plan.md §1.3 / §3（A1）、ARCHITECTURE.md §7/§9、W1-env-check.md §4
# 安全约定：本脚本不含任何 API Key，不访问云端，模型 endpoint 仅监听 127.0.0.1
# 使用方法：
#   chmod +x deploy_14b.sh
#   MODEL_PATH=/mnt/nas/models/Qwen2.5-14B-Instruct-AWQ ./deploy_14b.sh deploy   # 启动服务
#   ./deploy_14b.sh test                                                        # 跑 A1 实测
#   ./deploy_14b.sh all                                                         # 部署 + 实测
#   ./deploy_14b.sh stop                                                        # 停止服务
# 关键说明：A1 结论决定技术路线——不达标则按 ACTION 提示降级 7B 或转云端为主。

set -euo pipefail

# ---------------------------------------------------------------------------
# 配置区（均可用环境变量覆盖，不硬编码任何密钥或机器专属绝对路径）
# ---------------------------------------------------------------------------
MODEL_PATH="${MODEL_PATH:-/mnt/nas/models/Qwen2.5-14B-Instruct-AWQ}"
SERVED_MODEL="${SERVED_MODEL:-qwen2.5-14b-instruct}"
QUANTIZATION="${QUANTIZATION:-awq}"          # 若权重是 GPTQ 则改为 gptq
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
GPU_UTIL="${GPU_UTIL:-0.85}"                  # 3090 24G，14B 量化留有余量
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
DTYPE="${DTYPE:-half}"
VLLM_LOG="${VLLM_LOG:-logs/vllm.log}"
PID_FILE="${PID_FILE:-logs/vllm.pid}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENDPOINT="http://${HOST}:${PORT}/v1/chat/completions"
A1_TASKS=10
A1_MIN_VALID=8
A1_MAX_LATENCY=5.0

log()  { echo "[deploy-14b] $*"; }
fail() { echo "[deploy-14b] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------
precheck() {
  command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi 不可用：请在 3090 主机运行，当前机器无 GPU"
  local gpu
  gpu="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -n1)"
  echo "$gpu" | grep -qi "3090" || log "WARN: 未检测到 RTX 3090，实测结果需按实际硬件判断：$gpu"
  python3 -c 'import vllm' >/dev/null 2>&1 || fail "vLLM 未安装：pip install vllm（由人类确认后安装，脚本不自行安装依赖）"
  [ -d "$MODEL_PATH" ] || fail "模型权重目录不存在：$MODEL_PATH（请先下载到 NAS 并设置 MODEL_PATH）"
  [ -f "$MODEL_PATH/config.json" ] || fail "权重目录缺少 config.json，请确认是完整的 vLLM 可用模型目录"
  mkdir -p "$(dirname "$VLLM_LOG")" "$(dirname "$PID_FILE")"
  log "前置检查通过：GPU=$gpu 模型=$MODEL_PATH 量化=$QUANTIZATION"
}

# ---------------------------------------------------------------------------
# 启动 / 停止 vLLM（OpenAI 兼容 endpoint）
# ---------------------------------------------------------------------------
start_server() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "服务已在运行，PID=$(cat "$PID_FILE")"
    return
  fi
  log "启动 vLLM：$SERVED_MODEL @ ${HOST}:${PORT}"
  nohup python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --served-model-name "$SERVED_MODEL" \
    --quantization "$QUANTIZATION" \
    --dtype "$DTYPE" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_UTIL" \
    --host "$HOST" \
    --port "$PORT" \
    > "$VLLM_LOG" 2>&1 &
  echo $! > "$PID_FILE"
  log "PID=$(cat "$PID_FILE")，日志=$VLLM_LOG，等待 endpoint 就绪..."
  for _ in $(seq 1 60); do
    if curl -sf "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
      log "服务就绪"
      return
    fi
    sleep 2
  done
  fail "60 次健康检查未通过，请查看日志：$VLLM_LOG"
}

stop_server() {
  if [ -f "$PID_FILE" ]; then
    local pid; pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null && log "已停止 vLLM PID=$pid" || log "进程 $pid 已不存在"
    rm -f "$PID_FILE"
  else
    log "无 PID 文件，服务未由此脚本启动"
  fi
}

# ---------------------------------------------------------------------------
# A1 实测：10 个任务的结构化输出合法率 + 延迟
# ---------------------------------------------------------------------------
run_a1() {
  python3 - "$ENDPOINT" "$SERVED_MODEL" "$A1_TASKS" "$A1_MIN_VALID" "$A1_MAX_LATENCY" "$REPO_ROOT" <<'PY'
import json, sys, time, urllib.request

endpoint, model = sys.argv[1], sys.argv[2]
n_tasks, min_valid, max_latency = int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
repo_root = sys.argv[6]

TASKS = [
    "统计指定目录下的文件数量与类型分布",
    "列出当前目录的所有子目录",
    "读取指定文本文件并统计行数",
    "找出最近修改的 10 个文件",
    "统计代码中 TODO 注释的数量",
    "检查磁盘占用最大的文件",
    "生成目录结构的 Markdown 报告",
    "对比两个目录的文件差异",
    "查找重复文件并列出路径",
    "统计日志文件中 ERROR 出现的次数",
]

PROMPT_TMPL = (
    "你是任务规划器。只输出 JSON，不要输出其他文字。\n"
    "字段：task_id(string)/goal(string)/steps(array)，"
    "steps 每项含 step_id(格式 step-<n>)/action/tool/args(object)/risk_level(low|medium|high)，"
    "risk_level=high 时必须有 requires_confirm=true。\n"
    "tool 只能是 dir_scan/file_read/script_run/report_write/log_record 之一。\n"
    "task_id: {tid}\n任务: {task}"
)

schema_path = f"{repo_root}/src/schema/plan.schema.json"
try:
    import jsonschema
    with open(schema_path, encoding="utf-8") as f:
        validator = jsonschema.Draft7Validator(json.load(f))
    def validate(d):
        return not list(validator.iter_errors(d))
except Exception as e:  # 缺 jsonschema 时退化为字段级校验
    print(f"[a1] WARN: Schema 校验不可用({type(e).__name__})，使用字段级校验")
    def validate(d):
        if not all(k in d for k in ("task_id", "goal", "steps")) or not isinstance(d["steps"], list) or not d["steps"]:
            return False
        req = {"step_id", "action", "tool", "args", "risk_level"}
        for s in d["steps"]:
            if not isinstance(s, dict) or not req.issubset(s):
                return False
            if s["risk_level"] not in ("low", "medium", "high"):
                return False
            if s["risk_level"] == "high" and s.get("requires_confirm") is not True:
                return False
        return True

valid, latencies, details = 0, [], []
for i, task in enumerate(TASKS, 1):
    tid = f"a1-{i:02d}"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT_TMPL.format(tid=tid, task=task)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic()
    ok, note = False, ""
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        dt = time.monotonic() - t0
        latencies.append(dt)
        data = json.loads(text)
        ok = validate(data)
        note = "ok" if ok else "schema 不合法"
    except Exception as e:
        dt = time.monotonic() - t0
        latencies.append(dt)
        note = f"{type(e).__name__}: {str(e)[:60]}"
    valid += int(ok)
    details.append((tid, "PASS" if ok else "FAIL", round(dt, 2), note))

avg = sum(latencies) / len(latencies)
print("\n[a1] 逐任务结果: tid | 合法 | 延迟(s) | 备注")
for row in details:
    print("     ", " | ".join(str(x) for x in row))
print(f"\n[a1] 合法率: {valid}/{n_tasks}  平均延迟: {avg:.2f}s")
passed = valid >= min_valid and avg < max_latency
if passed:
    print(f"[a1] 结论: PASS（≥{min_valid} 合法且 <{max_latency}s）→ 继续 14B。")
else:
    print(f"[a1] 结论: FAIL → 按 W1-plan.md §6 预案：")
    print("     1) 先用同脚本降级 7B 测 A1：MODEL_PATH=<7B路径> SERVED_MODEL=qwen2.5-7b-instruct ./deploy_14b.sh all")
    print("     2) 7B 仍不达标 → 转云端为主、本地仅可选，并出 ADR")
sys.exit(0 if passed else 1)
PY
}

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
用法: $0 {deploy|test|all|stop}
  deploy  前置检查 + 启动 vLLM 服务
  test    仅跑 A1 合法率实测（需服务已就绪）
  all     deploy + test
  stop    停止 vLLM 服务
环境变量: MODEL_PATH SERVED_MODEL QUANTIZATION DTYPE HOST PORT GPU_UTIL MAX_MODEL_LEN VLLM_LOG PID_FILE
EOF
}

case "${1:-}" in
  deploy) precheck; start_server ;;
  test)   run_a1 ;;
  all)    precheck; start_server; run_a1 ;;
  stop)   stop_server ;;
  *)      usage; exit 1 ;;
esac
