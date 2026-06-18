# config/ 字段说明（给协作者）

本目录是 Agent 的"可配置面"。**日常扩展只改这里，不动 `src/`。**

---

## llm.json — 模型配置

```jsonc
{
  "provider": "deepseek",        // 当前启用哪个模型，取值为 providers 下的 key
  "providers": {
    "deepseek": {
      "base_url": "...",          // OpenAI 兼容接口地址
      "model": "deepseek-chat",   // 模型名
      "api_key_env": "DEEPSEEK_API_KEY", // 从哪个环境变量读 key（绝不写进文件）
      "temperature": 0.7,
      "max_tokens": 8000
    }
  }
}
```

- **换模型**：改 `provider` 字段。
- **加模型**：在 `providers` 里加一条（任何 OpenAI 兼容接口都行）。

---

## reasons.json — "不能手算的原因" 映射表（最核心）

每个 key 是一类考点，也是 CLI `--reason` 的取值。字段：

| 字段 | 作用 |
|---|---|
| `label` | 人类可读的考点名 |
| `math_structure` | 该考点对应的**数学结构**（必须落到方程上） |
| `numerical_method` | 对应的数值方法 |
| `must_have` | 生成题目**必须满足**的硬约束（自检环节会用） |
| `answer_pattern` | 答案应该长什么样（单一数值的类型） |
| `pitfalls` | 常见坑，提醒 LLM 规避 |

> 加新考点：复制一条改内容即可。`must_have` 写得越具体，自检越有效。

---

## subjects/ — 学科背景库

每个 `<学科>.json` 给 LLM 提供该方向能承载数值方法的物理情境。字段：

| 字段 | 作用 |
|---|---|
| `subject` | 学科名（= 文件名 = CLI `--subject` 取值） |
| `domain_description` | 学科概述 |
| `typical_scenarios` | 典型物理情境清单（LLM 从中取材或借鉴） |
| `common_symbols` | 常用符号约定 |
| `typical_units` | 常用单位 |
| `style_notes` | 出题风格要求 |

> 加新学科（化学/生物…）：复制一个文件改内容，文件名即学科名，CLI 立即可用。
