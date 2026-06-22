#!/usr/bin/env python3
"""codeprompt Agent — 批量出题入口

把「学科 × 考点」笛卡尔积一次性跑完，每题落一份 .md，并汇总成一张 summary.csv，
方便总览成功率与答案，再按需去看单题细节。复用 cli.py 同一套 pipeline.run()。

用法:
    python3 batch.py                                  # 全量矩阵（全部学科 × 全部考点）
    python3 batch.py --mock                           # 不调 API，跑通管线/CSV/落盘
    python3 batch.py --subjects 力学,热学              # 只跑指定学科
    python3 batch.py --reasons 事件检测求阈值时刻       # 只跑指定考点
    python3 batch.py --list                           # 查看可用学科 / 考点

产出: output/batch_<时间戳>/ 下含 summary.csv + 每题一个 .md
"""
import argparse
import csv
import itertools
import os
import sys
from datetime import datetime

from src import config_loader, pipeline


def cmd_list():
    print("可用学科 (--subjects):")
    for s in config_loader.list_subjects():
        print(f"  - {s}")
    print("\n可用考点 / 不能手算的原因 (--reasons):")
    for key, val in config_loader.load_reasons().items():
        print(f"  - {key}\n      {val['label']}")


def _parse_csv_arg(value, full_set, name):
    """解析逗号分隔的子集参数；缺省取全集，传入则校验合法性。"""
    if not value:
        return list(full_set)
    picked = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in picked if v not in full_set]
    if unknown:
        valid = "、".join(full_set)
        raise ValueError(f"未知的 {name}：{'、'.join(unknown)}。可选值：{valid}")
    return picked


CSV_HEADER = [
    "学科", "考点", "考点说明", "序号", "是否成功",
    "答案", "单位", "尝试次数", "失败原因", "md文件",
]


def run_batch(subjects, reasons, *, count=1, mock=False, retries=2, timeout=60,
              judge_mode="warn", batch_dir=None, progress_cb=None):
    """跑一批「学科 × 考点」组合，复用 pipeline.run/save。

    被 CLI(main) 与网页(app.py) 共用，保证两边结果一致。

    参数:
        subjects   学科列表
        reasons    考点 key 列表
        count      每个「学科 × 考点」组合出几道题（默认 1）
        progress_cb(done, total, subject, reason_key, seq, result) 可选回调，
                   每跑完一道触发一次，用于命令行打印 / 网页进度条。
    返回:
        dict {batch_dir, csv_path, rows, header, success_count, total}
        rows 为与 CSV_HEADER 对齐的二维列表（不含表头）。
    """
    all_reasons = config_loader.load_reasons()
    combos = list(itertools.product(subjects, reasons))
    count = max(1, int(count))
    total = len(combos) * count

    if batch_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = os.path.join(config_loader.ensure_output_dir(), f"batch_{ts}")
    os.makedirs(batch_dir, exist_ok=True)

    rows = []
    success_count = 0
    done = 0
    for subject, reason_key in combos:
        reason_label = all_reasons.get(reason_key, {}).get("label", "")
        for seq in range(1, count + 1):
            done += 1
            result = None
            md_name = ""
            try:
                result = pipeline.run(
                    subject=subject, reason_key=reason_key,
                    max_retries=retries, timeout=timeout,
                    mock=mock, judge_mode=judge_mode, verbose=False,
                )
                try:
                    md_path = pipeline.save(result, subject, reason_key, output_dir=batch_dir)
                    md_path = _ensure_unique(md_path, seq, count)
                    md_name = os.path.basename(md_path)
                except Exception as e:  # noqa: BLE001 - 落盘失败不应中断整批
                    md_name = f"(保存失败: {e})"
                if result.success:
                    success_count += 1
            except Exception as e:  # noqa: BLE001 - 单题异常不中断整批
                if progress_cb:
                    progress_cb(done, total, subject, reason_key, seq, None)
                rows.append([subject, reason_key, reason_label, seq, "❌",
                             "", "", 0, f"异常：{e}", ""])
                continue

            rows.append(_row_from_result(
                subject, reason_key, reason_label, seq, result, md_name))
            if progress_cb:
                progress_cb(done, total, subject, reason_key, seq, result)

    csv_path = os.path.join(batch_dir, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)

    return {
        "batch_dir": batch_dir, "csv_path": csv_path,
        "rows": rows, "header": CSV_HEADER,
        "success_count": success_count, "total": total,
    }


