"""LLM client. Talks to DeepSeek (OpenAI-compatible) or returns a baked-in mock
response so the whole pipeline can be exercised without an API key."""
import os

from . import config_loader


class LLMError(RuntimeError):
    pass


def _build_messages(system_prompt, user_prompt):
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_llm(system_prompt, user_prompt, mock=False):
    """Return raw assistant text. If mock=True, skip the network entirely."""
    if mock:
        return _mock_response()

    cfg = config_loader.load_llm_config()
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise LLMError(
            f"环境变量 {cfg['api_key_env']} 未设置。请先 export，或使用 --mock 跑通管线。"
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise LLMError("缺少 openai 库，请 pip install -r requirements.txt") from e

    client = OpenAI(api_key=api_key, base_url=cfg["base_url"])
    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=_build_messages(system_prompt, user_prompt),
            temperature=cfg.get("temperature", 0.7),
            max_tokens=cfg.get("max_tokens", 8000),
        )
    except Exception as e:  # noqa: BLE001 - surface any SDK/network error uniformly
        raise LLMError(f"调用 {cfg['provider_name']} 失败: {e}") from e

    return resp.choices[0].message.content


def _mock_response():
    """A deterministic, correct sample (平方阻力抛体射程) used to test the pipeline
    end-to-end without an API key. Demonstrates the exact tag format the parser expects."""
    return """<QUERY>
一质量为 $m = 0.15\\,\\text{kg}$ 的球形抛体（半径 $r = 0.035\\,\\text{m}$），以初速度
$v_0 = 35.0\\,\\text{m/s}$、仰角 $\\theta_0 = 45^\\circ$ 从地面发射。空气阻力大小与速度平方
成正比：$\\mathbf{F}_d = -\\tfrac{1}{2}\\rho A C_d v\\,\\mathbf{v}$，其中 $\\rho = 1.225\\,
\\text{kg/m}^3$，$C_d = 0.45$，$A = \\pi r^2$，$g = 9.8\\,\\text{m/s}^2$。

由于阻力 $\\propto v^2$ 使 $v_x, v_y$ 非线性耦合，运动方程无解析解，必须数值求解。

**问题：** 求抛体的水平射程（落地点与发射点的水平距离），单位 m，保留两位小数。

---
**要求：**
1. 使用Python语言（或其他语言）解答本题；
2. 完整写出代码的实现步骤。
</QUERY>

<APPROACH>
阻力与速度平方成正比，使两个方向通过 $v=\\sqrt{v_x^2+v_y^2}$ 非线性耦合，无解析弹道解。
将运动写成一阶 ODE 系统 $[x, y, v_x, v_y]$，用 `solve_ivp` 数值积分，并用 events 机制
检测落地事件 $y=0$，终止时刻的 $x$ 即为射程。
</APPROACH>

<CODE>
import numpy as np
from scipy.integrate import solve_ivp

m = 0.15
r = 0.035
rho = 1.225
C_d = 0.45
g = 9.8
v0 = 35.0
theta0 = np.radians(45.0)

A = np.pi * r**2
b = 0.5 * rho * A * C_d

def ode(t, s):
    x, y, vx, vy = s
    v = np.sqrt(vx**2 + vy**2)
    k = b * v / m
    return [vx, vy, -k * vx, -g - k * vy]

def apex(t, s):
    return s[3]
apex.direction = -1

def hit_ground(t, s):
    return s[1]
hit_ground.terminal = True
hit_ground.direction = -1

sol = solve_ivp(ode, [0, 20], [0.0, 0.0, v0*np.cos(theta0), v0*np.sin(theta0)],
                events=[apex, hit_ground], max_step=1e-3, rtol=1e-9, atol=1e-12)

t_apex = sol.t_events[0][0]
y_apex = sol.y_events[0][0][1]
t_land = sol.t_events[1][0]
x_range = sol.y[0, -1]

print(f"水平射程: {x_range:.2f} m")
print(f"CHECK: 抛体在 t={t_apex:.2f} s 到达最高点，最大高度为 {y_apex:.2f} m。")
print(f"CHECK: 抛体在 t={t_land:.2f} s 落地。")
print(f"ANSWER: {x_range:.2f}")
</CODE>

<ANSWER_UNIT>
水平射程，单位 m
</ANSWER_UNIT>

<I_CHECKLIST>
- 抛体的水平射程约为 49.92 m。
- 抛体在约 t=2.30 s 到达轨迹最高点，最大高度约 25.6 m。
- 抛体在约 t=4.66 s 时落回地面。
- 空气阻力使射程显著小于无阻力理想情形。
</I_CHECKLIST>

<CHECKLIST_NEW>
- 抛体的水平射程约为 49.92 m。
</CHECKLIST_NEW>"""
