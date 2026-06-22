"""Consistency self-check: does the generated problem actually match the claimed
'不能手算的原因'? Catches the most common failure — problem/code drifting away
from the stated 考点.

Two layers:
  1. Heuristic checks (no API needed; always run, so --mock stays fully usable).
  2. Optional LLM judge (skipped in mock mode) for semantic alignment.

A check produces a list of findings; the candidate passes only if there are no
findings of severity 'fail'.
"""
import re

from . import config_loader, llm_client

# Markers that hint a given numerical method is genuinely present in the code.
# Keyed by reason key in reasons.json. Used only as a soft signal.
_METHOD_MARKERS = {
    "非线性ODE初值问题": ["solve_ivp", "odeint", "rk4", "runge"],
    "非线性边值问题打靶法": ["solve_ivp", "shoot", "secant", "bisect", "root_scalar", "brentq"],
    "非线性边值问题有限差分": ["diag", "thomas", "newton", "jacobian", "tridiag", "linalg"],
    "事件检测求阈值时刻": ["events", "terminal", "brentq", "root_scalar", "direction"],
    "混沌系统对初值敏感": ["solve_ivp", "rtol", "atol"],
    "变系数微分方程": ["solve_ivp", "odeint", "brentq", "root_scalar"],
}

# Nonlinear-structure markers — at least one should appear for "无解析解" claims.
_NONLINEAR_MARKERS = [
    "sin", "cos", "sinh", "cosh", "tanh", "**3", "**4", "**2",
    "np.exp", "exp(", "sqrt", "abs(",
]

# Things generated code must NOT do (prompt forbids them).
_FORBIDDEN = [
    (r"\bopen\s*\(", "代码打开了文件"),
    (r"\brequests\b", "代码疑似访问网络 (requests)"),
    (r"\burllib\b", "代码疑似访问网络 (urllib)"),
    (r"\bsocket\b", "代码使用了 socket"),
    (r"\bsubprocess\b", "代码调用了 subprocess"),
    (r"\bos\.system\b", "代码调用了 os.system"),
    (r"\beval\s*\(", "代码使用了 eval"),
]


class Finding:
    def __init__(self, severity, message):
        self.severity = severity  # 'fail' | 'warn'
        self.message = message

    def __str__(self):
        tag = "❌ FAIL" if self.severity == "fail" else "⚠️  WARN"
        return f"{tag}: {self.message}"


def _count_list_items(text):
    """Count markdown bullet items (lines starting with -, *, • or a number)."""
    if not text:
        return 0
    n = 0
    for line in text.splitlines():
        if re.match(r"\s*(?:[-*•]|\d+[.)、])\s+", line):
            n += 1
    return n


