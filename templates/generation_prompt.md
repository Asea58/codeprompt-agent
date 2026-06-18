请构造一道物理编程题。

## 本题约束（由系统给定）

- **学科背景**：{subject}
  - 领域说明：{domain_description}
  - 可取材的典型情境：{typical_scenarios}
  - 常用符号：{common_symbols}
  - 常用单位：{typical_units}
  - 风格要求：{style_notes}

- **核心考点（不能手算的原因）**：{reason_label}
  - 必须体现的数学结构：{math_structure}
  - 应采用的数值方法：{numerical_method}
  - **必须满足的硬约束**：
{must_have_block}
  - 答案形态：{answer_pattern}
  - 需规避的坑：{pitfalls}

{extra_requirements_block}

## 输出格式（严格遵守，便于程序解析）

必须依次输出以下用 XML 标签包裹的四个部分，标签独占一行：

<QUERY>
（完整题面：物理场景 + 全部参数数值 + 控制方程(LaTeX) + 初始/边界条件 + 明确的求解任务 + 保留小数位要求。题面里必须包含"使用 Python 语言解答"的要求。）
</QUERY>

<APPROACH>
（解题思路：为什么不能手算、采用什么数值方法、关键步骤。用 Markdown。）
</APPROACH>

<CODE>
（完整可运行的 Python 代码，只用 numpy/scipy。代码最后必须打印最终答案，并单独打印一行 `ANSWER: <纯数值>`，不带单位。）
</CODE>

<ANSWER_UNIT>
（答案的单位与含义，一行，如：水平射程，单位 m）
</ANSWER_UNIT>

不要输出任何标签以外的额外解释。
