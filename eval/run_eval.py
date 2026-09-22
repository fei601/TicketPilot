"""
评测跑批器（D5-3）

跑 eval_set.jsonl，逐案例走 ChatService.chat() 端到端，产出：
    - 路由准确率（domain + route 双层）
    - 订单字段准确率（eq / contains / all_contains 三种匹配器）
    - 拒答正确率 + 误拒数（阈值校准的两个方向性指标）
    - 引用率（knowledge_qa 回复含「来源」的占比）
    - 成本口径（llm_calls / latency_ms，直接从 D4-3 结构化日志读取）

为什么成本指标从日志读而不是自己再计时：D4-3 的设计承诺是「字段名即口径」，
评测脚本就是这个口径的第一个消费者——如果日志字段不够评测用，说明埋点白做。
这也验证了 llm_calls 计的是「尝试」（拒答案例应为 0，硬门控省钱的直接证据）。

为什么每个案例新建 :memory: 数据库：管理类回复依赖库内状态（订单数影响文案），
案例间必须互不污染；内存库天然隔离、跑完无痕、不碰生产 data/ticketpilot.db。

为什么 manage 的 action 单独调 classify_order_action 判分，而不是从回复文本猜：
ChatResult 不暴露子动作（只有 route=order_manage），从回复措辞反解是猜谜；
classify_order_action 是纯本地决策函数，白盒判分确定性 100%。
端到端 chat() 照跑——domain/route 判它，action 判决策函数，两层口径分开报告。

为什么异常不中断整轮：32 条里第 17 条网络抖动不该报废前 16 条的结果；
异常记入该案例 error 字段，照常汇总。

用法：
    python eval/run_eval.py                    # 全量（真实 LLM，约 60-90 次调用）
    python eval/run_eval.py --no-llm-router    # 关闭 LLM 路由层（测硬层+关键词兜底）
    python eval/run_eval.py --only mng --no-llm-router       # 冒烟组合
    # 注意：--no-llm-router 不等于零 LLM——关键词兜底把无特征词的消息
    # 送进 AGENT 域后，agent handler 照样真调 LLM（冒烟实测烧了 7 次）。
    # 「拒答 0 LLM」的前提是路由先把域判成 QA，门控只管 QA 域内的事。

结果落盘 eval/results.json（含每条 reply 全文，供人工复核 qa-13 双问、qa-14 错字等）。
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.application.chat_service import ChatService
from ticketpilot.core import router
from ticketpilot.data.database import Database
from ticketpilot.rag import retriever as rag_retriever

BASE = Path(__file__).parent
SET_PATH = BASE / "eval_set.jsonl"
RESULTS_PATH = BASE / "results.json"


class LogCapture(logging.Handler):
    """捕获 chat_service 的结构化 JSON 日志行（event=chat 那条）"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.payload: dict | None = None

    def emit(self, record):
        msg = record.getMessage()
        # 只认 JSON 行：同 logger 还有 "[chat] domain=..." 等人类可读行
        if not msg.startswith("{"):
            return
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return
        if data.get("event") == "chat":
            self.payload = data


def make_service() -> ChatService:
    """每案例一套全新的 内存DB→OrderManager→ChatService，状态零串染"""
    return ChatService(OrderManager(Database(":memory:")))


def field_ok(spec, actual) -> bool:
    """字段匹配器：eq 精确 / contains 子串 / all_contains 全部子串"""
    if isinstance(spec, dict):
        if "eq" in spec:
            return actual == spec["eq"]
        text = "" if actual is None else str(actual)
        if "contains" in spec:
            return str(spec["contains"]) in text
        if "all_contains" in spec:
            return all(x in text for x in spec["all_contains"])
        raise ValueError(f"未知匹配器: {spec}")
    return actual == spec