def _ensure_unique(md_path, seq, count):
    """同一组合出多道时，pipeline.save 的秒级时间戳可能撞名；插入序号后缀避免覆盖。"""
    if count <= 1 or not os.path.exists(md_path):
        return md_path
    base, ext = os.path.splitext(md_path)
    new_path = f"{base}__{seq:02d}{ext}"
    n = seq
    while os.path.exists(new_path):
        n += 1
        new_path = f"{base}__{n:02d}{ext}"
    os.rename(md_path, new_path)
    return new_path


def _row_from_result(subject, reason_key, reason_label, seq, result, md_name):
    answer = ""
    unit = ""
    fail_reason = ""
    attempts = 0
    if result is not None:
        attempts = result.attempts
        unit = result.parsed.get("answer_unit", "")
        if result.exec_result and result.exec_result.answer is not None:
            answer = result.exec_result.answer
        if not result.success:
            fail_msgs = [f.message for f in result.findings if f.severity == "fail"]
            fail_reason = result.error or "; ".join(fail_msgs)
    return [
        subject, reason_key, reason_label, seq,
        "✅" if (result and result.success) else "❌",
        answer, unit, attempts, fail_reason, md_name,
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="批量出题：学科 × 考点矩阵一次跑完，汇总成 summary.csv。"
    )
    parser.add_argument("--subjects", help="逗号分隔的学科子集，缺省=全部。见 --list")
    parser.add_argument("--reasons", help="逗号分隔的考点子集，缺省=全部。见 --list")
    parser.add_argument("--count", type=int, default=1,
                        help="每个「学科 × 考点」组合出几道题（1-10，默认 1）")
    parser.add_argument("--mock", action="store_true",
                        help="不调用 API，用内置样例跑通整批（自测用）")
    parser.add_argument("--retries", type=int, default=2, help="单题失败时的最大重试次数")
    parser.add_argument("--timeout", type=int, default=60, help="单次代码执行超时(秒)")
    parser.add_argument("--list", action="store_true", help="列出可用学科与考点后退出")
    parser.add_argument(
        "--judge", choices=["warn", "block", "off"], default="warn",
        help="LLM 语义审核模式：warn=仅提示(默认) | block=可否决并重试 | off=关闭",
    )
    args = parser.parse_args(argv)

    if args.list:
        cmd_list()
        return 0

    all_subjects = config_loader.list_subjects()
    all_reasons = config_loader.load_reasons()  # dict: key -> {label, ...}

    try:
        subjects = _parse_csv_arg(args.subjects, all_subjects, "学科")
        reasons = _parse_csv_arg(args.reasons, list(all_reasons.keys()), "考点")
    except ValueError as e:
        print(f"\n输入错误：{e}", file=sys.stderr)
        return 2

    combos = list(itertools.product(subjects, reasons))
    if not combos:
        print("没有可跑的组合（学科或考点为空）。", file=sys.stderr)
        return 2

    count = max(1, min(10, args.count))
    total = len(combos) * count
    print(f"批量出题：{len(subjects)} 学科 × {len(reasons)} 考点 × {count} 道/组合 = {total} 道\n")

    def _progress(done, tot, subject, reason_key, seq, result):
        prefix = f"[{done}/{tot}] {subject} × {reason_key} #{seq}"
        if result is None:
            print(f"{prefix} → ❌ 异常")
        elif result.success:
            ans = result.exec_result.answer if result.exec_result else "N/A"
            print(f"{prefix} → ✅ {ans}")
        else:
            print(f"{prefix} → ❌ {result.error}")

    summary = run_batch(
        subjects, reasons, count=count,
        mock=args.mock, retries=args.retries, timeout=args.timeout,
        judge_mode=args.judge, progress_cb=_progress,
    )

    print(f"输出目录：{summary['batch_dir']}")
    fail_count = summary["total"] - summary["success_count"]
    print("\n" + "=" * 60)
    print(f"完成：共 {summary['total']} 道　✅ 成功 {summary['success_count']}　❌ 失败 {fail_count}")
    print(f"汇总表：{summary['csv_path']}")
    print("=" * 60)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
