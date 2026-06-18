#!/usr/bin/env python3
"""codeprompt Agent — 命令行入口

用法:
    python3 cli.py --subject 力学 --reason 非线性ODE初值问题            # 调 DeepSeek
    python3 cli.py --subject 力学 --reason 非线性ODE初值问题 --mock     # 不用 API Key 跑通管线
    python3 cli.py --list                                                # 查看可用学科 / 考点
"""
import argparse
import sys

from src import config_loader, pipeline


def cmd_list():
    print("可用学科 (--subject):")
    for s in config_loader.list_subjects():
        print(f"  - {s}")
    print("\n可用考点 / 不能手算的原因 (--reason):")
    for key, val in config_loader.load_reasons().items():
        print(f"  - {key}\n      {val['label']}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="输入 学科 + 不能手算的原因，自动构造并验证一道物理编程题。"
    )
    parser.add_argument("--subject", help="学科背景，如：力学 / 热学 / 电动力学")
    parser.add_argument("--reason", help="不能手算的原因（考点），见 --list")
    parser.add_argument("--mock", action="store_true",
                        help="不调用 API，用内置样例跑通管线（自测用）")
    parser.add_argument("--retries", type=int, default=2, help="生成失败时的最大重试次数")
    parser.add_argument("--timeout", type=int, default=60, help="单次代码执行超时(秒)")
    parser.add_argument("--list", action="store_true", help="列出可用学科与考点后退出")
    parser.add_argument("--no-save", action="store_true", help="不写出 markdown 文件")
    parser.add_argument(
        "--judge", choices=["warn", "block", "off"], default="warn",
        help="LLM 语义审核模式：warn=仅提示(默认) | block=可否决并重试 | off=关闭",
    )
    args = parser.parse_args(argv)

    if args.list:
        cmd_list()
        return 0

    if not args.subject or not args.reason:
        parser.error("必须同时提供 --subject 和 --reason（或使用 --list 查看可选值）")

    try:
        result = pipeline.run(
            subject=args.subject,
            reason_key=args.reason,
            max_retries=args.retries,
            timeout=args.timeout,
            mock=args.mock,
            judge_mode=args.judge,
        )
    except ValueError as e:
        print(f"\n输入错误：{e}", file=sys.stderr)
        return 2

    print("\n" + "=" * 60)
    if result.success:
        print(f"✅ 出题成功（共 {result.attempts} 次生成）")
        print(f"   答案：{result.exec_result.answer}  （{result.parsed.get('answer_unit','')}）")
    else:
        print(f"⚠️  未能产出通过校验的题目：{result.error}")
        print("   仍会保存最后一次结果供排查。")

    if not args.no_save:
        path = pipeline.save(result, args.subject, args.reason)
        print(f"   已保存：{path}")
    print("=" * 60)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
