"""阈值校准工具（D5）：拿评测集里所有 QA 路径案例的裸检索分（阈值降 0），
看 应答组 vs 应拒组 的分数分布能否被单一阈值分开。

语料（faq.md 常见问法）或检索算法改动后重跑本脚本，确认
MIN_RETRIEVE_SCORE 仍落在可分窗口内——0.40 这个数是 32 条样本上的
一次校准结果，不是永恒真理，窗口塌了就要重新定。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.rag import retriever as R

R.MIN_RETRIEVE_SCORE = 0.0  # 门控全开，只取分数不取通过/拒绝
ret = R.get_retriever()

cases = [json.loads(l) for l in
         (Path(__file__).parent / "eval_set.jsonl").read_text(encoding="utf-8").splitlines()
         if l.strip()]

answer_rows, refuse_rows = [], []
for c in cases:
    exp = c["expect"]
    if "refuse" not in exp:
        continue
    results = ret.retrieve(c["input"])
    top = results[0]["score"] if results else 0.0
    title = results[0]["title"][:22] if results else "-"
    row = (top, c["id"], c["input"][:24], title)
    (refuse_rows if exp["refuse"] else answer_rows).append(row)

print("=== 应答组（refuse=false，分数必须 >= 阈值）===")
for top, cid, inp, title in sorted(answer_rows):
    print(f"  {top:.3f}  {cid:<7} {inp:<26} top命中: {title}")
print("\n=== 应拒组（refuse=true，分数必须 < 阈值）===")
for top, cid, inp, title in sorted(refuse_rows, reverse=True):
    print(f"  {top:.3f}  {cid:<7} {inp:<26} top命中: {title}")

a_min = min(r[0] for r in answer_rows) if answer_rows else None
r_max = max(r[0] for r in refuse_rows) if refuse_rows else None
print(f"\n应答组最低分 {a_min:.3f} | 应拒组最高分 {r_max:.3f}")
print("可分" if a_min > r_max else "重叠——单一阈值分不开，需要语料/算法侧动手")
