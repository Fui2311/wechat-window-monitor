"""Extraction selection and safe rule fallback, preserving original input."""
from datetime import datetime, timezone
from ..analyzer import analyze_message
from .llm import extract_llm
from .settings import ProcessingError, effective_processing_config, settings

def process_message(message, config, *, rules_analyzer=analyze_message, report=None):
    # The monitor keeps its capture configuration; only processing is hot loaded.
    report = {} if report is None else report
    report.clear()
    report["processing_last_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        active = effective_processing_config(config)
        report.update(processing_mode=active.get("processing_mode", "rules"),
                      processing_model=active.get("llm_model", ""),
                      processing_settings_shared=active["processing_settings_shared"])
        if settings(active)["processing_mode"] == "rules":
            report.update(processing_last_method="rules", processing_last_error=None)
            return [dict(item, processing_method="rules") for item in rules_analyzer(message)]
        result = extract_llm(message, active)
        report.update(processing_last_method="llm", processing_last_error=None)
        return result
    except (ProcessingError, ValueError, OSError) as error:
        report.update(processing_last_method="rules_fallback", processing_last_error=(
            str(error) if isinstance(error, ProcessingError) else "处理配置读取或校验失败"))
        return [dict(item, processing_method="rules_fallback",
                     processing_error="LLM 提取失败，已使用本地规则") for item in rules_analyzer(message)]
