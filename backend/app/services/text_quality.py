"""Quality gates shared by ingestion and preview generation."""

from __future__ import annotations

from qa_core.indexing.encoding import quality_report


def validate_extracted_text(text: str, *, minimum_characters: int = 8) -> dict[str, object]:
    report = quality_report(text)
    report["too_short"] = len(text.strip()) < minimum_characters
    report["healthy"] = bool(report["healthy"]) and not report["too_short"]
    if not report["healthy"]:
        reasons = []
        if report["too_short"]:
            reasons.append("提取文本为空或过短")
        if float(report["replacement_ratio"]) >= 0.01:
            reasons.append("包含大量Unicode替换字符，疑似编码错误")
        if float(report["control_ratio"]) >= 0.02:
            reasons.append("包含异常控制字符")
        if float(report["suspicious_characters"]) / max(int(report["characters"]), 1) >= 0.03:
            reasons.append("疑似乱码字符比例过高")
        report["reasons"] = reasons
    return report
