#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把分析结果渲染成 SVG / 可交互 HTML: K线 + 笔 + 线段 + 中枢树(L1/L2+) + 买卖点 + 关键价位。

用法:
    python3 scripts/render_chart.py day.json --out chart.svg
    python3 scripts/render_chart.py day.json --html chart.html   # 图层开关 + 悬停提示
    python3 scripts/render_chart.py day.json --center-level stroke --last 250 --out chart.svg

纯标准库生成; SVG 数字与 analyze() 的 JSON 同源, 报告直接引用, 不手改。
"""
import argparse
import os
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chanlun_analyze as C  # noqa: E402

WIDTH, HEIGHT = 1280, 680
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 64, 24, 48, 96
COLORS = {
    "bg": "#ffffff", "grid": "#eceff1", "axis": "#607d8b", "text": "#37474f",
    "up": "#e53935", "down": "#1e88e5", "stroke": "#90a4ae", "segment_up": "#c62828",
    "segment_down": "#1565c0", "center1": "#1e88e5", "center2": "#ef6c00",
    "signal": "#2e7d32", "level": "#8e24aa",
}
LAYERS = (
    ("layer-levels", "关键价位"),
    ("layer-centers2", "L2+ 中枢"),
    ("layer-centers1", "L1 中枢"),
    ("layer-segments", "线段"),
    ("layer-strokes", "笔"),
    ("layer-signals", "买卖点"),
)


def _x(index, count, plot_width):
    if count <= 1:
        return MARGIN_LEFT + plot_width / 2
    return MARGIN_LEFT + index / (count - 1) * plot_width


def _y(price, low, high, plot_height):
    if high <= low:
        return MARGIN_TOP + plot_height / 2
    return MARGIN_TOP + (high - price) / (high - low) * plot_height


def _text(x, y, content, size=12, anchor="start", color=None, weight="normal", halo=False):
    outline = (' stroke="#ffffff" stroke-width="3" stroke-opacity="0.9" paint-order="stroke"'
               if halo else "")
    return ('<text x="%.1f" y="%.1f" font-size="%d" text-anchor="%s" fill="%s" '
            'font-weight="%s" font-family="-apple-system, PingFang SC, sans-serif"%s>%s</text>'
            % (x, y, size, anchor, color or COLORS["text"], weight, outline, escape(str(content))))


def _max_signals(result, limit=8):
    return min(limit, len(result["signals"]))


def render(result, bars, title=""):
    """生成 SVG 字符串; 各图层用 <g id="layer-*"> 包住, 便于 HTML 端开关。"""
    plot_width = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    plot_height = HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    dates = [bar["date"] for bar in bars]
    index_of = {date: i for i, date in enumerate(dates)}
    count = len(bars)
    low = min(bar["low"] for bar in bars)
    high = max(bar["high"] for bar in bars)
    pad = (high - low) * 0.04 or 1
    low, high = low - pad, high + pad

    layers = {name: [] for name, _ in LAYERS}
    base = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (WIDTH, HEIGHT, WIDTH, HEIGHT),
        '<rect width="100%%" height="100%%" fill="%s"/>' % COLORS["bg"],
        _text(MARGIN_LEFT, 28, title or result.get("name") or "缠论结构图", 18, weight="bold"),
        _text(WIDTH - MARGIN_RIGHT, 28, "口径: %s / %s" % (
            result["center_level"], "线段" if result["center_level"] == "segment" else "笔"),
            12, "end", COLORS["axis"]),
    ]

    for step in range(5):
        price = low + (high - low) * step / 4
        y = _y(price, low, high, plot_height)
        base.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
                    % (MARGIN_LEFT, y, WIDTH - MARGIN_RIGHT, y, COLORS["grid"]))
        base.append(_text(MARGIN_LEFT - 8, y + 4, "%.2f" % price, 11, "end", COLORS["axis"]))

    candle_width = max(1.0, plot_width / count * 0.65)
    for i, bar in enumerate(bars):
        x = _x(i, count, plot_width)
        color = COLORS["up"] if bar["close"] >= bar["open"] else COLORS["down"]
        y_high = _y(bar["high"], low, high, plot_height)
        y_low = _y(bar["low"], low, high, plot_height)
        y_open = _y(bar["open"], low, high, plot_height)
        y_close = _y(bar["close"], low, high, plot_height)
        base.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>'
                    % (x, y_high, x, y_low, color))
        top, bottom = min(y_open, y_close), max(y_open, y_close)
        base.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s">'
                    '<title>%s O=%.2f H=%.2f L=%.2f C=%.2f</title></rect>'
                    % (x - candle_width / 2, top, candle_width, max(1.0, bottom - top), color,
                       escape(str(bar["date"])), bar["open"], bar["high"], bar["low"], bar["close"]))

    for stroke in result["strokes"]:
        start = index_of.get(stroke["start_date"])
        end = index_of.get(stroke["end_date"])
        if start is None or end is None:
            continue
        layers["layer-strokes"].append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1" opacity="0.55">'
            '<title>笔 %s %.2f → %s %.2f</title></line>'
            % (_x(start, count, plot_width), _y(stroke["start_price"], low, high, plot_height),
               _x(end, count, plot_width), _y(stroke["end_price"], low, high, plot_height),
               COLORS["stroke"], escape(stroke["start_date"]), stroke["start_price"],
               escape(stroke["end_date"]), stroke["end_price"]))

    for segment in result["segments"]:
        start = index_of.get(segment["start_date"])
        end = index_of.get(segment["end_date"])
        if start is None or end is None:
            continue
        color = COLORS["segment_up"] if segment["dir"] == "up" else COLORS["segment_down"]
        width = 2.5 if segment.get("confirmed") else 1.5
        layers["layer-segments"].append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f" %s>'
            '<title>线段 %s %s(%.2f) → %s(%.2f) [%.2f, %.2f]%s</title></line>'
            % (_x(start, count, plot_width), _y(segment["start_price"], low, high, plot_height),
               _x(end, count, plot_width), _y(segment["end_price"], low, high, plot_height),
               color, width, "" if segment.get("confirmed") else 'stroke-dasharray="5 4"',
               "上涨" if segment["dir"] == "up" else "下跌", escape(segment["start_date"]),
               segment["start_price"], escape(segment["end_date"]), segment["end_price"],
               segment["low"], segment["high"], "" if segment.get("confirmed") else " 未确认"))

    for level in result.get("center_tree", []):
        rank = level["level"]
        target = "layer-centers1" if rank == 1 else "layer-centers2"
        for center in level["centers"]:
            start = index_of.get(center.get("start_date"))
            end = index_of.get(center.get("end_date"))
            if start is None or end is None:
                continue
            x1, x2 = _x(start, count, plot_width), _x(end, count, plot_width)
            y1, y2 = _y(center["zg"], low, high, plot_height), _y(center["zd"], low, high, plot_height)
            color = COLORS["center1"] if rank == 1 else COLORS["center2"]
            tooltip = "L%d 中枢 %s ~ %s ZD=%.2f ZG=%.2f%s" % (
                rank, center.get("start_date"), center.get("end_date"), center["zd"], center["zg"],
                " (级别升级)" if center.get("promoted") else "")
            layers[target].append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" fill-opacity="%.2f" '
                'stroke="%s" stroke-width="%d" stroke-dasharray="%s" data-tip="%s">'
                '<title>%s</title></rect>'
                % (x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1), color, 0.10 if rank == 1 else 0.14,
                   color, 1 if rank == 1 else 2, "3 3" if rank == 1 else "6 3",
                   escape(tooltip), escape(tooltip)))
            label = "L%d ZD=%.2f ZG=%.2f" % (rank, center["zd"], center["zg"])
            if center.get("promoted"):
                label += " (升级)"
            layers[target].append(_text(x1 + 4, y1 + 12, label, 10, "start", color, "bold", halo=True))

    for signal in result["signals"][-_max_signals(result):]:
        start = index_of.get(C._date_only(signal["date"]))
        if start is None:
            continue
        price = signal.get("price") or result["last_close"]
        x = _x(start, count, plot_width)
        y = _y(price, low, high, plot_height)
        is_buy = signal["type"].startswith("buy")
        direction = -1 if is_buy else 1
        points = "%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (
            x, y + direction * -14 - (2 if is_buy else -2),
            x - 6, y + direction * -2, x + 6, y + direction * -2)
        tooltip = "%s(%s) %s 价格=%s 触发=%s 失效=%s %s" % (
            signal["type"], signal["state"], signal["date"], signal["price"],
            signal["trigger"], signal["invalidate"], signal["note"])
        layers["layer-signals"].append(
            '<polygon points="%s" fill="%s" opacity="0.85" data-tip="%s"><title>%s</title></polygon>'
            % (points, COLORS["signal"] if is_buy else COLORS["up"],
               escape(tooltip), escape(tooltip)))
        label = "%s(%s)" % (signal["type"], signal["state"])
        anchor = "end" if x > WIDTH - MARGIN_RIGHT - 110 else "middle"
        text_x = min(x, WIDTH - MARGIN_RIGHT - 4) if anchor == "end" else x
        layers["layer-signals"].append(
            _text(text_x, y + (22 if is_buy else -18), label, 10, anchor, COLORS["signal"], halo=True))

    for price in result["levels"]["support"][:2]:
        y = _y(price, low, high, plot_height)
        layers["layer-levels"].append(
            '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1" '
            'stroke-dasharray="2 4" opacity="0.7"/>'
            % (MARGIN_LEFT, y, WIDTH - MARGIN_RIGHT, y, COLORS["level"]))
        layers["layer-levels"].append(_text(WIDTH - MARGIN_RIGHT - 4, y - 4, "支撑 %.2f" % price,
                                            10, "end", COLORS["level"], halo=True))
    for price in result["levels"]["resistance"][:2]:
        y = _y(price, low, high, plot_height)
        layers["layer-levels"].append(
            '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1" '
            'stroke-dasharray="2 4" opacity="0.7"/>'
            % (MARGIN_LEFT, y, WIDTH - MARGIN_RIGHT, y, COLORS["level"]))
        layers["layer-levels"].append(_text(WIDTH - MARGIN_RIGHT - 4, y - 4, "阻力 %.2f" % price,
                                            10, "end", COLORS["level"], halo=True))

    for step in range(6):
        i = round((count - 1) * step / 5)
        x = _x(i, count, plot_width)
        label = dates[i].split(" ")[0]
        anchor = "start" if step == 0 else ("end" if step == 5 else "middle")
        base.append(_text(x, HEIGHT - MARGIN_BOTTOM + 20, label, 10, anchor, COLORS["axis"]))
        base.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1"/>'
                    % (x, MARGIN_TOP, x, HEIGHT - MARGIN_BOTTOM, COLORS["grid"]))

    legend_x = MARGIN_LEFT
    for label, color in (("线段", COLORS["segment_up"]), ("笔", COLORS["stroke"]),
                         ("L1 中枢", COLORS["center1"]), ("L2+ 中枢", COLORS["center2"]),
                         ("买卖点", COLORS["signal"]), ("关键价位", COLORS["level"])):
        base.append('<rect x="%d" y="%d" width="10" height="10" fill="%s"/>' % (legend_x, HEIGHT - 24, color))
        base.append(_text(legend_x + 14, HEIGHT - 15, label, 11, "start", COLORS["text"]))
        legend_x += 14 + len(label) * 7 + 18

    base.append(_text(MARGIN_LEFT, HEIGHT - 46, "当前定位: %s" % result["position"][:96],
                      11, "start", COLORS["axis"]))

    body = []
    for name, _ in LAYERS:
        body.append('<g id="%s">' % name)
        body.extend(layers[name])
        body.append("</g>")
    return "\n".join(base + body + ["</svg>"])


def render_html(svg, title):
    """把 SVG 包成单文件 HTML: 图层开关 + 悬停浮窗。"""
    controls = "\n".join(
        '<label><input type="checkbox" checked onchange="toggleLayer(\'%s\', this.checked)"> %s</label>'
        % (name, label) for name, label in LAYERS)
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>%(title)s</title>
<style>
  body { margin: 0; padding: 16px; font-family: -apple-system, "PingFang SC", sans-serif;
         background: #fafafa; color: #37474f; }
  h1 { font-size: 18px; margin: 0 0 8px; }
  .controls { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
  .controls label { cursor: pointer; user-select: none; }
  #chart { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; overflow: auto; }
  #tip { position: fixed; pointer-events: none; background: rgba(33,33,33,.92); color: #fff;
         font-size: 12px; line-height: 1.5; padding: 6px 8px; border-radius: 4px; max-width: 420px;
         display: none; z-index: 10; }
  #chart svg { max-width: 100%%; height: auto; display: block; }
</style></head>
<body>
<h1>%(title)s</h1>
<div class="controls">%(controls)s</div>
<div id="chart">%(svg)s</div>
<div id="tip"></div>
<script>
function toggleLayer(id, on) {
  var node = document.getElementById(id);
  if (node) node.style.display = on ? "" : "none";
}
var tip = document.getElementById('tip');
var chart = document.getElementById('chart');
chart.addEventListener('mousemove', function (event) {
  var target = event.target.closest('[data-tip]');
  if (!target) { tip.style.display = 'none'; return; }
  tip.textContent = target.getAttribute('data-tip');
  tip.style.display = 'block';
  var x = event.clientX + 14, y = event.clientY + 14;
  if (x + tip.offsetWidth > window.innerWidth - 8) x = event.clientX - tip.offsetWidth - 14;
  if (y + tip.offsetHeight > window.innerHeight - 8) y = event.clientY - tip.offsetHeight - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
});
chart.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
</script>
</body></html>
""" % {"title": escape(title), "controls": controls, "svg": svg}


