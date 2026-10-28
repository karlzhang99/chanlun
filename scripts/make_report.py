#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一份可直接发给人看的分析报告(HTML 自带图与表, 另出 Markdown)。

用法:
    python3 scripts/make_report.py day.json --out report.html --md report.md
    python3 scripts/make_report.py day.json --sub-level m30.json --history \\
        --center-level stroke --title "贵州茅台 日线" --out report.html

报告内容: 概览 / 结构(线段·中枢·中枢树) / 背驰 / 买卖点 / 多级别联立 /
历史回溯 / 关键价位 / 风险提示。所有数字来自 analyze() 的同一份结果, 不手改。
"""
import argparse
import os
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chanlun_analyze as C  # noqa: E402
import render_chart as R  # noqa: E402

STYLE = """
body { margin: 0 auto; max-width: 1180px; padding: 24px;
       font-family: -apple-system, "PingFang SC", sans-serif; color: #263238; background: #fff; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 28px 0 8px; border-left: 4px solid #1e88e5; padding-left: 8px; }
.meta { color: #607d8b; font-size: 13px; margin-bottom: 16px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0 4px; }
th, td { border: 1px solid #e0e0e0; padding: 6px 8px; text-align: left; }
th { background: #f5f7fa; font-weight: 600; }
.note { background: #fff8e1; border: 1px solid #ffe082; padding: 8px 12px; border-radius: 4px;
        font-size: 13px; margin: 8px 0; }
.risk li { margin: 4px 0; }
.chart { border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px; overflow: auto; }
.chart svg { max-width: 100%; height: auto; }
footer { margin-top: 32px; color: #90a4ae; font-size: 12px; border-top: 1px solid #eceff1; padding-top: 8px; }
"""


def _fmt(value):
    """报告里的空值统一显示为 —。"""
    return "—" if value is None or value == "" else value


def _table(headers, rows):
    head = "".join("<th>%s</th>" % escape(str(item)) for item in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % escape(str(cell)) for cell in row)
                   for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)


def _md_table(headers, rows):
    lines = ["| " + " | ".join(str(item) for item in headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def build(result, klines, title, svg, history=None):
    """返回 (HTML 片段列表, Markdown 片段列表)。"""
    html, md = [], []
    meta = "%s ~ %s · %d 根 K 线 · 最新收盘 %s · 中枢口径 %s" % (
        result["date_range"][0], result["date_range"][1], result["bars"],
        result["last_close"], "线段级" if result["center_level"] == "segment" else "笔级")
    html.append('<h1>%s</h1><div class="meta">%s</div>' % (escape(title), escape(meta)))
    md.append("# %s\n\n%s" % (title, meta))
    html.append("<p><b>当前定位：</b>%s</p>" % escape(result["position"]))
    md.append("\n**当前定位：** %s" % result["position"])

    html.append('<div class="chart">%s</div>' % svg)
    md.append("\n> 结构图见同目录 chart.svg（HTML 版已内嵌）。")

    html.append("<h2>结构</h2>")
    md.append("\n## 结构")
    if result["segments"]:
        rows = [(index + 1, "上涨" if seg["dir"] == "up" else "下跌", seg["start_date"], seg["end_date"],
                 "%.2f" % seg["start_price"], "%.2f" % seg["end_price"],
                 "%.2f ~ %.2f" % (seg["low"], seg["high"]), seg["stroke_count"],
                 "是" if seg.get("confirmed", True) else "否")
                for index, seg in enumerate(result["segments"])]
        headers = ("#", "方向", "起点", "终点", "起价", "终价", "区间", "笔数", "确认")
        html.append("<h3>线段</h3>" + _table(headers, rows))
        md.append("\n### 线段\n\n" + _md_table(headers, rows))
    if result["centers"]:
        rows = [("L1", "%.2f ~ %.2f" % (center["zd"], center["zg"]),
                 center["start_date"], center["end_date"], center["segments"],
                 "级别升级" if center.get("upgrade") else "")
                for center in result["centers"]]
        tree_rows = []
        for level in result.get("center_tree", []):
            for center in level["centers"]:
                tree_rows.append(("L%d" % level["level"], "%.2f ~ %.2f" % (center["zd"], center["zg"]),
                                  center["start_date"], center["end_date"],
                                  center.get("segments", ""),
                                  "级别升级" if center.get("promoted") else ""))
        headers = ("级别", "区间", "起", "止", "段数", "备注")
        html.append("<h3>中枢</h3>" + _table(headers, rows))
        html.append("<h3>中枢树</h3>" + _table(headers, tree_rows))
        md.append("\n### 中枢\n\n" + _md_table(headers, rows))
        md.append("\n### 中枢树\n\n" + _md_table(headers, tree_rows))

    divergences = [item for item in result["divergences"] if item["divergence"]]
    html.append("<h2>背驰</h2>")
    md.append("\n## 背驰")
    if divergences:
        rows = [("趋势" if item["kind"] == "trend" else "盘整", "下跌" if item["dir"] == "down" else "上涨",
                 item["prev"]["end"], item["last"]["end"],
                 "%.2f→%.2f" % (item["prev"]["amplitude"], item["last"]["amplitude"]),
                 "%.0f→%.0f" % (item["prev"]["volume"], item["last"]["volume"]),
                 "%.2f→%.2f" % (item["prev"]["macd_hist_area"], item["last"]["macd_hist_area"]))
                for item in divergences[-10:]]
        headers = ("类型", "方向", "前段", "后段", "幅度", "成交量", "MACD面积")
        html.append(_table(headers, rows))
        md.append(_md_table(headers, rows))
    else:
        html.append('<p class="note">当前没有满足"三项中两项走弱"的背驰。</p>')
        md.append("\n当前没有满足条件的背驰。")

    html.append("<h2>买卖点</h2>")
    md.append("\n## 买卖点")
    if result["signals"]:
        ordered = sorted(result["signals"], key=lambda item: C._date_only(item["date"]))
        shown = ordered[-8:]
        rows = [(signal["type"], signal["state"], signal["date"], _fmt(signal["price"]),
                 _fmt(signal["trigger"]), _fmt(signal["invalidate"]), signal["note"])
                for signal in shown]
        headers = ("类型", "状态", "日期", "价格", "触发", "失效", "说明")
        note = "共 %d 条，仅列最近 8 条；完整列表见 JSON。" % len(ordered) if len(ordered) > 8 else ""
        html.append(_table(headers, rows) + ('<p class="meta">%s</p>' % note if note else ""))
        md.append(_md_table(headers, rows) + ("\n\n" + note if note else ""))
    else:
        html.append('<p class="note">当前没有符合口径的买卖点 —— 严格口径下这是正常结果。</p>')
        md.append("\n当前没有符合口径的买卖点。")

    if result.get("multi_level"):
        sub = result["multi_level"]["sub"]
        confluence = result["multi_level"]["confluence"]
        html.append("<h2>多级别联立</h2>")
        md.append("\n## 多级别联立")
        summary = "次级别 %d 根（%s ~ %s）收盘 %s，定位：%s；结论：<b>%s</b>" % (
            sub["bars"], sub["date_range"][0], sub["date_range"][1], sub["last_close"],
            sub["position"], {"aligned": "共振", "conflict": "矛盾", "none": "无"}[confluence["state"]])
        html.append("<p>%s</p>" % summary.replace("<b>", "<b>").replace("</b>", "</b>"))
        if confluence["pairs"]:
            rows = [(pair["verdict"], pair["main"], pair["sub"] or "无", pair["note"])
                    for pair in confluence["pairs"]]
            html.append(_table(("结论", "大级别", "次级别", "说明"), rows))
            md.append(summary.replace("<b>", "**").replace("</b>", "**"))
            md.append("\n" + _md_table(("结论", "大级别", "次级别", "说明"), rows))
        else:
            md.append(summary.replace("<b>", "**").replace("</b>", "**"))

    if history:
        summary = history["summary"]
        html.append("<h2>历史信号回溯</h2>")
        md.append("\n## 历史信号回溯")
        line = ("共 %d 条（%s），胜率 %s，毛收益 %s%%、净收益 %s%%，止损 %d / 到期 %d，平均持有 %s 根" % (
            summary["count"], summary["by_type"], summary["win_rate"],
            summary["avg_return_pct"], summary["avg_net_return_pct"],
            summary["stop_exits"], summary["horizon_exits"], summary["avg_bars_held"]))
        html.append("<p>%s</p>" % escape(line))
        md.append("\n" + line)
        rows = [(record["type"], record["signal_date"], record["detected_at"],
                 "%.2f" % record["entry_price"], "%.2f" % record["exit_price"], record["exit_reason"],
                 "%s%%" % record["net_return_pct"], "%s%%" % record["max_drawdown_pct"])
                for record in history["records"][-10:]]
        headers = ("类型", "信号日", "识别日", "入场", "出场", "出场原因", "净收益", "最大回撤")
        html.append(_table(headers, rows))
        md.append(_md_table(headers, rows))

    levels = result["levels"]
    html.append("<h2>关键价位</h2>")
    md.append("\n## 关键价位")
    rows = [("支撑", "、".join("%.2f" % price for price in levels["support"])),
            ("阻力", "、".join("%.2f" % price for price in levels["resistance"])),
            ("触发", levels["trigger"] if levels["trigger"] is not None else "—"),
            ("失效", levels["invalidate"] if levels["invalidate"] is not None else "—")]
    html.append(_table(("类型", "价位"), rows))
    md.append(_md_table(("类型", "价位"), rows))

    risks = []
    if result["segments"] and not result["segments"][-1].get("confirmed", True):
        risks.append("最后一条线段未确认，可能被后续走势延伸。")
    if any(center.get("upgrade") for center in result["centers"]):
        risks.append("存在段数超过 9 的中枢（级别升级提示），本级别结构应按大级别复核。")
    if result["conflicts"]:
        risks.append("存在同日买卖点冲突（%d 处），当前处于临界状态，方向不明。"
                     % len(result["conflicts"]))
    if not result["signals"]:
        risks.append("当前没有买卖点：严格口径下没有结论是正常结果，不要为了有结论降低标准。")
    if result["center_level"] == "stroke":
        risks.append("使用笔级中枢（降级口径），噪声大于线段级，结论只作结构参考。")
    if history and history["summary"]["count"] < 10:
        risks.append("回溯样本仅 %d 条，不足以评估这套参数的表现。" % history["summary"]["count"])
    html.append("<h2>风险提示</h2><ul class=\"risk\">%s</ul>"
                % "".join("<li>%s</li>" % escape(item) for item in risks))
    md.append("\n## 风险提示\n\n" + "\n".join("- %s" % item for item in risks))
    html.append('<footer>由 chanlun skill 机械化生成：分型/笔/线段/中枢/背驰/买卖点均按固定口径计算，'
                '不构成投资建议。生成口径：%s。</footer>' % escape(str(result["method"])))
    md.append("\n---\n\n由 chanlun skill 机械化生成，不构成投资建议。")
    return html, md


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成缠论分析报告")
    parser.add_argument("klines", help="K线 JSON")
    parser.add_argument("--sub-level", default="", help="次级别 K 线 JSON")
    parser.add_argument("--center-level", choices=("segment", "stroke"), default="segment")
    parser.add_argument("--history", action="store_true", help="附历史回溯")
    parser.add_argument("--history-horizon", type=int, default=20)
    parser.add_argument("--history-stride", type=int, default=10)
    parser.add_argument("--history-min-bars", type=int, default=120)
    parser.add_argument("--history-stale-bars", type=int, default=20)
    parser.add_argument("--title", default="", help="报告标题")
    parser.add_argument("--out", default="report.html", help="HTML 输出路径")
    parser.add_argument("--md", default="", help="同时输出 Markdown 的路径")
    parser.add_argument("--svg", default="", help="同时保存独立 SVG 的路径")
    parser.add_argument("--last", type=int, default=0, help="图上只画最近 N 根")
    parser.add_argument("--min-bars", type=int, default=C.MIN_BARS)
    args = parser.parse_args(argv)

    try:
        klines = C.load_klines(args.klines, args.min_bars, False)
        sub_klines = None
        if args.sub_level:
            sub_klines = C.load_klines(args.sub_level, C.MIN_SUB_BARS, True)
    except C.DataError as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2

    chart_bars = klines[-args.last:] if args.last > 0 else klines
    title = args.title or os.path.basename(args.klines)
    result = C.analyze(chart_bars, title, sub_klines, args.center_level)
    history = None
    if args.history:
        if len(klines) <= args.history_min_bars:
            print("错误: K 线不足以回溯", file=sys.stderr)
            return 2
        records = C.backtest_signals(klines, args.history_horizon, args.history_stride,
                                     args.history_min_bars, args.center_level,
                                     args.history_stale_bars)
        history = {"records": records,
                   "summary": C.summarize_backtest(records, args.history_horizon)}
    svg = R.render(result, chart_bars, title)
    html_parts, md_parts = build(result, chart_bars, title, svg, history)
    page = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>%s</title><style>%s</style></head><body>%s</body></html>"
            % (escape(title), STYLE, "\n".join(html_parts)))
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(page)
    print("报告已写入 %s (%d 字节)" % (args.out, len(page.encode("utf-8"))))
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as handle:
            handle.write(svg)
        print("结构图已写入 %s" % args.svg)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write("\n".join(md_parts) + "\n")
        print("Markdown 已写入 %s" % args.md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
