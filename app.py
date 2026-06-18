#!/usr/bin/env python3
"""codeprompt Agent — Gradio 前端

一个轻量网页界面，复用 src/pipeline.run()，与 cli.py 共享同一套核心逻辑。
下拉框选项直接从 config/ 自动读取：你新增学科/考点后，刷新页面即可出现，无需改本文件。

启动：
    python3 app.py                 # 本机访问 http://127.0.0.1:7860
    python3 app.py --share         # 生成临时公网链接（给别人用）
    python3 app.py --port 8000     # 自定义端口

API Key 仍从环境变量读取，不经过浏览器：
    export DEEPSEEK_API_KEY=sk-xxxx
"""
import argparse
import os

import gradio as gr

# --- 兼容补丁：gradio 4.44 + 新版依赖在生成 API schema 时有一个已知 bug
# (gradio_client.utils.get_type 遇到布尔型 schema 会抛
#  "argument of type 'bool' is not iterable")，并连带导致启动时的本地健康检查失败。
# 这里就地修正这两个函数，使其容忍布尔/非字典 schema。无需改动依赖版本。
try:
    import gradio_client.utils as _gcu

    _orig_get_type = _gcu.get_type

    def _safe_get_type(schema):
        if not isinstance(schema, dict):
            return "Any"
        return _orig_get_type(schema)

    _gcu.get_type = _safe_get_type

    _orig_j2p = _gcu._json_schema_to_python_type

    def _safe_j2p(schema, defs=None):
        if isinstance(schema, bool):
            return "Any"
        return _orig_j2p(schema, defs)

    _gcu._json_schema_to_python_type = _safe_j2p
except Exception:  # noqa: BLE001 - 补丁失败不应阻断启动
    pass

from src import config_loader, pipeline


# ---------- 从 config/ 动态读取下拉选项 ----------
def get_subjects():
    return config_loader.list_subjects()


def get_reasons():
    """返回 [(显示文本, 取值)]，显示带 label 更友好。"""
    reasons = config_loader.load_reasons()
    return [(f"{key}（{val['label']}）", key) for key, val in reasons.items()]


def reason_detail(reason_key):
    """选中某考点时，在界面上展示它的数学结构/方法，帮助用户理解。"""
    if not reason_key:
        return ""
    try:
        r = config_loader.load_reason(reason_key)
    except ValueError:
        return ""
    musts = "\n".join(f"  - {m}" for m in r.get("must_have", []))
    return (
        f"**数学结构**：{r['math_structure']}\n\n"
        f"**数值方法**：{r['numerical_method']}\n\n"
        f"**必须满足**：\n{musts}\n\n"
        f"**答案形态**：{r['answer_pattern']}"
    )


# ---------- 主回调：调用流水线生成一道题 ----------
def generate_problem(subject, reason_key, judge_mode, retries, timeout, use_mock,
                     progress=gr.Progress()):
    if not subject or not reason_key:
        return "⚠️ 请先选择学科和考点。", "", "", "", "", None

    # 没设 Key 且未勾选 mock，给出明确提示而不是抛栈
    if not use_mock:
        cfg = config_loader.load_llm_config()
        if not os.environ.get(cfg["api_key_env"]):
            return (
                f"⚠️ 未检测到环境变量 {cfg['api_key_env']}。\n\n"
                f"请在启动前 `export {cfg['api_key_env']}=sk-xxxx`，"
                f"或勾选「mock 模式」先体验界面。",
                "", "", "", "", None,
            )

    progress(0.1, desc="生成中（调用模型）...")
    try:
        result = pipeline.run(
            subject=subject,
            reason_key=reason_key,
            max_retries=int(retries),
            timeout=int(timeout),
            mock=use_mock,
            judge_mode=judge_mode,
            verbose=False,
        )
    except ValueError as e:
        return f"❌ 输入错误：{e}", "", "", "", "", None
    except Exception as e:  # noqa: BLE001
        return f"❌ 生成失败：{e}", "", "", "", "", None

    progress(0.9, desc="渲染结果...")

    parsed = result.parsed
    exec_result = result.exec_result
    answer = exec_result.answer if (exec_result and exec_result.answer is not None) else "N/A"
    unit = parsed.get("answer_unit", "")
    run_output = exec_result.stdout.strip() if exec_result else ""
    code = parsed.get("code", "")

    if result.success:
        status = f"✅ 出题成功（共 {result.attempts} 次生成）　答案：**{answer}** （{unit}）"
    else:
        status = f"⚠️ 未通过校验：{result.error}　（以下为最后一次结果，供排查）"

    findings = "\n".join(str(f) for f in result.findings) or "（无）"

    # 同时落盘，返回文件供下载
    saved_path = None
    try:
        saved_path = pipeline.save(result, subject, reason_key)
    except Exception:  # noqa: BLE001 - 落盘失败不应阻断界面
        saved_path = None

    query_md = parsed.get("query", "（无题面）")
    approach_md = parsed.get("approach", "")
    code_block = f"```python\n{code}\n```" if code else "（无代码）"
    detail_md = (
        f"### 解题思路\n{approach_md}\n\n"
        f"### 完整代码\n{code_block}\n\n"
        f"### 运行结果\n```\n{run_output}\n```\n\n"
        f"### 校验报告\n{findings}"
    )

    return status, query_md, detail_md, str(answer), str(unit), saved_path


