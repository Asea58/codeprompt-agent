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
import batch as batch_mod


# ---------- 从 config/ 动态读取下拉选项 ----------
def get_subjects():
    return config_loader.list_subjects()


def get_reasons():
    """返回 [(显示文本, 取值)]，显示带 label 更友好。"""
    reasons = config_loader.load_reasons()
    return [(f"{key}（{val['label']}）", key) for key, val in reasons.items()]


def reason_detail(reason_key):
    """返回某考点的数学结构/方法说明（保留备用，当前批量界面未展示）。"""
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


# ---------- 批量回调：跑「学科 × 考点」矩阵，复用 batch.run_batch ----------
def run_batch_ui(sel_subjects, sel_reasons, count, judge_mode, retries, timeout,
                 use_mock, progress=gr.Progress()):
    # 缺省取全集
    subjects = sel_subjects or get_subjects()
    reasons = sel_reasons or [k for _, k in get_reasons()]
    if not subjects or not reasons:
        return "⚠️ 请至少选择一个学科和一个考点。", None, None

    if not use_mock:
        cfg = config_loader.load_llm_config()
        if not os.environ.get(cfg["api_key_env"]):
            return (
                f"⚠️ 未检测到环境变量 {cfg['api_key_env']}。请先 "
                f"`export {cfg['api_key_env']}=sk-xxxx` 再启动，或勾选「mock 模式」体验。",
                None, None,
            )

    count = max(1, min(10, int(count)))
    total = len(subjects) * len(reasons) * count

    def _cb(done, tot, subject, reason_key, seq, result):
        mark = "✅" if (result and result.success) else "❌"
        progress(done / tot, desc=f"[{done}/{tot}] {subject} × {reason_key} #{seq} {mark}")

    progress(0.0, desc=(
        f"开始批量出题：{len(subjects)} 学科 × {len(reasons)} 考点 × {count} 道 = {total} 道"
    ))
    try:
        summary = batch_mod.run_batch(
            subjects, reasons, count=count,
            mock=use_mock, retries=int(retries), timeout=int(timeout),
            judge_mode=judge_mode, progress_cb=_cb,
        )
    except Exception as e:  # noqa: BLE001
        return f"❌ 批量执行失败：{e}", None, None

    fail = summary["total"] - summary["success_count"]
    status = (
        f"✅ 批量完成：共 {summary['total']} 道　成功 {summary['success_count']}　失败 {fail}\n\n"
        f"📁 输出目录：`{summary['batch_dir']}`"
    )
    # 表格数据（含表头）供界面预览
    table = [summary["header"]] + summary["rows"]
    return status, table, summary["csv_path"]


# ---------- 构建界面 ----------
def build_ui():
    with gr.Blocks(title="codeprompt Agent · 物理编程题生成器") as demo:
        gr.Markdown(
            "# 🧮 codeprompt Agent · 物理编程题生成器\n"
            "选择**学科**与**不能手算的原因**、设定**出题数量**，批量构造物理编程题，"
            "并通过**真实运行代码**得到可验证答案，汇总成总表。"
        )

        with gr.Tab("批量出题"):
          gr.Markdown(
              "选择多个**学科**与**考点**（不选=全部），设定每个组合的**出题数量**，"
              "一次性跑完所有组合，汇总成一张总表，每题另存一份 .md。"
              "组合多时耗时较长，请耐心等待。"
          )
          with gr.Row():
            with gr.Column(scale=1):
                b_subjects = gr.CheckboxGroup(
                    choices=get_subjects(), label="学科（不选=全部）",
                )
                b_reasons = gr.CheckboxGroup(
                    choices=get_reasons(), label="考点（不选=全部）",
                )
                b_count = gr.Slider(
                    1, 10, value=1, step=1, label="每个组合出题数量",
                    info="每个「学科 × 考点」组合生成几道题（1-10）",
                )
                with gr.Accordion("高级选项", open=False):
                    b_judge = gr.Radio(
                        choices=["warn", "block", "off"], value="warn",
                        label="LLM 审核模式",
                        info="warn=仅提示(推荐) | block=可否决重试 | off=关闭",
                    )
                    b_retries = gr.Slider(0, 5, value=2, step=1, label="最大重试次数")
                    b_timeout = gr.Slider(10, 180, value=60, step=10, label="代码执行超时(秒)")
                    b_mock = gr.Checkbox(
                        value=False, label="mock 模式（不调用 API，用内置样例体验流程）",
                    )
                b_btn = gr.Button("🚀 批量生成", variant="primary")

            with gr.Column(scale=2):
                b_status = gr.Markdown("准备就绪。不选学科/考点 = 跑全量矩阵。")
                b_table = gr.Dataframe(
                    label="汇总结果", interactive=False, wrap=True,
                )
                b_download = gr.File(label="下载汇总 CSV", interactive=False)

          b_btn.click(
              fn=run_batch_ui,
              inputs=[b_subjects, b_reasons, b_count, b_judge, b_retries, b_timeout, b_mock],
              outputs=[b_status, b_table, b_download],
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
