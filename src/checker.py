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


# Pull every floating-point literal out of a chunk of prose (for answer matching).
_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _extract_numbers(text):
    """Return all numeric literals appearing in `text` as floats."""
    out = []
    for tok in _NUMBER_RE.findall(text or ""):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _answer_matches(numbers, answer, rel_tol=0.02):
    """True if any value in `numbers` is within rel_tol of `answer`."""
    if answer is None:
        return True  # nothing to compare against
    for n in numbers:
        denom = abs(answer) if answer != 0 else 1.0
        if abs(n - answer) <= rel_tol * denom:
            return True
    return False


# Integration-interval endpoints written as (a, b) / [a, b] pairs in the code —
# used to flag answers that are merely a fallback to the span boundary.
_SPAN_RE = re.compile(
    r"[\[(]\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*,"
    r"\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*[\])]"
)


def _integration_bounds(code):
    """Collect numeric endpoints of (a, b) / [a, b] literals in the code.

    Returns the non-zero endpoints (0 is a common, legitimate start time and
    would cause false positives against answers near zero)."""
    bounds = []
    for lo, hi in _SPAN_RE.findall(code or ""):
        for tok in (lo, hi):
            try:
                v = float(tok)
            except ValueError:
                continue
            if v != 0:
                bounds.append(v)
    return bounds


def _has_huge_magnitude(stdout, threshold=1e8):
    """True if any number printed to stdout exceeds `threshold` in magnitude
    (a sign of numerical blow-up / non-convergent root finding)."""
    for v in _extract_numbers(stdout):
        if abs(v) > threshold:
            return True
    return False


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

    # --- 4d. skill 要求：应打印 4-6 条中间关键结果（CHECK 行）供 I-checklist 使用 ---
    # 统计"清洗后仍合格"的 CHECK 条数（去掉无数值/实现细节行），更贴近最终 md 里的实际条数。
    # 延迟导入 pipeline，避免与其 `from . import checker` 形成模块级循环导入。
    from . import pipeline
    raw_checks = list(getattr(exec_result, "checks", []) or [])
    good_checks = [c for c in raw_checks if pipeline._clean_check_line(c)]
    if not good_checks:
        findings.append(Finding(
            "warn",
            "代码未打印任何合格的 `CHECK:` 中间关键结果行（需含数值、非实现细节；"
            "I-checklist 将缺中间项）",
        ))
    elif not (4 <= len(good_checks) <= 6):
        findings.append(Finding(
            "warn",
            f"合格的 CHECK 中间结果共 {len(good_checks)} 条（期望 4-6 条）；"
            f"原始打印 {len(raw_checks)} 条，其余为无数值/实现细节行已被剔除",
        ))

    # --- 4e. skill 要求：模型应给出 I-checklist（总条数不再限制——最终 md 由执行事实
    # 重建，答案固定 1 条 + 中间 4-6 条，条数由 4d 段按合格 CHECK 数把关）---
    if not _count_list_items(parsed.get("i_checklist", "")):
        findings.append(Finding("warn", "缺少 I-checklist"))

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

    # --- 7b. 答案疑似为积分区间端点伪值 ---
    # 若 ANSWER 恰好等于代码里某个积分区间端点（如 t_span=(0,2000) 的 2000），
    # 多半是"事件未在区间内发生、回退到区间端点"的伪值，而非真实求解结果。
    # 这是客观错误（答案就是错的），故无论 judge 模式一律 fail，触发重试。
    bounds = _integration_bounds(code)
    if a == a and abs(a) != float("inf") and \
            any(abs(a - b) <= max(1e-9, 1e-6 * abs(b)) for b in bounds):
        findings.append(Finding(
            "fail",
            f"答案 {a} 恰好等于代码中的积分区间端点，疑似事件未在区间内发生而回退为"
            f"端点伪值。应延长积分区间并用 solve_ivp 的 events 精确捕捉事件时刻，"
            f"事件未发生时应 raise 而非返回端点。",
        ))

    # --- 7c. 数值发散迹象：stdout 出现极大量级数值 ---
    # 残差/状态量量级爆炸（|x|>1e8）通常意味着求根/积分发散、未真正收敛。
    if _has_huge_magnitude(exec_result.stdout, threshold=1e8):
        sev = "fail" if judge_mode == "block" else "warn"
        findings.append(Finding(
            sev,
            "运行输出中出现极大量级数值（|值|>1e8），疑似数值发散或求根未收敛，"
            "请改用更稳健的求解器（如 solve_bvp / brentq 先扫描定界）并校验收敛。",
        ))

    # --- 8. model-claimed answer must match the EXECUTED answer ---
    # The final output reconstructs the I-checklist from exec_result, but a
    # mismatch here means the model's prose disagreed with reality — report it
    # honestly. warn by default (advisory); fail under judge_mode='block'.
    claim_text = parsed.get("i_checklist", "")
    if claim_text.strip() and not _answer_matches(_extract_numbers(claim_text), a):
        sev = "fail" if judge_mode == "block" else "warn"
        findings.append(Finding(
            sev,
            f"模型 checklist 中未出现与实跑答案 {a} 一致的数值，输出已按实跑值重建",
        ))

    # --- 9. optional LLM semantic judge ---
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
            "你判断四件事：(1) 题目是否真正体现了指定考点的数学结构；"
            "(2) 题面、考点、代码三者是否一致（例如声称打靶法就该是边值问题）；"
            "(3) 题目是否确实无法手算、必须编程；"
            "(4) 【物理合理性】答案数值在物理上是否可能——结合题面给定的物理约束判断，"
            "例如：冷却/换热问题中温度必落在初温与环境温度之间，不能低于环境温度；"
            "答案恰好等于积分区间端点（如 t_span 上限）往往是'事件未在区间内发生而回退端点'"
            "的伪值；求根/打靶的残差应真正收敛到 0，而非在极大量级间振荡。\n"
            "不要纠结无关紧要的实现细节（如取中点用 N//2）。"
            "只有当出现【考点/题面/代码实质不一致】【其实可以手算】或【答案物理上不可能/为"
            "积分端点伪值/数值未收敛】时才判 FAIL。\n"
            "只输出一行：PASS，或 FAIL:<简短原因>。"
        )
        checks_txt = "\n".join(getattr(exec_result, "checks", []) or [])
        user_p = (
            f"指定考点：{reason['label']}\n"
            f"要求的数学结构：{reason['math_structure']}\n"
            f"必须满足：{reason.get('must_have', [])}\n"
            f"代码已成功执行，得到答案：{answer}\n"
            f"代码打印的中间关键结果(CHECK)：\n{checks_txt}\n\n"
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