def check(parsed, exec_result, reason_key, mock=False, judge_mode="block"):
    """Return (passed: bool, findings: list[Finding]).

    judge_mode: 'block' = LLM judge can FAIL (gate); 'warn' = judge findings are
    downgraded to warnings; 'off' = skip the LLM judge entirely.
    """
    findings = []
    code = parsed.get("code", "")
    query = parsed.get("query", "")
    code_l = code.lower()

    # --- 1. execution must have succeeded with an answer ---
    if not exec_result.ok:
        findings.append(Finding("fail", f"代码未能成功执行/取数：{exec_result.error}"))
        # No point checking the rest if it didn't run.
        return False, findings
    if exec_result.answer is None:
        findings.append(Finding("fail", "未提取到数值答案"))
        return False, findings

    # --- 2. forbidden operations ---
    for pat, msg in _FORBIDDEN:
        if re.search(pat, code):
            findings.append(Finding("fail", msg))

    # --- 3. query must ask for Python ---
    if "python" not in query.lower():
        findings.append(Finding("warn", "题面未明确要求使用 Python"))

    # --- 3b. skill 要求：题面末尾应有"完整写出代码的实现步骤"类编程解答标注 ---
    if not re.search(r"实现步骤|解答本题|编写代码|实现代码", query):
        findings.append(Finding("warn", "题面末尾缺少编程解答要求（应标注：使用Python解答 + 完整写出代码实现步骤）"))

    # --- 3c. skill 要求：题面不可依赖图片 ---
    if re.search(r"如图|见图|下图|上图|图\s*\d|!\[", query):
        findings.append(Finding("warn", "题面疑似依赖图片（skill 要求不得含/依赖图片）"))

    # --- 4. query should state a decimal-place / precision requirement ---
    if not re.search(r"保留|精确|小数", query):
        findings.append(Finding("warn", "题面未规定保留小数位（答案精度不明确）"))

    # --- 4b. skill 要求：精度不可过高（如"小数点后6位"），保留 1-2 位 ---
    m_prec = re.search(r"小数(?:点后|位)?\s*(\d+)\s*位", query)
    if m_prec and int(m_prec.group(1)) > 2:
        findings.append(Finding(
            "warn", f"题面要求保留 {m_prec.group(1)} 位小数，精度过高（skill 要求 1-2 位）",
        ))

    # --- 4c. skill 要求：运行时间 < 5 秒 ---
    if getattr(exec_result, "elapsed", None) is not None and exec_result.elapsed > 5.0:
        findings.append(Finding(
            "warn", f"代码运行耗时 {exec_result.elapsed:.1f}s，超过 5 秒（skill 要求 5 秒内）",
        ))

    # --- 4d. skill 要求：应打印中间关键结果（CHECK 行）供 I-checklist 使用 ---
    n_checks = len(getattr(exec_result, "checks", []) or [])
    if n_checks == 0:
        findings.append(Finding("warn", "代码未打印任何 `CHECK:` 中间关键结果行（I-checklist 将缺中间项）"))

    # --- 4e. skill 要求：I-checklist 条数 4-6 ---
    i_items = _count_list_items(parsed.get("i_checklist", ""))
    if i_items and not (4 <= i_items <= 6):
        findings.append(Finding("warn", f"I-checklist 共 {i_items} 条，建议 4-6 条"))
    elif not i_items:
        findings.append(Finding("warn", "缺少 I-checklist"))

    # --- 4f. skill 要求：checklist_new 第一部分必须是答案 ---
    if not _count_list_items(parsed.get("checklist_new", "")):
        findings.append(Finding("warn", "缺少 checklist_new（第一部分应为答案）"))

    # --- 5. numerical-method marker present? ---
    markers = _METHOD_MARKERS.get(reason_key, [])
    if markers and not any(m in code_l for m in markers):
        findings.append(Finding(
            "warn",
            f"代码中未见该考点典型方法的痕迹（期望含 {markers} 之一）",
        ))

    # --- 6. nonlinearity present? (most reasons claim 无解析解) ---
    if not any(m in code_l for m in _NONLINEAR_MARKERS):
        findings.append(Finding(
            "warn", "代码中未见明显非线性/特殊函数项，可能退化为可解析问题",
        ))

    # --- 7. answer sanity ---
    a = exec_result.answer
    if a != a:  # NaN
        findings.append(Finding("fail", "答案为 NaN"))
    elif abs(a) == float("inf"):
        findings.append(Finding("fail", "答案为无穷大（数值发散）"))

    # --- 8. optional LLM semantic judge ---
    if not mock and judge_mode != "off":
        verdict = _llm_judge(parsed, exec_result, reason_key)
        if verdict and verdict.get("severity"):
            sev = verdict["severity"]
            # In 'warn' mode, never let the judge hard-fail a problem whose code
            # already executed and produced a sane answer.
            if judge_mode == "warn" and sev == "fail":
                sev = "warn"
            findings.append(Finding(sev, "LLM 审核：" + verdict["message"]))

    passed = not any(f.severity == "fail" for f in findings)
    return passed, findings


def _llm_judge(parsed, exec_result, reason_key):
    """Ask the model whether the problem genuinely matches the 考点. Best-effort:
    any error here returns None (judge skipped) rather than failing the pipeline.

    The judge is told the code ALREADY executed successfully and produced a
    numeric answer, so it must NOT speculate about runtime/index errors or other
    implementation bugs — execution is the ground truth. Its only job is semantic
    alignment: 考点 ↔ 题面 ↔ 代码, and whether the problem is genuinely
    non-hand-computable.
    """
    try:
        reason = config_loader.load_reason(reason_key)
        answer = exec_result.answer if exec_result else None
        sys_p = (
            "你是物理编程题的语义审核员。代码已经在沙箱中真实运行成功并得到了数值答案，"
            "因此你【绝对不要】去猜测代码会崩溃、数组越界、运行报错或有实现 bug——"
            "执行结果就是事实，这些都已被证否。\n"
            "你只判断三件事：(1) 题目是否真正体现了指定考点的数学结构；"
            "(2) 题面、考点、代码三者是否一致（例如声称打靶法就该是边值问题）；"
            "(3) 题目是否确实无法手算、必须编程。\n"
            "不要纠结无关紧要的细节（如取中点用 N//2 这类实现选择）。"
            "只有当出现【实质性的考点/题面/代码不一致】或【其实可以手算】时才判 FAIL。\n"
            "只输出一行：PASS，或 FAIL:<简短原因>。"
        )
        user_p = (
            f"指定考点：{reason['label']}\n"
            f"要求的数学结构：{reason['math_structure']}\n"
            f"必须满足：{reason.get('must_have', [])}\n"
            f"代码已成功执行，得到答案：{answer}\n\n"
            f"题面：\n{parsed.get('query','')}\n\n"
            f"代码：\n{parsed.get('code','')}\n"
        )
        out = llm_client.call_llm(sys_p, user_p, mock=False).strip()
        if out.upper().startswith("PASS"):
            return None
        if out.upper().startswith("FAIL"):
            return {"severity": "fail", "message": out}
        return {"severity": "warn", "message": out}
    except Exception as e:  # noqa: BLE001 - judge is best-effort
        return {"severity": "warn", "message": f"审核调用失败（已跳过）：{e}"}


def render_report(findings):
    if not findings:
        return "✅ 全部检查通过，无异常。"
    return "\n".join(str(f) for f in findings)
