#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数敏感性扫描(支持多标的): 对 entry-delay / horizon / stale-bars / 中枢层级 做网格。

用法:
    python3 scripts/param_sweep.py day.json --out sweep.csv
    python3 scripts/param_sweep.py --dir /tmp/chanlun '*.json' --center-levels segment,stroke \
        --entry-delays 1,2 --horizons 10,20,30 --stale-bars 10,20,40 --stride 20

多标的时输出两条口径:
  - 汇总矩阵(--out): 每个参数组合跨标的的均值/中位数/几个标的为正;
  - 明细(--detail-out): 每个标的 × 每个参数组合一行。
参数排序按"为正的标的数 → 平均净收益", 用来看参数是否稳定, 而不是挑单标的最优。
"""
import argparse
import csv
import glob
import io
import itertools
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chanlun_analyze as C  # noqa: E402

SUMMARY_COLUMNS = ("center_level", "entry_delay", "horizon", "stale_bars", "stride",
                   "symbols", "trades_total", "mean_net_return_pct", "median_net_return_pct",
                   "positive_symbols", "mean_win_rate")
DETAIL_COLUMNS = ("target",) + SUMMARY_COLUMNS[:-1] + ("trades", "win_rate", "avg_return_pct",
                                                      "avg_net_return_pct", "stop_exits")


def parse_ints(text):
    return [int(part) for part in text.split(",") if part.strip()]


def collect_files(args):
    targets = list(args.klines)
    if args.dir:
        targets.extend(sorted(glob.glob(os.path.join(args.dir, args.pattern or "*.json"))))
    return targets


def sweep_one(path, klines, combo, args):
    level, delay, horizon, stale = combo
    records = C.backtest_signals(klines, horizon, args.stride, args.min_bars, level,
                                 stale, args.fee, args.slippage, delay)
    summary = C.summarize_backtest(records, horizon)
    return {
        "target": os.path.basename(path),
        "center_level": level, "entry_delay": delay, "horizon": horizon,
        "stale_bars": stale, "stride": args.stride,
        "trades": summary["count"], "win_rate": summary["win_rate"],
        "avg_return_pct": summary["avg_return_pct"],
        "avg_net_return_pct": summary["avg_net_return_pct"],
        "stop_exits": summary["stop_exits"],
    }


def aggregate(details, combos):
    rows = []
    for combo in combos:
        level, delay, horizon, stale = combo
        group = [row for row in details
                 if (row["center_level"], row["entry_delay"], row["horizon"], row["stale_bars"]) == combo]
        nets = [row["avg_net_return_pct"] for row in group if row["avg_net_return_pct"] is not None]
        wins = [row["win_rate"] for row in group if row["win_rate"] is not None]
        rows.append({
            "center_level": level, "entry_delay": delay, "horizon": horizon,
            "stale_bars": stale, "stride": group[0]["stride"] if group else 0,
            "symbols": len(group), "trades_total": sum(row["trades"] for row in group),
            "mean_net_return_pct": round(statistics.fmean(nets), 2) if nets else None,
            "median_net_return_pct": round(statistics.median(nets), 2) if nets else None,
            "positive_symbols": sum(1 for value in nets if value > 0),
            "mean_win_rate": round(statistics.fmean(wins), 3) if wins else None,
        })
    rows.sort(key=lambda row: (-row["positive_symbols"],
                               row["mean_net_return_pct"] is None,
                               -(row["mean_net_return_pct"] or 0)))
    return rows


def write_csv(path, columns, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(buffer.getvalue())


def main(argv=None):
    parser = argparse.ArgumentParser(description="回溯参数敏感性扫描(多标的)")
    parser.add_argument("klines", nargs="*", help="K线 JSON 文件(可多个)")
    parser.add_argument("--dir", default="", help="目录")
    parser.add_argument("--pattern", default="*.json", help="目录文件模式, 默认 *.json")
    parser.add_argument("--center-levels", default="segment", help="逗号分隔: segment,stroke")
    parser.add_argument("--entry-delays", default="1", help="逗号分隔, 默认 1")
    parser.add_argument("--horizons", default="10,20,30", help="逗号分隔, 默认 10,20,30")
    parser.add_argument("--stale-bars", default="10,20,40", help="逗号分隔, 默认 10,20,40")
    parser.add_argument("--stride", type=int, default=10, help="滑动步长, 默认 10")
    parser.add_argument("--min-bars", type=int, default=C.MIN_BARS)
    parser.add_argument("--fee", type=float, default=0.0005)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out", default="", help="汇总矩阵 CSV")
    parser.add_argument("--detail-out", default="", help="明细 CSV(标的 × 参数)")
    args = parser.parse_args(argv)

    levels = [part.strip() for part in args.center_levels.split(",") if part.strip()]
    delays = parse_ints(args.entry_delays)
    horizons = parse_ints(args.horizons)
    stales = parse_ints(args.stale_bars)
    for name, values in (("--entry-delays", delays), ("--horizons", horizons),
                         ("--stale-bars", stales)):
        if not values or any(value < 0 for value in values):
            parser.error("%s 需要非负整数列表" % name)
    if args.stride < 1:
        parser.error("--stride 必须为正")

    targets = collect_files(args)
    if not targets:
        print("错误: 没有可扫描的标的", file=sys.stderr)
        return 2

    series = {}
    for path in targets:
        try:
            series[path] = C.load_klines(path, args.min_bars, False)
        except C.DataError as exc:
            print("跳过 %s: %s" % (path, exc), file=sys.stderr)
    if not series:
        print("错误: 没有可用标的", file=sys.stderr)
        return 2

    combos = list(itertools.product(levels, delays, horizons, stales))
    details, done = [], 0
    for path, klines in series.items():
        for combo in combos:
            details.append(sweep_one(path, klines, combo, args))
            done += 1
            print("\r进度 %d/%d" % (done, len(combos) * len(series)), end="", file=sys.stderr)
    print("", file=sys.stderr)

    summary = aggregate(details, combos)
    print("%-8s %5s %7s %5s %6s %6s %8s %8s" % (
        "中枢", "延迟", "horizon", "stale", "标的", "交易", "正收益数", "均值净收益"))
    for row in summary[:args.top]:
        print("%-8s %5d %7d %5d %6d %6d %8d %8s" % (
            row["center_level"], row["entry_delay"], row["horizon"], row["stale_bars"],
            row["symbols"], row["trades_total"], row["positive_symbols"],
            row["mean_net_return_pct"]))
    if len(summary) > args.top:
        print("... 共 %d 个参数组合, 完整结果见 --out" % len(summary))

    if args.out:
        write_csv(args.out, SUMMARY_COLUMNS, summary)
        print("汇总矩阵已写入 %s" % args.out)
    if args.detail_out:
        write_csv(args.detail_out, DETAIL_COLUMNS, details)
        print("明细已写入 %s" % args.detail_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
