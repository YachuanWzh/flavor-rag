"""Interview generation, material hashing, and scoring domain service.

The public functions in this module are deterministic so the interview flow
remains usable in local/mock mode. When a configured LLM is available the API
layer may use the async refinement helpers; their output is validated against
the same contracts before persistence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from typing import Any

from app.config.settings import settings
from app.llm.client import collect_agentic_generation


DEFAULT_SCORE_DIMENSIONS = [
    {"key": "knowledgeAccuracy", "label": "知识准确度", "weight": 0.25},
    {"key": "technicalDepth", "label": "原理与技术深度", "weight": 0.20},
    {"key": "practicalApplication", "label": "实践与落地能力", "weight": 0.20},
    {"key": "problemSolving", "label": "分析解决能力", "weight": 0.15},
    {"key": "communication", "label": "表达结构性", "weight": 0.10},
    {"key": "roleFit", "label": "岗位匹配度", "weight": 0.10},
]

ROLE_FIT_WEIGHTS = {
    "skillCoverage": 0.35,
    "projectEvidence": 0.30,
    "responsibilityMatch": 0.20,
    "levelMatch": 0.15,
}

DIFFICULTIES = {
    "mid": "中级",
    "senior": "高级",
    "expert": "专家",
}

MIN_QUESTIONS = 10
MAX_QUESTIONS = 20
DEFAULT_QUESTIONS = 12


def material_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def allocate_question_quota(
    count: int,
    *,
    has_resume: bool,
    has_jd: bool,
) -> dict[str, int]:
    """Allocate a stable question mix while preserving the 7/3 invariant."""
    count = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(count)))
    knowledge = min(7, count)
    profile = min(3, count - knowledge) if has_resume or has_jd else 0
    scenario = count - knowledge - profile
    return {
        "knowledge": knowledge,
        "profile": profile,
        "scenario": scenario,
    }


def _source_excerpt(source: dict) -> str:
    content = re.sub(r"\s+", " ", str(source.get("content") or "")).strip()
    if not content:
        return "该知识点"
    sentence = re.split(r"(?<=[。！？.!?])\s*", content)[0]
    return sentence[:96].rstrip("，,。.;； ")


def _copy_source(source: dict | None) -> dict | None:
    if not source:
        return None
    keys = (
        "documentId",
        "chunkId",
        "docName",
        "chunkIndex",
        "content",
        "score",
        "fusionScore",
        "rerankScore",
        "channelScores",
        "matchedChannels",
        "blockType",
        "pageStart",
        "pageEnd",
        "bboxes",
        "assets",
        "neighborOf",
        "fileType",
        "kbId",
        "kbName",
    )
    return {key: source.get(key) for key in keys if key in source}


def extract_resume_signals(resume_text: str, *, limit: int = 6) -> list[str]:
    """Extract concrete, ordered resume anchors instead of using its first line."""
    if not resume_text.strip():
        return []
    raw_segments = re.split(r"[\r\n]+|(?<=[。！？!?；;])\s*", resume_text)
    signals: list[str] = []
    seen: set[str] = set()
    evidence_markers = (
        "项目",
        "负责",
        "设计",
        "实现",
        "搭建",
        "开发",
        "优化",
        "主导",
        "架构",
        "上线",
        "提升",
        "降低",
        "减少",
        "built",
        "designed",
        "implemented",
        "developed",
        "optimized",
        "led",
        "improved",
        "reduced",
    )
    technology_pattern = re.compile(
        r"\b(?:agent|rag|llm|python|java|go|rust|fastapi|spring|react|"
        r"postgresql|mysql|redis|kafka|docker|kubernetes|k8s)\b",
        re.I,
    )
    for raw in raw_segments:
        segment = re.sub(r"\s+", " ", raw).strip(" •·●○-\t，,。.;；")
        lowered = segment.lower()
        if not 6 <= len(segment) <= 220:
            continue
        if re.search(r"[\w.+-]+@[\w.-]+|\b1[3-9]\d{9}\b", segment):
            continue
        has_evidence = (
            any(marker in lowered for marker in evidence_markers)
            or bool(re.search(r"\d+(?:\.\d+)?\s*%|\d+\s*(?:倍|ms|秒|万|亿)", segment, re.I))
            or bool(technology_pattern.search(segment))
        )
        if not has_evidence:
            continue
        key = segment.casefold()
        if key in seen:
            continue
        seen.add(key)
        signals.append(segment[:180])
        if len(signals) >= limit:
            break
    return signals


def _question_resume_anchor(signals: list[str], index: int) -> str:
    if not signals:
        return ""
    primary = signals[0]
    detail = signals[index % len(signals)]
    if detail == primary:
        return primary
    return f"{primary}；{detail}"


def _question_prefix(*, focus_text: str, resume_anchor: str) -> str:
    parts: list[str] = []
    focus = re.sub(r"\s+", " ", focus_text).strip()[:120]
    if focus:
        parts.append(f"本场重点“{focus}”")
    if resume_anchor:
        parts.append(f"简历锚点“{resume_anchor}”")
    return "，".join(parts) + "。 " if parts else ""


def build_fallback_questions(
    *,
    sources: list[dict],
    count: int = DEFAULT_QUESTIONS,
    resume_text: str = "",
    jd_text: str = "",
    focus_text: str = "",
    difficulty: str = "senior",
) -> list[dict]:
    """Build source-grounded questions when structured LLM output is absent."""
    count = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(count)))
    quota = allocate_question_quota(
        count,
        has_resume=bool(resume_text.strip()),
        has_jd=bool(jd_text.strip()),
    )
    level = DIFFICULTIES.get(difficulty, DIFFICULTIES["senior"])
    safe_sources = list(sources)
    resume_signals = extract_resume_signals(resume_text)
    profile_hint = _profile_hint(resume_text, jd_text)
    questions: list[dict] = []

    knowledge_templates = [
        "请解释“{excerpt}”背后的核心原理，并说明它成立的前提。",
        "围绕“{excerpt}”，请比较两种可行方案，并给出你的取舍依据。",
        "如果“{excerpt}”在生产环境失效，你会如何定位根因？",
        "请把“{excerpt}”设计成可扩展方案，重点说明边界与瓶颈。",
        "关于“{excerpt}”，最容易被忽略的异常路径是什么？如何验证？",
        "请从数据一致性、性能和可运维性三个角度评审“{excerpt}”。",
        "如何用指标和实验验证你对“{excerpt}”的判断，而不是依赖经验？",
    ]
    for index in range(quota["knowledge"]):
        source = safe_sources[index % len(safe_sources)] if safe_sources else None
        excerpt = _source_excerpt(source or {})
        resume_anchor = _question_resume_anchor(resume_signals, index)
        questions.append(
            {
                "category": "knowledge",
                "question": f"[{level}] "
                + _question_prefix(
                    focus_text=focus_text,
                    resume_anchor=resume_anchor,
                )
                + knowledge_templates[index % len(knowledge_templates)].format(
                    excerpt=excerpt
                ),
                "followUp": "如果规模扩大 10 倍，你的结论会发生什么变化？",
                "rubric": [
                    "概念与事实准确",
                    "说明关键机制和适用边界",
                    "能讨论替代方案与工程取舍",
                    "给出可验证的指标或案例",
                ],
                "source": _copy_source(source),
                "agentGenerated": source is None,
                "resumeAnchor": resume_anchor,
                "userFocus": focus_text.strip(),
            }
        )

    profile_templates = [
        "结合你的经历“{hint}”，讲一次与目标岗位最相关的技术决策。你负责的边界、量化结果和复盘是什么？",
        "岗位需要把知识转化为稳定交付。请用“{hint}”中的项目说明你如何处理一次高风险问题。",
        "请选取“{hint}”里最能代表你能力的一项经历，并说明如果重做一次，你会改变什么。",
    ]
    for index in range(quota["profile"]):
        source = (
            safe_sources[(quota["knowledge"] + index) % len(safe_sources)]
            if safe_sources
            else None
        )
        resume_anchor = _question_resume_anchor(
            resume_signals,
            quota["knowledge"] + index,
        )
        questions.append(
            {
                "category": "profile",
                "question": f"[{level}] "
                + _question_prefix(
                    focus_text=focus_text,
                    resume_anchor=resume_anchor,
                )
                + profile_templates[index % len(profile_templates)].format(
                    hint=resume_anchor or profile_hint
                ),
                "followUp": "你如何证明结果主要来自你的贡献，而不是团队或环境红利？",
                "rubric": [
                    "经历与简历/JD一致",
                    "个人职责边界清晰",
                    "结果有量化证据",
                    "能把经历映射到知识库原理",
                ],
                "source": _copy_source(source),
                "agentGenerated": source is None,
                "resumeAnchor": resume_anchor,
                "userFocus": focus_text.strip(),
            }
        )

    scenario_templates = [
        "线上核心链路出现间歇性超时，但监控没有明显异常。请主持一次从止损到根因定位的完整排障。",
        "业务量预计三个月增长 10 倍。请设计演进方案，并说明容量、成本、回滚和故障演练计划。",
        "一个跨团队项目在上线前发现关键假设不成立。你会如何重新决策并推动各方达成一致？",
        "请设计一次能暴露系统真实瓶颈的压测，并说明如何避免得到误导性结论。",
        "如果必须在交付速度、可靠性和长期架构之间取舍，你会如何建立可审计的决策依据？",
    ]
    for index in range(quota["scenario"]):
        source = (
            safe_sources[
                (quota["knowledge"] + quota["profile"] + index)
                % len(safe_sources)
            ]
            if safe_sources
            else None
        )
        resume_anchor = _question_resume_anchor(
            resume_signals,
            quota["knowledge"] + quota["profile"] + index,
        )
        questions.append(
            {
                "category": "scenario",
                "question": f"[{level}] "
                + _question_prefix(
                    focus_text=focus_text,
                    resume_anchor=resume_anchor,
                )
                + scenario_templates[index % len(scenario_templates)],
                "followUp": "现在限制你只能使用一半资源，你会保留哪些动作，放弃哪些动作？",
                "rubric": [
                    "先澄清目标、约束和风险",
                    "方案有优先级和决策依据",
                    "覆盖异常、回滚和可观测性",
                    "能结合知识库证据解释取舍",
                ],
                "source": _copy_source(source),
                "agentGenerated": source is None,
                "resumeAnchor": resume_anchor,
                "userFocus": focus_text.strip(),
            }
        )
    return questions


def _profile_hint(resume_text: str, jd_text: str) -> str:
    text = re.sub(r"\s+", " ", f"{resume_text} {jd_text}").strip()
    if not text:
        return "你最具代表性的项目"
    return text[:72].rstrip("，,。.;； ")


def _json_from_text(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1).strip()
    start_candidates = [i for i in (stripped.find("["), stripped.find("{")) if i >= 0]
    if not start_candidates:
        raise ValueError("LLM output does not contain JSON")
    start = min(start_candidates)
    end = max(stripped.rfind("]"), stripped.rfind("}"))
    if end < start:
        raise ValueError("LLM output contains incomplete JSON")
    return json.loads(stripped[start : end + 1])


def _llm_is_configured() -> bool:
    return bool(
        settings.agentic_model_api_key
        or settings.reasoning_api_key
        or settings.bailian_api_key
        or settings.siliconflow_api_key
        or settings.hyde_api_key
    )


async def refine_questions_with_agent(
    scaffold: list[dict],
    *,
    resume_text: str,
    jd_text: str,
    difficulty: str,
    focus_text: str = "",
) -> list[dict]:
    """Refine wording with an LLM without letting it alter quotas/provenance."""
    if not _llm_is_configured():
        return scaffold
    payload = [
        {
            "index": index,
            "category": item["category"],
            "question": item["question"],
            "followUp": item["followUp"],
            "rubric": item["rubric"],
            "evidence": (item.get("source") or {}).get("content", "")[:500],
            "resumeAnchor": item.get("resumeAnchor", ""),
            "userFocus": item.get("userFocus", ""),
        }
        for index, item in enumerate(scaffold)
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是大厂技术面试官。只返回 JSON 数组，不要 Markdown。"
                "保持 index/category/题目数量不变，问题要具体、可追问、有工程取舍，"
                "不得引入 evidence、resumeAnchor、JD 中不存在的技术或事实。"
                "有 resumeAnchor 时至少 70% 题目必须直接点名该项目、动作或量化结果，"
                "并继续追问个人边界、技术原理、失败路径和验证数据。"
                "userFocus 是最高优先级，不得改成无关方向。"
                "每项字段为 index, question, followUp, rubric。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "difficulty": difficulty,
                    "resume": resume_text[:5000],
                    "jd": jd_text[:5000],
                    "userFocus": focus_text[:1000],
                    "questions": payload,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        generated = await collect_agentic_generation(messages, max_tokens=5000)
        parsed = _json_from_text(
            "".join(token for token in generated.tokens if not token.startswith("__THINK__"))
        )
        if not isinstance(parsed, list) or len(parsed) != len(scaffold):
            return scaffold
        by_index = {
            int(item["index"]): item
            for item in parsed
            if isinstance(item, dict) and str(item.get("index", "")).isdigit()
        }
        if len(by_index) != len(scaffold):
            return scaffold
        refined: list[dict] = []
        for index, original in enumerate(scaffold):
            candidate = by_index[index]
            question = str(candidate.get("question") or "").strip()
            follow_up = str(candidate.get("followUp") or "").strip()
            rubric = candidate.get("rubric")
            if not question or not follow_up or not isinstance(rubric, list):
                return scaffold
            resume_anchor = str(original.get("resumeAnchor") or "").strip()
            if resume_anchor and not any(
                token and token.casefold() in question.casefold()
                for token in re.split(r"[；;，,\s/]+", resume_anchor)
                if len(token) >= 4
            ):
                question = f"结合你简历中的“{resume_anchor}”，{question}"
            normalized_focus = re.sub(r"\s+", " ", focus_text).strip()[:120]
            if normalized_focus and normalized_focus.casefold() not in question.casefold():
                question = f"围绕本场重点“{normalized_focus}”，{question}"
            refined.append(
                {
                    **original,
                    "question": question[:1500],
                    "followUp": follow_up[:800],
                    "rubric": [str(item)[:300] for item in rubric[:6]],
                }
            )
        return refined
    except Exception:
        return scaffold


def _answer_quality(question: dict, answer: str, skipped: bool) -> dict:
    if skipped or not answer.strip():
        return {
            "score": 0.0,
            "analysis": "该题未作答，无法验证知识掌握与岗位能力。",
            "strengths": [],
            "improvements": ["先给出结论，再按原理、证据、取舍和验证方式展开。"],
            "referencePoints": list(question.get("rubric") or []),
        }
    normalized = re.sub(r"\s+", " ", answer).strip()
    length_score = min(3.5, math.log2(max(2, len(normalized))) * 0.55)
    structure_markers = sum(
        marker in normalized.lower()
        for marker in (
            "首先",
            "其次",
            "最后",
            "because",
            "trade-off",
            "指标",
            "监控",
            "回滚",
            "验证",
            "风险",
        )
    )
    structure_score = min(2.0, structure_markers * 0.35)
    evidence_text = " ".join(
        [
            str((question.get("source") or {}).get("content") or ""),
            " ".join(question.get("rubric") or []),
        ]
    )
    evidence_terms = set(_meaningful_terms(evidence_text))
    answer_terms = set(_meaningful_terms(normalized))
    overlap = len(evidence_terms & answer_terms) / max(1, min(12, len(evidence_terms)))
    evidence_score = min(3.0, overlap * 6.0)
    decision_score = 1.5 if any(
        marker in normalized for marker in ("取舍", "权衡", "成本", "边界", "故障")
    ) else 0.5
    score = round(min(10.0, 1.0 + length_score + structure_score + evidence_score + decision_score), 1)
    strengths = []
    if length_score >= 2.5:
        strengths.append("回答包含了足够的展开信息。")
    if structure_score >= 0.7:
        strengths.append("表达具备一定结构，并覆盖了验证或风险。")
    if evidence_score >= 1.0:
        strengths.append("回答与题目证据和评分点存在有效对应。")
    improvements = []
    if structure_score < 0.7:
        improvements.append("先明确结论，再分层说明原理、方案、风险和验证指标。")
    if evidence_score < 1.0:
        improvements.append("补充知识库中的关键机制或边界条件，减少泛化表述。")
    if decision_score < 1.0:
        improvements.append("说明备选方案、取舍依据以及失败时的回滚策略。")
    return {
        "score": score,
        "analysis": (
            f"回答完整度与证据关联度综合为 {score:.1f}/10。"
            "评分关注事实准确、技术深度、工程取舍和表达结构。"
        ),
        "strengths": strengths,
        "improvements": improvements,
        "referencePoints": list(question.get("rubric") or []),
    }


def _meaningful_terms(text: str) -> list[str]:
    latin = re.findall(r"[A-Za-z][A-Za-z0-9_+.#/-]{2,}", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    stop = {"一个", "以及", "如何", "什么", "进行", "可以", "需要", "说明"}
    return [item for item in [*latin, *chinese] if item not in stop][:100]


def _algorithm_answer_quality(question: dict, answer: str, skipped: bool) -> dict:
    """Conservative static fallback; configured deployments use the LLM grader."""
    rubric = list(question.get("rubric") or [])
    if skipped or not answer.strip():
        return {
            "score": 0.0,
            "analysis": "该算法题未作答，无法评估代码正确性与复杂度。",
            "strengths": [],
            "improvements": ["先完成可运行解法，再补充时间、空间复杂度和边界条件。"],
            "referencePoints": rubric,
        }
    code = answer.strip()
    metadata = question.get("metadata") or {}
    function_name = str(metadata.get("functionName") or "")
    has_function = bool(
        re.search(
            rf"\b(?:function\s+)?{re.escape(function_name)}\s*\(",
            code,
        )
    ) if function_name else bool(re.search(r"\bfunction\b|=>|\bdef\b", code))
    has_control_flow = bool(
        re.search(r"\b(?:for|while|if|reduce|map|filter|return)\b", code)
    )
    has_return = bool(re.search(r"\breturn\b", code))
    balance_ok = all(
        code.count(opening) == code.count(closing)
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"))
    )
    score = 2.0
    score += 2.0 if has_function else 0.5
    score += 1.5 if has_control_flow else 0.0
    score += 1.5 if has_return else 0.0
    score += 1.0 if balance_ok else 0.0
    rubric_terms = set(_meaningful_terms(" ".join(rubric)))
    answer_terms = set(_meaningful_terms(code))
    if rubric_terms:
        score += min(1.5, len(rubric_terms & answer_terms) * 0.3)
    score = round(min(8.5, score), 1)
    strengths = []
    if has_function and has_return:
        strengths.append("提交了结构完整、可供判题器调用的函数实现。")
    if has_control_flow:
        strengths.append("代码中包含了明确的求解流程。")
    improvements = []
    if not balance_ok:
        improvements.append("检查括号配对，当前代码可能存在语法错误。")
    if not has_function:
        improvements.append(f"按题目约定实现 {function_name or '指定'} 函数。")
    improvements.append("结合示例与极端输入验证正确性，并明确时间、空间复杂度。")
    return {
        "score": score,
        "analysis": (
            f"本地兜底评分为 {score:.1f}/10；该结果基于代码结构、接口完整性和评分点覆盖度。"
            "配置 AI 模型后会进一步检查算法正确性、复杂度与边界条件。"
        ),
        "strengths": strengths,
        "improvements": improvements,
        "referencePoints": rubric,
    }


def score_interview_answers(
    questions: list[dict],
    answers: dict[str, dict],
    *,
    has_resume: bool,
    has_jd: bool,
) -> dict:
    """Deterministic scoring fallback with per-question review details."""
    reviews: list[dict] = []
    category_scores: dict[str, list[float]] = defaultdict(list)
    for question in questions:
        answer_record = answers.get(str(question["id"]), {})
        quality = (
            _algorithm_answer_quality
            if question.get("category") == "algorithm"
            else _answer_quality
        )
        review = quality(
            question,
            str(answer_record.get("answer") or ""),
            bool(answer_record.get("skipped")),
        )
        reviews.append({"questionId": question["id"], **review})
        category_scores[str(question.get("category") or "knowledge")].append(
            review["score"]
        )

    def average(category: str, fallback: float) -> float:
        values = category_scores.get(category) or []
        return sum(values) / len(values) if values else fallback

    all_scores = [item["score"] for item in reviews]
    base = sum(all_scores) / max(1, len(all_scores))
    knowledge = average("knowledge", base)
    scenario = average("scenario", base)
    profile = average("profile", base)
    algorithm = average("algorithm", base)
    has_algorithm = bool(category_scores.get("algorithm"))
    answered_text = " ".join(
        str(value.get("answer") or "") for value in answers.values()
    )
    communication = min(
        10.0,
        base * 0.75
        + min(2.5, len(_meaningful_terms(answered_text)) / max(1, len(questions)) * 0.45),
    )
    role_fit = (
        profile * 0.6 + knowledge * 0.2 + scenario * 0.2
        if has_resume or has_jd
        else knowledge * 0.45 + scenario * 0.55
    )
    dimensions = {
        "knowledgeAccuracy": round(knowledge, 1),
        "technicalDepth": round(
            knowledge * 0.45 + scenario * 0.35 + algorithm * 0.20
            if has_algorithm
            else knowledge * 0.55 + scenario * 0.45,
            1,
        ),
        "practicalApplication": round(scenario * 0.65 + profile * 0.35, 1),
        "problemSolving": round(
            algorithm * 0.55 + scenario * 0.30 + knowledge * 0.15
            if has_algorithm
            else scenario * 0.75 + knowledge * 0.25,
            1,
        ),
        "communication": round(communication, 1),
        "roleFit": round(role_fit, 1),
    }
    overall = round(
        sum(
            dimensions[dimension["key"]] * dimension["weight"]
            for dimension in DEFAULT_SCORE_DIMENSIONS
        ),
        1,
    )
    role_fit_breakdown = {
        "skillCoverage": round(knowledge, 1),
        "projectEvidence": round(profile if has_resume else scenario, 1),
        "responsibilityMatch": round(role_fit, 1),
        "levelMatch": round(knowledge * 0.4 + scenario * 0.6, 1),
    }
    return {
        "overallScore": overall,
        "dimensionScores": dimensions,
        "roleFitBreakdown": role_fit_breakdown,
        "summary": (
            f"本次面试综合得分 {overall:.1f}/10。"
            "建议优先复盘低分题目的知识边界、工程取舍和量化验证方式。"
        ),
        "reviews": reviews,
    }


async def score_answers_with_agent(
    questions: list[dict],
    answers: dict[str, dict],
    *,
    has_resume: bool,
    has_jd: bool,
    target_role: str,
    difficulty: str,
) -> dict:
    """Use the configured agent for analysis, with a validated local fallback."""
    fallback = score_interview_answers(
        questions,
        answers,
        has_resume=has_resume,
        has_jd=has_jd,
    )
    if not _llm_is_configured():
        return fallback

    compact_questions = []
    for question in questions:
        answer = answers.get(str(question["id"]), {})
        compact_questions.append(
            {
                "questionId": str(question["id"]),
                "category": question.get("category"),
                "question": str(question.get("question") or "")[:1200],
                "answer": str(answer.get("answer") or "")[
                    :8000 if question.get("category") == "algorithm" else 1200
                ],
                "answerLanguage": answer.get("answerLanguage"),
                "skipped": bool(answer.get("skipped")),
                "rubric": list(question.get("rubric") or [])[:6],
                "algorithmMetadata": (
                    question.get("metadata")
                    if question.get("category") == "algorithm"
                    else None
                ),
                "evidence": str(
                    (question.get("source") or {}).get("content") or ""
                )[:400],
            }
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是严格但公正的大厂技术面试官。只返回 JSON 对象，不要 Markdown。"
                "所有分数范围 0-10，允许一位小数。不得把表达长度等同于技术正确，"
                "非算法题不得使用 evidence 之外的知识作为题目标准答案事实。"
                "category=algorithm 时必须逐行审查代码：先判断能否通过 algorithmMetadata.testCases，"
                "answerLanguage 表示候选人使用的编程语言，必须按该语言的语法和惯用法审查。"
                "再检查题目要求、时间复杂度、空间复杂度和边界条件；语法错误或核心结果错误不得高于 4 分，"
                "只通过部分示例不得高于 6 分，正确但复杂度未达要求不得高于 8 分。"
                "返回字段：dimensionScores（knowledgeAccuracy, technicalDepth, "
                "practicalApplication, problemSolving, communication, roleFit）、"
                "roleFitBreakdown（skillCoverage, projectEvidence, responsibilityMatch, levelMatch）、"
                "summary、reviews。reviews 必须逐题覆盖，字段 questionId, score, analysis, "
                "strengths, improvements, referencePoints。跳过题必须为 0 分。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "targetRole": target_role,
                    "difficulty": difficulty,
                    "hasResume": has_resume,
                    "hasJd": has_jd,
                    "scoreDimensions": DEFAULT_SCORE_DIMENSIONS,
                    "questions": compact_questions,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        generated = await collect_agentic_generation(messages, max_tokens=7000)
        parsed = _json_from_text(
            "".join(
                token
                for token in generated.tokens
                if not token.startswith("__THINK__")
            )
        )
        if not isinstance(parsed, dict):
            return fallback
        dimension_scores = _validated_score_map(
            parsed.get("dimensionScores"),
            [item["key"] for item in DEFAULT_SCORE_DIMENSIONS],
        )
        role_fit_breakdown = _validated_score_map(
            parsed.get("roleFitBreakdown"),
            list(ROLE_FIT_WEIGHTS),
        )
        parsed_reviews = parsed.get("reviews")
        if not isinstance(parsed_reviews, list):
            return fallback
        review_by_id = {
            str(item.get("questionId")): item
            for item in parsed_reviews
            if isinstance(item, dict) and item.get("questionId") is not None
        }
        reviews: list[dict] = []
        for fallback_review in fallback["reviews"]:
            question_id = str(fallback_review["questionId"])
            candidate = review_by_id.get(question_id)
            if not candidate:
                return fallback
            answer = answers.get(question_id, {})
            score = _score_value(candidate.get("score"))
            if bool(answer.get("skipped")):
                score = 0.0
            analysis = str(candidate.get("analysis") or "").strip()
            strengths = candidate.get("strengths")
            improvements = candidate.get("improvements")
            reference_points = candidate.get("referencePoints")
            if (
                not analysis
                or not isinstance(strengths, list)
                or not isinstance(improvements, list)
                or not isinstance(reference_points, list)
            ):
                return fallback
            reviews.append(
                {
                    "questionId": fallback_review["questionId"],
                    "score": score,
                    "analysis": analysis[:1200],
                    "strengths": [str(item)[:300] for item in strengths[:6]],
                    "improvements": [
                        str(item)[:400] for item in improvements[:6]
                    ],
                    "referencePoints": [
                        str(item)[:400] for item in reference_points[:8]
                    ],
                }
            )
        overall = round(
            sum(
                dimension_scores[dimension["key"]] * dimension["weight"]
                for dimension in DEFAULT_SCORE_DIMENSIONS
            ),
            1,
        )
        summary = str(parsed.get("summary") or "").strip()
        if not summary:
            return fallback
        return {
            "overallScore": overall,
            "dimensionScores": dimension_scores,
            "roleFitBreakdown": role_fit_breakdown,
            "summary": summary[:1600],
            "reviews": reviews,
        }
    except Exception:
        return fallback


def _score_value(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("score must be numeric")
    return round(max(0.0, min(10.0, float(value))), 1)


def _validated_score_map(value: Any, keys: list[str]) -> dict[str, float]:
    if not isinstance(value, dict) or any(key not in value for key in keys):
        raise ValueError("score map is incomplete")
    return {key: _score_value(value[key]) for key in keys}


def aggregate_interview_profile(
    previous_scores: dict[str, float] | None,
    current_scores: dict[str, float],
    *,
    interview_count: int,
) -> dict:
    """Apply an EMA and return trend metadata for the long-term profile."""
    previous_scores = previous_scores or {}
    first_interview = interview_count <= 0 or not previous_scores
    scores: dict[str, float] = {}
    for dimension in DEFAULT_SCORE_DIMENSIONS:
        key = dimension["key"]
        current = max(0.0, min(10.0, float(current_scores.get(key, 0))))
        previous = max(0.0, min(10.0, float(previous_scores.get(key, current))))
        scores[key] = round(current if first_interview else previous * 0.65 + current * 0.35, 1)
    overall = round(
        sum(
            scores[dimension["key"]] * dimension["weight"]
            for dimension in DEFAULT_SCORE_DIMENSIONS
        ),
        1,
    )
    previous_overall = round(
        sum(
            float(previous_scores.get(dimension["key"], scores[dimension["key"]]))
            * dimension["weight"]
            for dimension in DEFAULT_SCORE_DIMENSIONS
        ),
        1,
    )
    delta = 0.0 if first_interview else round(overall - previous_overall, 1)
    trend = "up" if delta >= 0.3 else "down" if delta <= -0.3 else "stable"
    return {
        "scores": scores,
        "overallScore": overall,
        "previousOverallScore": previous_overall,
        "delta": delta,
        "trend": trend,
    }