def run_case(case: dict, use_llm_router: bool, logcap: LogCapture) -> dict:
    expect = case["expect"]
    checks: dict[str, dict] = {}      # 案例级判分（domain/route/action/orders/refuse/citation/gate）
    field_checks: dict[str, dict] = {}  # 字段级判分（仅 order_block 有）

    svc = make_service()
    logcap.payload = None
    error = None
    try:
        result = svc.chat(case["input"], use_llm_router=use_llm_router)
    except Exception as e:  # noqa: BLE001 —— 单案例失败不报废整轮，见 docstring
        result, error = None, f"{type(e).__name__}: {e}"

    log = logcap.payload or {}
    got_domain = log.get("domain")
    got_route = log.get("route") if result is None else result.route

    def check(name, expected, got):
        checks[name] = {"pass": expected == got, "expected": expected, "got": got}

    if result is None:
        checks["error"] = {"pass": False, "expected": "正常返回", "got": error}
    else:
        check("domain", expect["domain"], got_domain)
        if "route" in expect:
            check("route", expect["route"], got_route)
        if "routes" in expect:
            # agent 域两条合法路由：工具循环 / 降级直查，都算对
            checks["route"] = {"pass": got_route in expect["routes"],
                               "expected": expect["routes"], "got": got_route}
        if "action" in expect:
            # 白盒判分子动作，见 docstring「为什么单独调 classify_order_action」
            got_action = router.classify_order_action(case["input"])
            check("action", expect["action"], got_action)
        if "orders" in expect:
            check("orders", expect["orders"], len(result.orders))
        if "orders_created" in expect:
            check("orders_created", expect["orders_created"], len(result.orders))

        if "refuse" in expect:
            got_refuse = got_route == "knowledge_qa_refused"
            check("refuse", expect["refuse"], got_refuse)
            if expect["refuse"]:
                # 硬门控的省钱承诺：拒答后 QA handler 不再调 LLM 生成回答。
                # 全链路 llm_calls 允许 =1：那是路由分类层的合法开销
                # （首跑把口径写成全链路=0，ref-1/3 正确拒答也被判负——
                # 判分器自己的口径 bug，评测集第一次跑就抓到了我自己的错）
                budget = 1 if use_llm_router else 0
                checks["gate_no_answer_llm"] = {
                    "pass": (log.get("llm_calls") or 0) <= budget,
                    "expected": f"<={budget}", "got": log.get("llm_calls")}
            elif got_route == "knowledge_qa":
                # 引用只在「确实走了 QA 且答了」时判，路由错了不重复扣分
                checks["citation"] = {"pass": "来源" in (result.reply or ""),
                                      "expected": True, "got": "来源" in (result.reply or "")}

        if "fields" in expect:
            if result.orders:
                order = result.orders[0]
                for fname, spec in expect["fields"].items():
                    actual = getattr(order, fname, None)
                    field_checks[fname] = {"pass": field_ok(spec, actual),
                                           "expected": spec, "got": actual}
            else:
                # 没产出订单：全部字段记失败，而不是跳过（跳过=虚高准确率）
                for fname in expect["fields"]:
                    field_checks[fname] = {"pass": False,
                                           "expected": expect["fields"][fname],
                                           "got": "<无订单>"}

    all_checks = list(checks.values()) + list(field_checks.values())
    return {
        "id": case["id"],
        "type": case["type"],
        "synthetic": case.get("synthetic", False),
        "input": case["input"],
        "note": case.get("note"),
        "passed": bool(all_checks) and all(c["pass"] for c in all_checks),
        "checks": checks,
        "field_checks": field_checks,
        "domain": got_domain,
        "route": got_route,
        "llm_calls": log.get("llm_calls"),
        "latency_ms": log.get("latency_ms"),
        "orders_created": log.get("orders_created"),
        "reply": result.reply if result else None,
        "error": error,
    }


