"""Deterministic first-pass diagnosis for the vertical business advisor."""

import re
from typing import TypedDict


class Diagnosis(TypedDict):
    task_type: str
    needs_clarification: bool
    missing_information: list[str]
    recommended_next_step: str


_PLANNING_TERMS = ("方案", "规划", "策划", "增长", "商业")
_ANALYSIS_TERMS = ("分析", "诊断", "数据", "问题", "复盘")
_CONTEXT_TERMS = ("客户", "用户", "产品", "渠道", "团队", "预算", "目标", "时间")


def diagnose_request(request: str) -> Diagnosis:
    """Classify a request and identify only information that affects the decision."""
    normalized = re.sub(r"\s+", "", request or "")
    if any(term in normalized for term in _ANALYSIS_TERMS):
        task_type = "business_analysis"
    elif any(term in normalized for term in _PLANNING_TERMS):
        task_type = "business_planning"
    else:
        task_type = "general_business_advice"

    missing_information: list[str] = []
    if len(normalized) < 12:
        missing_information.append("目标")
    if not any(term in normalized for term in ("客户", "用户", "业务", "产品")):
        missing_information.append("客户或业务场景")
    if not any(term in normalized for term in ("指标", "结果", "提升", "降低", "完成")):
        missing_information.append("成功标准")

    needs_clarification = len(missing_information) >= 2
    if needs_clarification:
        next_step = "先补充目标、业务场景和成功标准，再制定方案。"
    else:
        next_step = "先确认关键事实和约束，再给出方案、风险与行动计划。"

    return {
        "task_type": task_type,
        "needs_clarification": needs_clarification,
        "missing_information": missing_information,
        "recommended_next_step": next_step,
    }


def format_diagnosis_context(request: str) -> str:
    """Format diagnosis as internal guidance for the model, not as hidden reasoning."""
    diagnosis = diagnose_request(request)
    missing = "、".join(diagnosis["missing_information"]) or "暂无关键缺口"
    return (
        "参谋任务诊断：\n"
        f"- 任务类型：{diagnosis['task_type']}\n"
        f"- 是否需要澄清：{'是' if diagnosis['needs_clarification'] else '否'}\n"
        f"- 关键信息缺口：{missing}\n"
        f"- 推荐下一步：{diagnosis['recommended_next_step']}"
    )