def main(argv=None):
    parser = argparse.ArgumentParser(description="渲染缠论结构 SVG / HTML")
    parser.add_argument("klines", help="K线 JSON")
    parser.add_argument("--out", default="", help="SVG 输出路径")
    parser.add_argument("--html", default="", help="可交互 HTML 输出路径")
    parser.add_argument("--title", default="", help="标题")
    parser.add_argument("--center-level", choices=("segment", "stroke"), default="segment")
    parser.add_argument("--last", type=int, default=0, help="只画最近 N 根, 0=全部")
    parser.add_argument("--min-bars", type=int, default=C.MIN_BARS)
    args = parser.parse_args(argv)

    try:
        klines = C.load_klines(args.klines, args.min_bars, False)
    except C.DataError as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2
    if args.last > 0:
        klines = klines[-args.last:]
    title = args.title or os.path.basename(args.klines)
    result = C.analyze(klines, title, None, args.center_level)
    svg = render(result, klines, title)
    if not args.out and not args.html:
        args.out = "chart.svg"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(svg)
        print("已写入 %s (%d 字节)" % (args.out, len(svg.encode("utf-8"))))
    if args.html:
        page = render_html(svg, title)
        with open(args.html, "w", encoding="utf-8") as handle:
            handle.write(page)
        print("已写入 %s (%d 字节)" % (args.html, len(page.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