def summarize(records: list[dict]) -> dict:
    n = len(records)
    passed = sum(1 for r in records if r["passed"])

    # 路由准确率：domain+route 都对的案例占比（评测集的第一指标）
    routing_recs = [r for r in records
                    if "domain" in r["checks"] and "route" in r["checks"]]
    routing_ok = sum(1 for r in routing_recs
                     if r["checks"]["domain"]["pass"] and r["checks"]["route"]["pass"])

    # 字段准确率：跨案例按字段计数（一个 blk 案例 6 个字段就是 6 票）
    f_all = [fc for r in records for fc in r["field_checks"].values()]
    f_ok = sum(1 for fc in f_all if fc["pass"])

    # 拒答双向口径：refuse=true 该拒没拒（漏拒/编造风险）；
    # refuse=false 被拒了（误拒/阈值过高）——MIN_RETRIEVE_SCORE 校准就看这两个数
    should_refuse = [r for r in records if r.get("checks", {}).get("refuse", {}).get("expected") is True]
    should_answer = [r for r in records if r.get("checks", {}).get("refuse", {}).get("expected") is False]
    missed_refusal = [r["id"] for r in should_refuse if not r["checks"]["refuse"]["pass"]]
    false_refusal = [r["id"] for r in should_answer if r["route"] == "knowledge_qa_refused"]

    cited = [r for r in records if "citation" in r.get("checks", {})]
    llm_calls = [r["llm_calls"] for r in records if r["llm_calls"] is not None]
    latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]

    return {
        "cases": n,
        "case_pass": f"{passed}/{n}",
        "routing_acc": f"{routing_ok}/{len(routing_recs)}" if routing_recs else "n/a",
        "field_acc": f"{f_ok}/{len(f_all)}" if f_all else "n/a",
        "missed_refusal": missed_refusal or "无",
        "false_refusal": false_refusal or "无",
        "citation_rate": (f"{sum(1 for r in cited if r['checks']['citation']['pass'])}/{len(cited)}"
                          if cited else "n/a"),
        "gate_zero_llm_ok": all(r["checks"]["gate_zero_llm"]["pass"]
                                for r in should_refuse if "gate_zero_llm" in r["checks"]),
        "total_llm_calls": sum(llm_calls),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "errors": [r["id"] for r in records if r["error"]] or "无",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="TicketPilot 评测跑批器")
    ap.add_argument("--only", help="只跑 id 以这些前缀开头的案例，逗号分隔（如 mng,ref）")
    ap.add_argument("--no-llm-router", action="store_true",
                    help="关闭 LLM 路由层（classify_domain use_llm=False），测硬层+关键词兜底")
    args = ap.parse_args()

    cases = [json.loads(line) for line in
             SET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        prefixes = tuple(p.strip() for p in args.only.split(","))
        cases = [c for c in cases if c["id"].startswith(prefixes)]
    if not cases:
        sys.exit("没有匹配的案例，检查 --only 前缀")

    logcap = LogCapture()
    cs_logger = logging.getLogger("ticketpilot.application.chat_service")
    cs_logger.addHandler(logcap)
    cs_logger.setLevel(logging.INFO)  # 只开这个 logger，root 不动，控制台不被日志刷屏

    use_llm_router = not args.no_llm_router
    mode = "llm_router=on" if use_llm_router else "llm_router=off(冒烟/硬层)"
    print(f"评测开始：{len(cases)} 条案例，{mode}，"
          f"MIN_RETRIEVE_SCORE={getattr(rag_retriever, 'MIN_RETRIEVE_SCORE', '?')}\n")

    records = []
    t0 = time.monotonic()
    for case in cases:
        rec = run_case(case, use_llm_router, logcap)
        records.append(rec)
        mark = "PASS" if rec["passed"] else "FAIL"
        detail = ""
        if not rec["passed"]:
            failed = [f"{k}(want={v['expected']!r} got={v['got']!r})"
                      for k, v in {**rec["checks"], **rec["field_checks"]}.items()
                      if not v["pass"]]
            detail = " | " + "; ".join(failed[:4])
        print(f"[{mark}] {rec['id']:<7} route={rec['route']} "
              f"llm={rec['llm_calls']} {rec['latency_ms']}ms{detail}")

    summary = summarize(records)
    elapsed = round(time.monotonic() - t0, 1)

    print(f"\n{'=' * 56}\n汇总（{mode}，总耗时 {elapsed}s）")
    for k, v in summary.items():
        print(f"  {k:<20} {v}")

    RESULTS_PATH.write_text(json.dumps({
        "meta": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "use_llm_router": use_llm_router,
            "min_retrieve_score": getattr(rag_retriever, "MIN_RETRIEVE_SCORE", None),
            "case_count": len(records),
            "elapsed_s": elapsed,
        },
        "summary": summary,
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细（含 reply 全文）已写入 {RESULTS_PATH.name}")


if __name__ == "__main__":
    main()
