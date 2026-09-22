"""
评测集脱敏流水线（D5-1）

    raw_messages.txt（真实消息，gitignore，永不入库）
        → python eval/mask_eval.py → masked_messages.txt（证件号/手机号已换假号）
        → 人工把真名换成张三/李四（同一假号=同一客户，假名保持一致）

为什么复用 redact_pii/restore_pii 而不是 mask_pii_in_text 打星：
星号证件号不再命中 18 位正则——路由硬路径不触发、订单字段解析不出，
评测消息就"死"了。脱敏必须保结构：假号和真号一样走完整条流水线，
只是数值无害。这也是"用自己系统的 PII 工具链构建评测集"的叙事本体。

为什么同值同映射：同一客户的证件号跨多条消息重复出现时必须映射到
同一个假号，否则"同客户先报单再改单"的多轮评测场景直接断裂。

为什么本脚本要有通行证号正则、而不复用 privacy 的：真实数据里存在
港澳通行证（字母+8位），privacy.py 只认 18 位身份证——评测集是要
入库的文件，真 PII 零容忍，所以流水线必须盖住数据里实际出现的形态。
（生产侧 privacy.py 的同一缺口已记入审计清单，另行修复。）

为什么 12-17 位残段只记行号不自动替换：残段可能是半截证件号（真 PII），
也可能是订单号/票号（不是）——机器分不清的，交人工裁决，不静默放过。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.core.privacy import redact_pii, restore_pii

BASE = Path(__file__).parent
RAW = BASE / "raw_messages.txt"
OUT = BASE / "masked_messages.txt"

# 港澳台通行证/回乡证类：字母+8位数字。前后不接字母数字，避免误伤
_PERMIT_RE = re.compile(r'(?<![A-Za-z0-9])[HCWSPEDFGhcwspedfg]\d{8}(?!\d)')
# 12-17 位数字残段：半截证件号嫌疑，仅标记行号交人工
_PARTIAL_RE = re.compile(r'\d{12,17}(?!\d)')

_TEMPLATE = """\
# ── 评测集原始消息收集箱（本文件已 gitignore，真实 PII 永不入库）──
# 用法：从群聊记录直接复制客户消息，一行一条，30 条起
# 覆盖面：订单报单 / 管理指令（查·确认·改·删）/ 规则咨询 / 闲聊 / 对抗边界
#        （半截证件号、订单+咨询混在一句、错别字、语音转写碎片——都要有）
# 别挑好看的——真实分布就要含烂消息；# 开头行和空行会被脚本跳过
# 贴完运行: python eval/mask_eval.py
"""

# 假号池：全部能通过 _ID_CARD_RE / _PHONE_RE，结构与真号同构
FAKE_IDS = [
    "310101199001011234", "310101199203054321", "310101198812120011",
    "310101199507162222", "310101200001013333", "310101198711224444",
    "310101199309095555", "310101199801016666", "310101200202027777",
    "310101199612128888",
]
FAKE_PHONES = [
    "13800138000", "13911112222", "13722223333", "15033334444", "18844445555",
    "17755556666", "19966667777", "13667778888", "15988889999", "18012345678",
]


def _fake(kind: str, idx: int) -> str:
    """按池取第 idx 个假号；池用尽时按规则派生（仍是合法结构）"""
    if kind == "ID":
        if idx < len(FAKE_IDS):
            return FAKE_IDS[idx]
        return f"3101011990{idx:02d}0112{idx:02d}"
    if kind == "PERMIT":
        return f"H0{4400000 + idx:07d}"  # 字母+8位，与真通行证同构
    if idx < len(FAKE_PHONES):
        return FAKE_PHONES[idx]
    return f"138{idx:08d}"


def main() -> None:
    if not RAW.exists():
        RAW.write_text(_TEMPLATE, encoding="utf-8")
        print(f"已创建 {RAW}，先贴消息再重跑")
        return

    real_to_fake: dict[str, str] = {}  # 真值 → 假值，全文件一致
    id_seen = phone_seen = permit_seen = 0
    n_id = n_phone = n_permit = 0
    out_lines = []
    partial_flagged = []  # (文件行号,) 残段嫌疑，交人工

    for lineno, line in enumerate(RAW.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        redacted, mapping = redact_pii(line)

        # 通行证号：privacy 不认的形态，本脚本补位（见模块 docstring）
        counters = {"n": 0}

        def _permit_repl(m, _c=counters, _map=mapping):
            _c["n"] += 1
            token = f"[PERMIT_{_c['n']}]"
            _map[token] = m.group(0)
            return token

        redacted = _PERMIT_RE.sub(_permit_repl, redacted)

        fake_map = {}
        for token, real in mapping.items():
            kind = token[1:].split("_")[0]  # ID / PHONE / PERMIT
            if real not in real_to_fake:
                if kind == "ID":
                    real_to_fake[real] = _fake("ID", id_seen)
                    id_seen += 1
                elif kind == "PHONE":
                    real_to_fake[real] = _fake("PHONE", phone_seen)
                    phone_seen += 1
                else:
                    real_to_fake[real] = _fake("PERMIT", permit_seen)
                    permit_seen += 1
            if kind == "ID":
                n_id += 1
            elif kind == "PHONE":
                n_phone += 1
            else:
                n_permit += 1
            fake_map[token] = real_to_fake[real]
        masked_line = restore_pii(redacted, fake_map)
        out_lines.append(masked_line)

        # 残段标记在替换后的行上做：完整证件号已变假号，剩下的
        # 12-17 位数字串才是真嫌疑（半截号/订单号），行号与编辑器一致
        if _PARTIAL_RE.search(masked_line):
            partial_flagged.append(lineno)

    OUT.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    # 自检：拿真值回扫输出文件，任何残留都是失败——脱敏工具自己也要被验证，
    # 不能"我写了脱敏脚本"就等于"输出是脱敏的"
    text = OUT.read_text(encoding="utf-8")
    leaked = [r for r in real_to_fake if r in text]
    if leaked:
        sys.exit(f"❌ 自检失败：{len(leaked)} 个真号仍残留在 {OUT.name}，该文件不可使用")

    print(f"✅ 已写入 {OUT}：替换证件号 {n_id} 处、手机号 {n_phone} 处、"
          f"通行证号 {n_permit} 处（唯一客户 {id_seen} + 手机号 {phone_seen}"
          f" + 通行证 {permit_seen}）")
    if partial_flagged:
        print(f"⚠️ 第 {partial_flagged} 行含 12-17 位数字残段（半截证件号嫌疑）——")
        print("   脚本不自动换（可能是订单号），请人工核对并改成假残段")
    print(f"⚠️ 人工待办：打开 {OUT.name} 把真实姓名换成张三/李四/王五——")
    print("   同一假号=同一客户，假名也要前后一致；顺带删掉残留的群昵称/地址")


if __name__ == "__main__":
    main()
