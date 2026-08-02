"""Tests for F5: Table QA — precise cell-level answers from structured tables."""
from __future__ import annotations

# ─── F5.2 Table query detection ───


def test_detect_table_query_positive():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    assert enhancer.is_table_query("员工总数是多少") is True
    assert enhancer.is_table_query("第三行的价格是什么") is True
    assert enhancer.is_table_query("销售额最大值") is True
    assert enhancer.is_table_query("各部门人数的平均值") is True
    assert enhancer.is_table_query("哪个部门人数最少") is True


def test_detect_table_query_negative():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    assert enhancer.is_table_query("什么是机器学习") is False
    assert enhancer.is_table_query("如何配置环境变量") is False
    assert enhancer.is_table_query("介绍一下公司历史") is False


# ─── F5.2 Cell extraction ───


def test_extract_single_cell():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    tables = [
        {
            "headers": ["部门", "人数", "预算"],
            "rows": [
                ["工程部", "50", "100万"],
                ["市场部", "30", "60万"],
            ],
        }
    ]
    result = enhancer.extract_answer("市场部有多少人", tables)
    assert result is not None
    assert "30" in result.value


def test_extract_aggregate_sum():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    tables = [
        {
            "headers": ["产品", "销售额"],
            "rows": [
                ["A", "100"],
                ["B", "200"],
                ["C", "300"],
            ],
        }
    ]
    result = enhancer.extract_answer("总销售额是多少", tables)
    assert result is not None
    assert "600" in result.value


def test_extract_aggregate_max():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    tables = [
        {
            "headers": ["城市", "GDP"],
            "rows": [
                ["北京", "4000"],
                ["上海", "4500"],
                ["广州", "2800"],
            ],
        }
    ]
    result = enhancer.extract_answer("GDP最大值是多少", tables)
    assert result is not None
    assert "4500" in result.value


def test_extract_aggregate_count():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    tables = [
        {
            "headers": ["姓名", "成绩"],
            "rows": [["张三", "90"], ["李四", "85"], ["王五", "92"]],
        }
    ]
    result = enhancer.extract_answer("一共有多少条记录", tables)
    assert result is not None
    assert "3" in result.value


# ─── F5.2 Edge cases ───


def test_extract_empty_table():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    result = enhancer.extract_answer("总人数", [])
    assert result is None


def test_extract_no_matching_column():
    from app.rag.table_qa import TableQAEnhancer

    enhancer = TableQAEnhancer()
    tables = [{"headers": ["A", "B"], "rows": [["1", "2"]]}]
    result = enhancer.extract_answer("Z列的值", tables)
    assert result is None


def test_table_answer_dataclass():
    from app.rag.table_qa import TableAnswer

    answer = TableAnswer(
        value="42",
        aggregation="sum",
        column="销售额",
        source_table_index=0,
    )
    assert answer.value == "42"
    assert answer.aggregation == "sum"
