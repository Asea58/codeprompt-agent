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

ANSWER_RE = re.compile(r"^ANSWER:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE)


class ExecutionResult:
    def __init__(self, ok, answer, stdout, stderr, error=None):
        self.ok = ok
        self.answer = answer          # float or None
        self.stdout = stdout
        self.stderr = stderr
        self.error = error            # short human-readable reason when ok is False

    def as_dict(self):
        return {
            "ok": self.ok,
            "answer": self.answer,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
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
            proc = subprocess.run(
                [sys.executable, tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout,
                # minimal, inherited env; code is restricted to numpy/scipy by prompt
            )
        except subprocess.TimeoutExpired as e:
            return ExecutionResult(
                ok=False,
                answer=None,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                error=f"执行超时（>{timeout}s）",
            )

        stdout, stderr = proc.stdout, proc.stderr

        if proc.returncode != 0:
            return ExecutionResult(
                ok=False, answer=None, stdout=stdout, stderr=stderr,
                error=f"代码运行报错（exit {proc.returncode}）",
            )

        answer = extract_answer(stdout)
        if answer is None:
            return ExecutionResult(
                ok=False, answer=None, stdout=stdout, stderr=stderr,
                error="未能从输出中解析到 `ANSWER: <数值>` 行",
            )

        return ExecutionResult(ok=True, answer=answer, stdout=stdout, stderr=stderr)
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
