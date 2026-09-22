"""
RAG 检索契约测试：无结果返回空串（而非特定文案）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.rag.retriever import (MIN_RETRIEVE_SCORE, SimpleRetriever,
                                       retrieve_from_knowledge)
from ticketpilot.tools.knowledge_qa import search_knowledge


class TestRetrieverContract:
    """retrieve_from_knowledge 返回值契约"""

    def test_no_result_returns_empty_string(self):
        """完全无关的查询返回空串，不是提示文案"""
        result = retrieve_from_knowledge("zzzqqqxxx 完全不存在的主题 98765")
        assert result == ""

    def test_hit_returns_content(self):
        """命中知识库时返回带来源的拼接内容"""
        # 注意：SimpleRetriever 按空白分词，中文整句会作为单一 token
        # 做子串匹配，因此用术语本身作为查询
        result = retrieve_from_knowledge("强实名")
        assert result  # 非空
        assert "【来源：" in result


class TestSearchKnowledgeTool:
    """search_knowledge 工具基于空串判断"""

    def test_not_found(self):
        result = json.loads(search_knowledge("zzzqqqxxx 完全不存在的主题 98765"))
        assert result["found"] is False

    def test_found(self):
        result = json.loads(search_knowledge("强实名"))
        assert result["found"] is True


class TestBigramScoring:
    """v0.2 覆盖度打分（_calculate_score 不依赖 docs，裸实例即可测）"""

    @staticmethod
    def _score(query: str, content: str) -> float:
        r = SimpleRetriever.__new__(SimpleRetriever)
        return r._calculate_score(query, content)

    def test_exact_term_full_score(self):
        assert self._score("强实名", "强实名是指购票需要绑定身份证") == 1.0

    def test_question_noise_stripped(self):
        """疑问句式词不稀释覆盖度：剥掉「什么是」后整体命中给满分"""
        assert self._score("什么是配票", "配票是指主办方释放余票") == 1.0

    def test_unrelated_below_threshold(self):
        """无关查询低于阈值 → retrieve 会丢弃（硬门控生效点）"""
        assert self._score("完全无关的话题", "强实名是指购票需要身份证") < MIN_RETRIEVE_SCORE

    def test_partial_coverage_ratio(self):
        """查询 bigram {退票,票大,大麦}，文档命中 2 个 → 2/3"""
        score = self._score("退票大麦", "大麦支持退票")
        assert abs(score - 2 / 3) < 1e-9

    def test_digit_runs_do_not_dilute_coverage(self):
        """数字串是身份标识不是主题信号（评测 adv-1 抓出：13 位半截号
        把覆盖度稀释到 0.158，比应拒组最高分还低，阈值救不回来）。
        性质断言：带数字的查询与剥掉数字的查询得分必须相等"""
        content = "门票想退票或换场次需要在开演前联系平台客服办理"
        with_digits = self._score("证件号3101011990010 想退票还要换场次", content)
        without_digits = self._score("证件号 想退票还要换场次", content)
        assert with_digits == without_digits
        assert with_digits >= 0.4  # 主题词（退票/换场次）不被数字淹死


class TestRetrieveLogging:
    """检索分数入日志：评测/排障时能回答「这条为什么拒了/为什么答了」"""

    def test_hit_logs_top_score(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="ticketpilot.rag.retriever"):
            retrieve_from_knowledge("强实名")
        assert "top_score=" in caplog.text

    def test_miss_logs_zero_hits(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="ticketpilot.rag.retriever"):
            retrieve_from_knowledge("zzzqqqxxx 完全不存在的主题 98765")
        assert "hits=0" in caplog.text
