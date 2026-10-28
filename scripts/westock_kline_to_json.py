#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `westock kline` 的 Markdown 表格输出转成 chanlun_analyze.py 要的 JSON。

用法:
    westock kline sh600519 --period m30 --start 2026-09-27 --end 2026-10-27 > /tmp/m30.md
    python3 scripts/westock_kline_to_json.py /tmp/m30.md > /tmp/m30.json
    python3 scripts/chanlun_analyze.py day.json --sub-level /tmp/m30.json

读取 stdin 时用 `-` 作为文件名。输出按日期升序, 字段为 date/open/high/low/close/volume,
其中 westock 的 `last` 列按收盘价处理。
"""
import json
import sys

COLUMN_ALIASES = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "last": "close",
    "close": "close",
    "volume": "volume",
}


def parse(markdown):
    """解析 Markdown 表格, 返回按日期升序的 K 线列表。"""
    header, rows = None, []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header is None:
            keys = [COLUMN_ALIASES.get(cell.lower()) for cell in cells]
            if "date" in keys and "open" in keys and "close" in keys:
                header = keys
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        row = {}
        for key, cell in zip(header, cells):
            if key is None:
                continue
            if key == "date":
                row["date"] = cell
            else:
                value = cell.replace(",", "")
                try:
                    row[key] = float(value)
                except ValueError:
                    raise SystemExit("无法解析数值: %r" % cell)
        if len(row) >= 6:
            rows.append({key: row[key] for key in ("date", "open", "high", "low", "close", "volume")})
    if not rows:
        raise SystemExit("没有在输入里找到 westock 的 K 线表格(需要含 date/open/high/low/last 列)")
    rows.sort(key=lambda item: item["date"])
    return rows


def main(argv):
    path = argv[1] if len(argv) > 1 else "-"
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    json.dump(parse(text), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
