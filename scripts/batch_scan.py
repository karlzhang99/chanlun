#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多标的批量扫描: 同一套参数跑一篮子 K 线文件, 汇总成一张表/CSV。

用法:
    python3 scripts/batch_scan.py --dir /tmp/chanlun '*.json' --history --out summary.csv
    python3 scripts/batch_scan.py a.json b.json --history
    python3 scripts/batch_scan.py --symbols sh600519,hk00700 --fetch --out summary.csv

每个标的输出: K线根数、笔/线段/中枢数量、中枢树层数、当前定位、信号类型、
以及可选的回溯统计(胜率/净收益/止损出场)。单个文件出错只记一行 error, 不中断整批。
"""
import argparse
import csv
import glob
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chanlun_analyze as C  # noqa: E402
import westock_kline_to_json as W  # noqa: E402

CSV_COLUMNS = ("target", "bars", "start", "end", "last_close", "position",
               "strokes", "segments", "centers", "tree_levels", "signals",
               "trades", "win_rate", "avg_net_return_pct", "stop_exits", "error")


def analyze_file(path, args):
    klines = C.load_klines(path, args.min_bars, True)
    result = C.analyze(klines, os.path.basename(path), None, args.center_level)
    row = {
        "target": os.path.basename(path),
        "bars": result["bars"],
        "start": result["date_range"][0],
        "end": result["date_range"][1],
        "last_close": result["last_close"],
        "position": result["position"],
        "strokes": len(result["strokes"]),
        "segments": len(result["segments"]),
        "centers": len(result["centers"]),
        "tree_levels": len(result["center_tree"]),
        "signals": "/".join(sorted({signal["type"] for signal in result["signals"]})) or "-",
        "trades": "",
        "win_rate": "",
        "avg_net_return_pct": "",
        "stop_exits": "",
        "error": "",
    }
    if args.history:
        if len(klines) <= args.history_min_bars:
            row["error"] = "K线太少, 无法回溯"
            return row
        records = C.backtest_signals(klines, args.history_horizon, args.history_stride,
                                     args.history_min_bars, args.center_level,
                                     args.history_stale_bars, args.history_fee,
                                     args.history_slippage, args.history_entry_delay)
        summary = C.summarize_backtest(records, args.history_horizon)
        row.update({
            "trades": summary["count"],
            "win_rate": summary["win_rate"],
            "avg_net_return_pct": summary["avg_net_return_pct"],
            "stop_exits": summary["stop_exits"],
        })
    return row


def fetch_symbol(symbol, args):
    """用 westock kline 取日线, 存成临时 JSON 后返回路径。"""
    process = subprocess.run(
        ["westock", "kline", symbol, "--period", "day", "--limit", str(args.fetch_limit)],
        capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "westock 调用失败")
    rows = W.parse(process.stdout)
    path = os.path.join(args.tmp_dir, "%s.json" % symbol.replace("/", "_"))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False)
    return path


def collect_targets(args):
    targets = list(args.files)
    if args.dir:
        pattern = args.pattern or "*.json"
        targets.extend(sorted(glob.glob(os.path.join(args.dir, pattern))))
    for symbol in (args.symbols or "").split(","):
        symbol = symbol.strip()
        if not symbol:
            continue
        if args.fetch:
            try:
                targets.append(fetch_symbol(symbol, args))
            except Exception as exc:  # noqa: BLE001 - 单个标的失败不影响整批
                print("跳过 %s: %s" % (symbol, exc), file=sys.stderr)
        else:
            targets.append(symbol)
    return targets


def main(argv=None):
    parser = argparse.ArgumentParser(description="多标的批量扫描")
    parser.add_argument("files", nargs="*", help="K线 JSON 文件")
    parser.add_argument("--dir", default="", help="目录")
    parser.add_argument("--pattern", default="*.json", help="目录下的文件模式, 默认 *.json")
    parser.add_argument("--symbols", default="", help="逗号分隔的代码, 配合 --fetch 取数")
    parser.add_argument("--fetch", action="store_true", help="用 westock kline 拉日线")
    parser.add_argument("--fetch-limit", type=int, default=700, help="拉取根数, 默认 700")
    parser.add_argument("--tmp-dir", default="/tmp/chanlun-batch", help="取数临时目录")
    parser.add_argument("--center-level", choices=("segment", "stroke"), default="segment")
    parser.add_argument("--min-bars", type=int, default=C.MIN_BARS)
    parser.add_argument("--history", action="store_true", help="同时做历史信号回溯")
    parser.add_argument("--history-horizon", type=int, default=20)
    parser.add_argument("--history-stride", type=int, default=10)
    parser.add_argument("--history-min-bars", type=int, default=120)
    parser.add_argument("--history-stale-bars", type=int, default=20)
    parser.add_argument("--history-fee", type=float, default=0.0005)
    parser.add_argument("--history-slippage", type=float, default=0.0005)
    parser.add_argument("--history-entry-delay", type=int, default=1)
    parser.add_argument("--out", default="", help="汇总 CSV 输出路径")
    args = parser.parse_args(argv)

    if args.fetch:
        os.makedirs(args.tmp_dir, exist_ok=True)
    targets = collect_targets(args)
    if not targets:
        print("错误: 没有可扫描的标的(--dir/--symbols/文件至少给一个)", file=sys.stderr)
        return 2

    rows = []
    for target in targets:
        try:
            rows.append(analyze_file(target, args))
        except (C.DataError, OSError, RuntimeError) as exc:
            rows.append({"target": os.path.basename(target), "error": str(exc)})
            print("跳过 %s: %s" % (target, exc), file=sys.stderr)

    header = "%-18s %5s %6s %6s %6s %5s %-22s %5s %6s %6s" % (
        "标的", "K线", "笔", "线段", "中枢", "树层", "信号", "交易", "胜率", "净收益")
    print(header)
    print("-" * len(header))
    for row in rows:
        if row.get("error"):
            print("%-18s  ! %s" % (row["target"][:18], row["error"]))
            continue
        print("%-18s %5d %6d %6d %6d %5d %-22s %5s %6s %6s" % (
            row["target"][:18], row["bars"], row["strokes"], row["segments"], row["centers"],
            row["tree_levels"], row["signals"][:22], row["trades"], row["win_rate"],
            row["avg_net_return_pct"]))

    if args.out:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(buffer.getvalue())
        print("\n汇总已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
