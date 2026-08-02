"""Prompt injection defense — detection, evidence sanitization, PII masking."""
from __future__ import annotations

import re

# ─── Injection detection patterns ───

_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Chinese patterns
    (re.compile(r"忽略以上(所有)?指令", re.IGNORECASE), "忽略以上指令"),
    (re.compile(r"忽略(以上|上面|上述)(的)?(所有)?(规则|提示|设定|约束)", re.IGNORECASE), "忽略以上规则"),
    (re.compile(r"忽略所有指令", re.IGNORECASE), "忽略所有指令"),
    (re.compile(r"你现在是\s*DAN", re.IGNORECASE), "DAN模式"),
    (re.compile(r"(输出|泄露|告诉我|显示)\s*(你的)?\s*(system\s*prompt|系统提示词|提示词)", re.IGNORECASE), "泄露提示词"),
    (re.compile(r"(改变|切换|替换)(你的)?角色", re.IGNORECASE), "改变角色"),
    (re.compile(r"跳过引用", re.IGNORECASE), "跳过引用"),
    (re.compile(r"调用未授权工具", re.IGNORECASE), "调用未授权工具"),
    (re.compile(r"(无视|忽略|跳过)(以上|规则|约束)", re.IGNORECASE), "无视规则"),
    (re.compile(r"(扮演|角色扮演模式|开发者模式|越狱|jailbreak)", re.IGNORECASE), "角色扮演越狱"),
    (re.compile(r"(无视|忽略)(以上|上面)(所有)?(内容|指令|规则)", re.IGNORECASE), "无视以上内容"),
    # English patterns
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)", re.IGNORECASE), "ignore previous instructions"),
    (re.compile(r"you\s+are\s+now\s+DAN", re.IGNORECASE), "DAN mode"),
    (re.compile(r"(output|reveal|show|print|display)\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE), "reveal system prompt"),
    (re.compile(r"disregard\s+(all\s+)?(above|previous|prior)", re.IGNORECASE), "disregard above"),
    (re.compile(r"forget\s+(all\s+)?(your\s+)?(rules|instructions|constraints)", re.IGNORECASE), "forget rules"),
    (re.compile(r"(new|override|overwrite)\s+(system\s+)?(instructions|prompt|rules)", re.IGNORECASE), "override system"),
    (re.compile(r"jailbreak", re.IGNORECASE), "jailbreak"),
]


def detect_injection(text: str) -> tuple[bool, str]:
    """Detect common prompt injection patterns in user input.

    Returns (is_injection, matched_pattern_description).
    """
    if not text:
        return False, ""
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            return True, description
    return False, ""


# ─── Evidence sanitization ───

_HTML_COMMENT_INJECTION = re.compile(
    r"<!--.*?(忽略|指令|ignore|instruction|输出|泄露|system\s*prompt|jailbreak).*?-->",
    re.IGNORECASE | re.DOTALL,
)

_EVIDENCE_INJECTION_LINES = re.compile(
    r"^\s*(忽略以上|忽略所有|ignore\s+(all\s+)?previous|你现在是\s*DAN|"
    r"output\s+system\s+prompt|reveal\s+your\s+prompt|jailbreak|"
    r"disregard\s+above|forget\s+your\s+rules|override\s+system)",
    re.IGNORECASE | re.MULTILINE,
)


def sanitize_evidence(text: str) -> str:
    """Remove obvious injection patterns from retrieved evidence.

    Strips HTML comments containing instruction-like content and lines
    that begin with known injection phrases. Normal content is preserved.
    """
    if not text:
        return text
    # Remove HTML comments containing injection keywords
    cleaned = _HTML_COMMENT_INJECTION.sub("[filtered]", text)
    # Remove lines starting with injection phrases
    cleaned = _EVIDENCE_INJECTION_LINES.sub("[filtered]", cleaned)
    return cleaned


# ─── PII detection and masking ───

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("id_card", re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")),
    ("phone", re.compile(r"1[3-9]\d{9}")),
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
]


def _mask_value(value: str, pii_type: str) -> str:
    """Replace middle characters with asterisks based on PII type."""
    if pii_type == "id_card":
        # Keep first 6 and last 4: 110101****1234
        if len(value) >= 10:
            return value[:6] + "*" * (len(value) - 10) + value[-4:]
        return "*" * len(value)
    if pii_type == "phone":
        # Keep first 3 and last 4: 138****1234
        if len(value) >= 7:
            return value[:3] + "*" * (len(value) - 7) + value[-4:]
        return "*" * len(value)
    if pii_type == "email":
        # Keep first 2 chars and domain: ab***@example.com
        at_idx = value.find("@")
        if at_idx > 2:
            return value[:2] + "***" + value[at_idx:]
        return "***" + value[at_idx:] if at_idx >= 0 else "***"
    return "*" * len(value)


def detect_pii(text: str) -> list[dict]:
    """Detect PII (ID card, phone, email) in text.

    Returns list of {"type", "start", "end", "masked"}.
    Overlapping matches are deduplicated: longer matches take priority.
    """
    if not text:
        return []
    results: list[dict] = []
    for pii_type, pattern in _PII_PATTERNS:
        for match in pattern.finditer(text):
            results.append({
                "type": pii_type,
                "start": match.start(),
                "end": match.end(),
                "masked": _mask_value(match.group(), pii_type),
            })
    # Sort by position, then by length descending (longer match wins)
    results.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    # Remove overlapping matches (keep the longer/earlier one)
    filtered: list[dict] = []
    for det in results:
        if filtered and det["start"] < filtered[-1]["end"]:
            continue  # overlaps with previous, skip
        filtered.append(det)
    return filtered


def mask_pii(text: str) -> str:
    """Replace detected PII in text with masked versions."""
    if not text:
        return text
    detections = detect_pii(text)
    if not detections:
        return text
    # Apply replacements from end to start to preserve indices
    result = text
    for det in reversed(detections):
        result = result[:det["start"]] + det["masked"] + result[det["end"]:]
    return result
