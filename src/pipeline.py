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
        checklist_new=_render_checklist_new(parsed, exec_result),
        check_report=checker.render_report(result.findings),
    )


def _format_answer_line(parsed, exec_result, prefix):
    """Build a single authoritative answer bullet from the EXECUTED value.

    The number always comes from exec_result.answer (代码真实运行得出), never from
    the model's prose — this is what keeps the checklists from contradicting the
    实跑结果."""
    answer = exec_result.answer if exec_result else None
    unit = (parsed.get("answer_unit") or "").strip()
    if answer is None:
        return None
    unit_part = f"（{unit}）" if unit else ""
    return f"- {prefix}{answer}{unit_part}。"


_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _restates_answer(check_line, answer):
    """True if a CHECK line's only/leading number just repeats the answer value
    (so listing it again under the answer bullet would be redundant)."""
    if answer is None:
        return False
    for tok in _NUM_RE.findall(check_line):
        try:
            v = float(tok)
        except ValueError:
            continue
        denom = abs(answer) if answer != 0 else 1.0
        if abs(v - answer) <= 0.005 * denom:  # within 0.5% → same quantity
            return True
    return False


def _render_i_checklist(parsed, exec_result):
    """Render the I-checklist entirely from EXECUTION facts.

    首条为权威答案（exec_result.answer），其后逐条为代码打印的 CHECK 中间关键结果。
    与答案数值重复的 CHECK 行会被剔除（答案只占一条）。模型在 <I_CHECKLIST> 里的猜测
    数值一律丢弃，避免"猜测段 + 实跑段"自相矛盾。仅当执行结果缺失时才回退模型原文。
    """
    answer = exec_result.answer if exec_result else None
    checks = list(getattr(exec_result, "checks", []) or []) if exec_result else []
    answer_line = _format_answer_line(parsed, exec_result, prefix="本题答案：")

    if answer_line is None and not checks:
        # No execution facts to build from — fall back to whatever the model gave.
        return (parsed.get("i_checklist") or "").strip() or "(无)"

    parts = []
    if answer_line:
        parts.append(answer_line)
        # drop CHECK lines that merely restate the final answer value
        checks = [c for c in checks if not _restates_answer(c, answer)]
    parts.extend(f"- {c}" for c in checks)
    return "\n".join(parts)


# 题面里"采用/使用 XXX法/方法"的指定算法 —— 用于补全 checklist_new 第二部分。
_METHOD_RE = re.compile(r"(?:采用|使用|利用|通过|借助)\s*([一-鿿 A-Za-z_]{2,12}?法)")


def _infer_method(query):
    """If the query explicitly mandates a named method (打靶法/有限差分法/蒙特卡洛法
    …), return it so checklist_new can record it; else None."""
    if not query:
        return None
    m = _METHOD_RE.search(query)
    return m.group(1).strip() if m else None


def _render_checklist_new(parsed, exec_result):
    """Render checklist_new with the answer forced to the EXECUTED value.

    第一部分（答案）用 exec_result.answer 重建，确保与 `## 答案` 一致；第二部分（指定
    解题方法）：优先保留模型 <CHECKLIST_NEW> 中除首条外的条目；若模型漏写但题面明确
    指定了某算法，则据题面补一条。
    """
    answer_line = _format_answer_line(parsed, exec_result, prefix="问题的答案：")
    model_text = (parsed.get("checklist_new") or "").strip()

    if answer_line is None:
        # 无实跑答案：退回模型原文，仍优于凭空。
        return model_text or "(无)"

    # 模型清单里除首条（答案）外的其余 `- ` 条目 = 指定解题方法部分，原样保留。
    extra_items = []
    seen_first = False
    for line in model_text.splitlines():
        if re.match(r"\s*-\s+", line):
            if not seen_first:
                seen_first = True  # skip the model's (possibly wrong) answer line
                continue
            extra_items.append(line.strip())

    # 模型漏写方法、但题面明确指定了算法 → 据题面补一条。
    if not extra_items:
        method = _infer_method(parsed.get("query", ""))
        if method:
            extra_items.append(f"- 指定的解题方法：{method}。")

    parts = [answer_line]
    parts.extend(extra_items)
    return "\n".join(parts)


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
