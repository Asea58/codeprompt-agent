# codeprompt Agent

输入「学科」+「不能手算的原因」，自动构造一道完整的物理编程题，并通过**真实执行代码**得到可验证的数值答案。

> MVP 第一版：物理学科，覆盖 **力学 / 热学 / 电动力学** 三个方向。

---

## 它解决什么问题

这类题目的共同特点是：物理系统的控制方程无解析解（非线性、非自治、变系数、边值问题、混沌……），
必须靠数值方法求解。本 Agent 把「出题」重构成一条结构化流水线：

```
输入(学科, 不能手算的原因)
   → [1] 方法选型   把"原因"映射到数值方法 + 方程结构 (config/reasons.json)
   → [2] 场景生成   在该学科里挑一个能承载该方法的物理情境 (config/subjects/*)
   → [3] LLM 生成   题面 Query + 解题思路 + 完整可运行代码
   → [4] 执行验证   ★真的跑代码★ 拿到唯一数值答案 (src/executor.py)
   → [5] 一致性自检 题面/考点/代码/答案 四者是否吻合 (src/checker.py)
   → [6] 失败重试   不通过则带着失败原因重新生成
   → 输出四件套     Query / Response / 答案 / 校验报告
```

**核心原则：答案永远来自真实代码执行，绝不让模型臆造运行结果。**

---

## 快速开始

```bash
# 1. 安装依赖（numpy/scipy 已用于跑题，openai 用于调 DeepSeek）
pip install -r requirements.txt

# 2. 不用 API Key 先跑通闭环（mock 模式，验证执行+自检管线）
python3 cli.py --subject 力学 --reason 非线性ODE初值问题 --mock

# 3. 接入 DeepSeek（OpenAI 兼容接口）真正出题
export DEEPSEEK_API_KEY=sk-xxxx
python3 cli.py --subject 热学 --reason 非线性边值问题打靶法
```

生成结果落盘到 `output/`（已 gitignore）。

---

## 如何扩展（给协作者）

**所有"会变的东西"都外置成配置/模板文件，主代码 (`src/`) 一般不用动。**

| 想做的事 | 改哪里 | 是否动主代码 |
|---|---|---|
| 加一个新学科（化学/生物…） | 新增 `config/subjects/<学科>.json` | 否 |
| 加一类"不能手算的原因" | 在 `config/reasons.json` 加一条 | 否 |
| 改输出格式 | 改 `templates/output_format.md` | 否 |
| 换大模型（DeepSeek↔其他） | 改 `config/llm.json` | 否 |
| 调出题口吻/约束 | 改 `templates/system_prompt.md` | 否 |

配置字段说明见 `config/README.md`。

---

## 目录结构

```
codeprompt Agent/
├── cli.py                   # 命令行入口
├── config/
│   ├── llm.json             # 模型配置（provider 可切换）
│   ├── reasons.json         # "不能手算的原因" → 数值方法 + 方程结构 映射表
│   └── subjects/            # 学科背景：力学/热学/电动力学
├── templates/               # 提示词 + 输出格式模板
├── src/                     # 流水线骨架（一般不需改动）
└── output/                  # 生成结果（gitignored）
```
