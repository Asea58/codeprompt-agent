"""Execute generated Python code in an isolated subprocess and extract the answer.

Safety/robustness measures for the MVP:
- run in a separate process via `python3 -c`, never exec() in-process
- hard wall-clock timeout
- no network/file expectations (code is told not to use them)
- capture stdout/stderr; pull the `ANSWER: <number>` line the prompt mandates
"""
import os
import re
import subprocess
import sys
import tempfile
import time

ANSWER_RE = re.compile(r"^ANSWER:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE)
CHECK_RE = re.compile(r"^CHECK:\s*(.+?)\s*$", re.MULTILINE)


class ExecutionResult:
    def __init__(self, ok, answer, stdout, stderr, error=None, checks=None,
                 elapsed=None):
        self.ok = ok
        self.answer = answer          # float or None
        self.stdout = stdout
        self.stderr = stderr
        self.error = error            # short human-readable reason when ok is False
        self.checks = checks or []    # list[str]: 代码打印的 CHECK 中间关键结果行
        self.elapsed = elapsed        # float seconds of wall-clock run time, or None

    def as_dict(self):
        return {
            "ok": self.ok,
            "answer": self.answer,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "checks": self.checks,
            "elapsed": self.elapsed,
        }


def run_code(code, timeout=60):
    """Run `code` in a subprocess; return ExecutionResult.

    The generated code is expected to print a line `ANSWER: <number>`.
    """
    # Write to a temp file so tracebacks have real line numbers.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp.py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code)
        tmp.close()

        try:
            t0 = time.monotonic()
            proc = subprocess.run(
                [sys.executable, tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout,
                # minimal, inherited env; code is restricted to numpy/scipy by prompt
            )
            elapsed = time.monotonic() - t0
        except subprocess.TimeoutExpired as e:
            return ExecutionResult(
                ok=False,
                answer=None,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                error=f"执行超时（>{timeout}s）",
                elapsed=float(timeout),
            )

        stdout, stderr = proc.stdout, proc.stderr
        checks = extract_checks(stdout)

        if proc.returncode != 0:
            return ExecutionResult(
                ok=False, answer=None, stdout=stdout, stderr=stderr,
                error=f"代码运行报错（exit {proc.returncode}）",
                checks=checks, elapsed=elapsed,
            )

        answer = extract_answer(stdout)
        if answer is None:
            return ExecutionResult(
                ok=False, answer=None, stdout=stdout, stderr=stderr,
                error="未能从输出中解析到 `ANSWER: <数值>` 行",
                checks=checks, elapsed=elapsed,
            )

        return ExecutionResult(
            ok=True, answer=answer, stdout=stdout, stderr=stderr,
            checks=checks, elapsed=elapsed,
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def extract_answer(stdout):
    """Return the float from the last `ANSWER:` line, or None."""
    matches = ANSWER_RE.findall(stdout or "")
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def extract_checks(stdout):
    """Return the list of `CHECK: ...` lines (中间关键结果) in order, stripped."""
    return [m.strip() for m in CHECK_RE.findall(stdout or "") if m.strip()]