# ---------- 构建界面 ----------
def build_ui():
    with gr.Blocks(title="codeprompt Agent · 物理编程题生成器") as demo:
        gr.Markdown(
            "# 🧮 codeprompt Agent · 物理编程题生成器\n"
            "选择**学科**与**不能手算的原因**，自动构造一道物理编程题，"
            "并通过**真实运行代码**得到可验证答案。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                subject = gr.Dropdown(
                    choices=get_subjects(), label="学科方向",
                    value=(get_subjects()[0] if get_subjects() else None),
                )
                reason = gr.Dropdown(
                    choices=get_reasons(), label="不能手算的原因（考点）",
                    value=(get_reasons()[0][1] if get_reasons() else None),
                )
                reason_info = gr.Markdown(
                    value=reason_detail(get_reasons()[0][1] if get_reasons() else None),
                )
                with gr.Accordion("高级选项", open=False):
                    judge_mode = gr.Radio(
                        choices=["warn", "block", "off"], value="warn",
                        label="LLM 审核模式",
                        info="warn=仅提示(推荐) | block=可否决重试 | off=关闭",
                    )
                    retries = gr.Slider(0, 5, value=2, step=1, label="最大重试次数")
                    timeout = gr.Slider(10, 180, value=60, step=10, label="代码执行超时(秒)")
                    use_mock = gr.Checkbox(
                        value=False, label="mock 模式（不调用 API，用内置样例体验界面）",
                    )
                btn = gr.Button("🚀 生成题目", variant="primary")

            with gr.Column(scale=2):
                status = gr.Markdown("准备就绪，点击「生成题目」。")
                with gr.Row():
                    answer_box = gr.Textbox(label="答案", interactive=False)
                    unit_box = gr.Textbox(label="单位 / 含义", interactive=False)
                with gr.Tab("题面 Query"):
                    query_out = gr.Markdown()
                with gr.Tab("思路 / 代码 / 校验"):
                    detail_out = gr.Markdown()
                download = gr.File(label="下载完整 .md", interactive=False)

        # 选中考点时刷新说明
        reason.change(fn=reason_detail, inputs=reason, outputs=reason_info)

        btn.click(
            fn=generate_problem,
            inputs=[subject, reason, judge_mode, retries, timeout, use_mock],
            outputs=[status, query_out, detail_out, answer_box, unit_box, download],
        )

        gr.Markdown(
            "---\n"
            "💡 扩展：新增学科改 `config/subjects/`，新增考点改 `config/reasons.json`，"
            "改输出格式改 `templates/output_format.md`。详见 `使用教程.md`。"
        )
    return demo


def main():
    parser = argparse.ArgumentParser(description="codeprompt Agent 网页前端")
    parser.add_argument("--port", type=int, default=7860, help="端口（默认 7860）")
    parser.add_argument("--share", action="store_true", help="生成临时公网分享链接")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    args = parser.parse_args()

    demo = build_ui()
    # show_api=False 关闭 /info schema 生成（老版 gradio 在该环节有兼容 bug）。
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        show_api=False,
    )


if __name__ == "__main__":
    main()
