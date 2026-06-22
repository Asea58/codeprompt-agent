"""Orchestrates the full pipeline:
    generate (LLM) -> execute code -> self-check -> retry on failure -> render output.

This is the骨架 (skeleton). Behaviour is driven by config/templates, so day-to-day
extension (new subjects/reasons/formats) shouldn't require editing this file.
"""
import datetime
import os
import re

from . import checker, config_loader, executor, generator


class PipelineResult:
    def __init__(self, success, parsed=None, exec_result=None, findings=None,
                 attempts=0, error=None):
        self.success = success
        self.parsed = parsed or {}
        self.exec_result = exec_result
        self.findings = findings or []
        self.attempts = attempts
        self.error = error


def _slugify(text, maxlen=40):
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^\w一-鿿-]", "", text)
    return text[:maxlen] or "item"


def run(subject, reason_key, max_retries=2, timeout=60, mock=False, verbose=True,
        judge_mode="warn"):
    """Generate one verified problem. Returns PipelineResult.

    judge_mode controls the LLM semantic judge:
      'warn'  (default) execution + heuristics gate; judge concerns are advisory
      'block'           judge can hard-fail a candidate and force a retry
      'off'             skip the judge entirely (fastest / cheapest)
    """

    def log(msg):
        if verbose:
            print(msg)

    # Validate inputs early with clear errors.
    config_loader.load_subject(subject)
    config_loader.load_reason(reason_key)

    extra = None
    last_findings = []
    parsed = None
    exec_result = None

    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        log(f"\n=== 第 {attempt} 次生成 (subject={subject}, reason={reason_key}, mock={mock}) ===")

        # 1. generate
        try:
            parsed = generator.generate(subject, reason_key, extra_requirements=extra, mock=mock)
        except Exception as e:  # noqa: BLE001
            log(f"  生成失败：{e}")
            extra = f"上一次生成失败：{e}。请严格按四个 XML 标签输出。"
            last_findings = [checker.Finding("fail", f"生成阶段失败：{e}")]
            continue
        log("  ✓ 已生成题面/代码")

        # 2. execute
        exec_result = executor.run_code(parsed["code"], timeout=timeout)
        if exec_result.ok:
            log(f"  ✓ 代码执行成功，ANSWER = {exec_result.answer}")
        else:
            log(f"  ✗ 执行问题：{exec_result.error}")

        # 3. self-check
        passed, findings = checker.check(
            parsed, exec_result, reason_key, mock=mock, judge_mode=judge_mode
        )
        last_findings = findings
        for f in findings:
            log(f"    {f}")

        if passed:
            log(f"  ✓ 自检通过（第 {attempt} 次）")
            return PipelineResult(
                success=True, parsed=parsed, exec_result=exec_result,
                findings=findings, attempts=attempt,
            )

        # 4. build retry feedback from the fail findings
        fail_msgs = [f.message for f in findings if f.severity == "fail"]
        extra = (
            "上一次生成未通过校验，请修正以下问题后重新出题：\n"
            + "\n".join(f"- {m}" for m in fail_msgs)
            + "\n务必确保代码可运行并打印 `ANSWER: <数值>`。"
        )
        log("  ✗ 自检未通过，准备重试")

    return PipelineResult(
        success=False, parsed=parsed, exec_result=exec_result,
        findings=last_findings, attempts=max_retries + 1,
        error="达到最大重试次数仍未通过校验",
    )


def render_markdown(result, subject, reason_key):
    """Fill output_format.md with the result."""
    reason = config_loader.load_reason(reason_key)
    subj = config_loader.load_subject(subject)
    template = config_loader.load_template("output_format")

    parsed = result.parsed
    exec_result = result.exec_result

    answer_val = exec_result.answer if (exec_result and exec_result.answer is not None) else "N/A"
    run_output = exec_result.stdout.strip() if exec_result else ""
    check_status = "通过" if result.success else "未通过"

    return template.format(
        subject=subj["subject"],
        reason_label=reason["label"],
        check_status=check_status,
        query=parsed.get("query", "(无)"),
        approach=parsed.get("approach", "(无)"),
        code=parsed.get("code", "(无)"),
        run_output=run_output,
        answer=answer_val,
        answer_unit=parsed.get("answer_unit", ""),
        i_checklist=_render_i_checklist(parsed, exec_result),
        checklist_new=parsed.get("checklist_new", "").strip() or "(无)",
        check_report=checker.render_report(result.findings),
    )


def _render_i_checklist(parsed, exec_result):
    """Render the I-checklist.

    The model proposes the checklist in <I_CHECKLIST>; the中间关键结果 should match
    the `CHECK:` lines the code actually printed. Since执行值才是事实，附上代码实跑出的
    CHECK 行作为权威中间结果，模型清单作为措辞参考。
    """
    model_list = (parsed.get("i_checklist") or "").strip()
    checks = list(getattr(exec_result, "checks", []) or []) if exec_result else []

    if model_list and not checks:
        return model_list
    if not model_list and not checks:
        return "(无)"

    parts = []
    if model_list:
        parts.append(model_list)
    if checks:
        executed = "\n".join(f"- {c}" for c in checks)
        parts.append("**（以下中间关键结果由代码真实运行得出）**\n" + executed)
    return "\n\n".join(parts)


def save(result, subject, reason_key, output_dir=None):
    """Write the rendered markdown to output/ and return the file path."""
    out_dir = output_dir or config_loader.ensure_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{_slugify(subject)}__{_slugify(reason_key)}__{ts}.md"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_markdown(result, subject, reason_key))
    return path
