#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠中说禅(缠论)机械化分析脚本
================================
输入: 主级别 K线 JSON; 可选次级别 K线 JSON(--sub-level), 用于三买/三卖/二买的区间套确认。
输出: 结构化 JSON(分型/笔/线段/线段级中枢/背驰/买卖点状态机/关键价位), 供报告与图表使用。

口径(按缠论原文, 每步都标注对应的课):
  1. K线包含处理      —— 第65/67课: 向上取"高高", 向下取"低低"
  2. 分型识别         —— 第54课: 顶分型(中间K线高低点均为三根最高), 底分型反之;
                          分型日期取极值所在原始K线的日期
  3. 笔的识别         —— 第77/78课新笔定义: 顶底分型交替, 中间至少1根独立K线,
                          创新高/新低; 终点分型须被后续反向分型确认(滞后确认)
  4. 线段             —— 第69-76课: 特征序列(反向笔)做包含处理后取顶/底分型,
                          有缺口时要求后续元素突破确认; 线段至少三笔
  5. 中枢             —— 第18-20课: 中枢由**连续三线段**重叠构成, ZD=max(三段低点),
                          ZG=min(三段高点); 后续线段与[ZD,ZG]重叠视为**延伸**,
                          区间不变、只记段数; 完全不重叠才是新中枢(新生);
                          超过 9 段提示级别升级(扩展) —— 本版不建中枢树
  6. 背驰             —— 第15/24课: 区分**趋势背驰**(围绕中枢的进入段与离开段同向比较)
                          与**盘整背驰**(中枢内部同向段比较); 价/量/MACD柱面积三项中
                          至少两项走弱; 只有趋势背驰构成一买/一卖
  7. 三类买卖点       —— 第21-27课: 状态机 + 互斥。三买/三卖要求"离开中枢后回抽不进入",
                          有次级别数据时用区间套确认, 否则标记为 candidate;
                          二买/二卖要求"一买后第一次次级别回调不破前低"
  8. 关键价位         —— 支撑/阻力/触发/失效, 直接供报告引用

用法:
  python3 chanlun_analyze.py klines.json [--name 名称] [--out result.json]
  python3 chanlun_analyze.py day.json --sub-level m30.json   # 区间套确认
  python3 chanlun_analyze.py klines.json --last 3            # 摘要只看最近 3 条
  python3 chanlun_analyze.py klines.json --allow-short       # 少于 60 根也分析(仅供参考)

输入: JSON 数组, 每项必填 date/open/high/low/close, volume 可缺(仅警告)。
错误以可读文本写到 stderr, 退出码 2; 正常退出码 0。
"""
import csv
import io
import json
import argparse
import sys
from collections import Counter


# ---------------------------------------------------------------- 基础工具
class DataError(Exception):
    """输入数据不可用(文件缺失/格式错误/字段缺失/样本不足), 由 main 转成可读错误。"""


REQUIRED_FIELDS = ("date", "open", "high", "low", "close")
MIN_BARS = 60
MIN_SUB_BARS = 40
LEVEL_UPGRADE_SEGMENTS = 9


def _warn(message):
    print("警告: %s" % message, file=sys.stderr)


def load_klines(path, min_bars=MIN_BARS, allow_short=False):
    """读取并校验 K 线 JSON, 返回按日期升序的列表。

    校验: 顶层数组、必填字段、OHLC 数值关系、日期唯一、样本根数。
    缺 volume 只警告(背驰的量能比较会退化), OHLC 与开收不一致也只警告。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise DataError("找不到文件 %s" % path)
    except IsADirectoryError:
        raise DataError("%s 是目录, 需要 K 线 JSON 文件" % path)
    except UnicodeDecodeError:
        raise DataError("%s 不是 UTF-8 文本(可另存为 UTF-8 后重试)" % path)
    except json.JSONDecodeError as exc:
        raise DataError("%s 不是合法 JSON: %s" % (path, exc))

    if not isinstance(data, list):
        raise DataError("顶层必须是数组: [{date,open,high,low,close,volume}, ...]")
    if not data:
        raise DataError("K 线数组为空")

    rows, no_volume, inconsistent = [], 0, 0
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise DataError("第 %d 项不是对象" % index)
        missing = [key for key in REQUIRED_FIELDS if key not in item]
        if missing:
            raise DataError("第 %d 项缺少字段: %s" % (index, ", ".join(missing)))
        try:
            ohlc = {key: float(item[key]) for key in ("open", "high", "low", "close")}
        except (TypeError, ValueError):
            raise DataError("第 %d 项的 OHLC 含非数字值" % index)
        if ohlc["high"] < ohlc["low"]:
            raise DataError("第 %d 项 high < low" % index)
        if ohlc["high"] < max(ohlc["open"], ohlc["close"]) or ohlc["low"] > min(ohlc["open"], ohlc["close"]):
            inconsistent += 1
        volume = item.get("volume")
        if volume is None:
            no_volume += 1
            volume = 0
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            raise DataError("第 %d 项的 volume 不是数字" % index)
        rows.append({"date": str(item["date"]), **ohlc, "volume": volume})

    rows.sort(key=lambda k: k["date"])
    duplicates = [date for date, count in Counter(k["date"] for k in rows).items() if count > 1]
    if duplicates:
        shown = ", ".join(duplicates[:5]) + (" 等 %d 个" % len(duplicates) if len(duplicates) > 5 else "")
        raise DataError("存在重复日期: %s" % shown)
    if no_volume == len(rows):
        _warn("全部 K 线缺少 volume, 背驰的量能比较恒为「不弱」, 结论会偏乐观")
    elif no_volume:
        _warn("%d/%d 根 K 线缺少 volume, 已按 0 计, 背驰量能比较会失真" % (no_volume, len(rows)))
    if inconsistent:
        _warn("%d 根 K 线的 high/low 与 open/close 不自洽, 请确认数据源口径" % inconsistent)
    if len(rows) < min_bars and not allow_short:
        raise DataError(
            "K 线仅 %d 根, 少于 %d 根: 笔/线段/中枢会严重不足(MACD 预热需 35 根)。"
            "确认数据无误可加 --allow-short, 或降低 --min-bars" % (len(rows), min_bars))
    if len(rows) < min_bars:
        _warn("K 线仅 %d 根(<%d), 结果仅供参考" % (len(rows), min_bars))
    return rows


def process_include(klines):
    """处理K线包含关系(第65/67课)。

    包含的严格定义: 一根K线的高低点完全落在另一根的区间内(方向无关)。
    向上合并取"高高", 向下合并取"低低"; 合并后的K线保留区间首根日期,
    但记录 src_start/src_end, 供分型日期与量能定位回到原始K线。
    """
    if len(klines) < 2:
        return [dict(k, src_start=i, src_end=i) for i, k in enumerate(klines)]
    merged = [dict(klines[0], src_start=0, src_end=0),
              dict(klines[1], src_start=1, src_end=1)]
    direction = "up" if merged[1]["high"] >= merged[0]["high"] else "down"
    for i, k in enumerate(klines[2:], start=2):
        last = merged[-1]
        has_inc = ((k["high"] <= last["high"] and k["low"] >= last["low"])
                   or (k["high"] >= last["high"] and k["low"] <= last["low"]))
        if has_inc:
            if direction == "up":
                nh, nl = max(k["high"], last["high"]), max(k["low"], last["low"])
                src = k if k["high"] == nh else last
            else:
                nh, nl = min(k["high"], last["high"]), min(k["low"], last["low"])
                src = k if k["low"] == nl else last
            merged[-1] = dict(last, high=nh, low=nl,
                              open=src["open"], close=src["close"],
                              src_end=i)
        else:
            merged.append(dict(k, src_start=i, src_end=i))
            direction = "up" if k["high"] > last["high"] else "down"
    return merged


def _extreme_date(raw, candle, kind):
    """合并K线的极值来自哪根原始K线, 就用那根的日期。"""
    start = candle.get("src_start", 0)
    end = candle.get("src_end", start)
    segment = raw[start:end + 1]
    if not segment:
        return candle["date"]
    bar = max(segment, key=lambda k: k["high"]) if kind == "top" else min(segment, key=lambda k: k["low"])
    return bar["date"]


def find_fractals(kl, raw):
    """顶/底分型(第54课严格定义), 日期取合并区间内极值所在原始K线。"""
    fx = []
    for i in range(1, len(kl) - 1):
        a, b, c = kl[i - 1], kl[i], kl[i + 1]
        if a["high"] == b["high"] == c["high"] and a["low"] == b["low"] == c["low"]:
            continue
        if (b["high"] >= a["high"] and b["high"] >= c["high"]
                and b["low"] >= a["low"] and b["low"] >= c["low"]):
            fx.append({"type": "top", "i": i, "date": _extreme_date(raw, b, "top"),
                       "high": b["high"], "low": b["low"]})
        if (b["low"] <= a["low"] and b["low"] <= c["low"]
                and b["high"] <= a["high"] and b["high"] <= c["high"]):
            fx.append({"type": "bottom", "i": i, "date": _extreme_date(raw, b, "bottom"),
                       "high": b["high"], "low": b["low"]})
    return fx


def _seed_initial_fractal(fx, merged):
    """补一个起始分型, 找回被丢掉的第一段行情。

    分型需要左右各一根K线, 序列开头那一段永远成不了分型, 于是第一笔经常整段丢失
    (实测干净锯齿只剩后面两笔)。当第一个分型是顶分型时, 在它之前的最低点补一个
    底分型(反之补顶分型), 让笔从行情起点开始。
    """
    if len(fx) < 2:
        return fx
    first = fx[0]
    before = merged[:first["i"]]
    if not before:
        return fx
    if first["type"] == "top":
        idx = min(range(len(before)), key=lambda k: before[k]["low"])
        bar = before[idx]
        if bar["low"] >= first["low"]:
            return fx
        seed = {"type": "bottom", "i": idx, "date": bar["date"],
                "high": bar["high"], "low": bar["low"]}
    else:
        idx = max(range(len(before)), key=lambda k: before[k]["high"])
        bar = before[idx]
        if bar["high"] <= first["high"]:
            return fx
        seed = {"type": "top", "i": idx, "date": bar["date"],
                "high": bar["high"], "low": bar["low"]}
    return [seed] + fx


def build_strokes(fx):
    """笔的识别(第77/78课新笔定义 + 滞后确认)。

    规则:
      a) 顶/底分型交替, 两个端点分型之间(不含端点自身)至少1根独立K线(j-i>=3);
      b) 向上笔要求顶分型高点 > 底分型高点, 向下笔反之;
      c) 终点分型采用"滞后确认": 异类型分型满足条件时先作候选(pending),
         此后同类型分型继续延伸取极值; 只有当与起点同类型的反向分型出现、
         且该反向分型自身能构成一笔(创新于候选端点)时, 候选才被确认为笔终点;
      d) 确认失败时, 该反向分型若满足条件, 升级为新的候选终点;
      e) 起点分型被更极端的同类型分型延伸时, 早于新起点的候选终点作废。
    返回 (strokes, pending) —— pending 为最后一笔未确认的候选终点(可为None)。
    """
    strokes = []
    if len(fx) < 2:
        return strokes, None
    A = fx[0]
    direction = "up" if A["type"] == "bottom" else "down"
    pending = None
    for f in fx[1:]:
        if pending is not None and f["type"] == pending["type"]:
            if ((pending["type"] == "top" and f["high"] > pending["high"])
                    or (pending["type"] == "bottom" and f["low"] < pending["low"])):
                pending = f
            continue
        if f["type"] == A["type"]:
            if pending is not None:
                ok = ((pending["type"] == "top" and f["low"] < pending["high"])
                      or (pending["type"] == "bottom" and f["high"] > pending["low"]))
                if ok and f["i"] - pending["i"] >= 3:
                    strokes.append({"dir": direction, "start": A, "end": pending})
                    A = pending
                    direction = "down" if direction == "up" else "up"
                    pending = None
                    if f["i"] - A["i"] >= 3:
                        if A["type"] == "bottom" and f["high"] > A["high"]:
                            pending = f
                        elif A["type"] == "top" and f["low"] < A["low"]:
                            pending = f
                else:
                    if f["i"] - A["i"] >= 3:
                        if A["type"] == "bottom" and f["high"] > A["high"]:
                            pending = f
                        elif A["type"] == "top" and f["low"] < A["low"]:
                            pending = f
            else:
                if ((A["type"] == "top" and f["high"] > A["high"])
                        or (A["type"] == "bottom" and f["low"] < A["low"])):
                    A = f
            continue
        if f["i"] - A["i"] < 3:
            continue
        if direction == "up" and f["high"] > A["high"]:
            pending = f
        elif direction == "down" and f["low"] < A["low"]:
            pending = f
    return strokes, pending


# ---------------------------------------------------------------- 线段
def _stroke_range(stroke):
    if stroke["dir"] == "up":
        return (stroke["start"]["low"], stroke["end"]["high"])
    return (stroke["end"]["low"], stroke["start"]["high"])


def _contains(a, b):
    return ((a["high"] >= b["high"] and a["low"] <= b["low"])
            or (b["high"] >= a["high"] and b["low"] <= a["low"]))


def _make_segment(strokes, start, end):
    highs = [max(s["start"]["high"], s["end"]["high"]) for s in strokes[start:end + 1]]
    lows = [min(s["start"]["low"], s["end"]["low"]) for s in strokes[start:end + 1]]
    high, low = max(highs), min(lows)
    first, last = strokes[start], strokes[end]
    return {
        "dir": first["dir"],
        "start_stroke": start, "end_stroke": end,
        "start_date": first["start"]["date"], "end_date": last["end"]["date"],
        "start_price": first["start"]["low"] if first["dir"] == "up" else first["start"]["high"],
        "end_price": last["end"]["high"] if first["dir"] == "up" else last["end"]["low"],
        "high": high, "low": low,
        "strokes": end - start + 1,
    }


def build_segments(strokes, min_strokes=3, max_segments=200):
    """线段划分(第69-76课, 特征序列分型 + 缺口确认的简化实现)。

    向上线段的特征序列取所有向下笔, 向下线段取所有向上笔; 特征序列先做包含处理
    (方向与线段一致), 再找顶/底分型:
      - 向上线段出现顶分型 / 向下线段出现底分型, 且分型满足缺口确认时, 线段在该
        分型中间元素之前的笔结束;
      - 有缺口(分型前后两个元素区间不重叠)时, 要求后一个元素跌破/升破前一个元素的
        极值才确认, 否则该分型无效、线段继续延伸。
    最后一段若未出现分型, 按"未确认线段"延伸到最后一笔, 并在输出里标记 confirmed=False。
    """
    segments = []
    i, n = 0, len(strokes)
    while i + min_strokes - 1 < n and len(segments) < max_segments:
        direction = strokes[i]["dir"]
        merged, end_stroke = [], None
        j = i + 1
        while j < n:
            s = strokes[j]
            if s["dir"] != direction:
                lo, hi = _stroke_range(s)
                elem = {"high": hi, "low": lo, "j": j}
                if merged and _contains(merged[-1], elem):
                    last = merged[-1]
                    if direction == "up":
                        merged[-1] = {"high": max(last["high"], hi), "low": max(last["low"], lo), "j": j}
                    else:
                        merged[-1] = {"high": min(last["high"], hi), "low": min(last["low"], lo), "j": j}
                else:
                    merged.append(elem)
                if len(merged) >= 3:
                    a, b, c = merged[-3], merged[-2], merged[-1]
                    top = (b["high"] >= a["high"] and b["high"] >= c["high"]
                           and b["low"] >= a["low"] and b["low"] >= c["low"])
                    bottom = (b["low"] <= a["low"] and b["low"] <= c["low"]
                              and b["high"] <= a["high"] and b["high"] <= c["high"])
                    if (direction == "up" and top) or (direction == "down" and bottom):
                        gap = (b["high"] < a["low"]) if direction == "up" else (b["low"] > a["high"])
                        confirmed = True
                        if gap:
                            confirmed = (c["low"] < a["low"]) if direction == "up" else (c["high"] > a["high"])
                        if confirmed:
                            end_stroke = b["j"] - 1
                            break
            j += 1
        confirmed_segment = end_stroke is not None
        if end_stroke is None:
            end_stroke = n - 1
        if end_stroke - i + 1 < min_strokes:
            break
        segment = _make_segment(strokes, i, end_stroke)
        segment["confirmed"] = confirmed_segment
        if not segment["confirmed"]:
            # 未确认线段还在延伸: 终点取当前方向的极值, 而不是最后一笔的终点
            span = strokes[i:end_stroke + 1]
            if segment["dir"] == "up":
                peak = max(span, key=lambda item: max(item["start"]["high"], item["end"]["high"]))
                segment["end_price"] = max(peak["start"]["high"], peak["end"]["high"])
                segment["end_date"] = peak["end"]["date"]
            else:
                trough = min(span, key=lambda item: min(item["start"]["low"], item["end"]["low"]))
                segment["end_price"] = min(trough["start"]["low"], trough["end"]["low"])
                segment["end_date"] = trough["end"]["date"]
        segments.append(segment)
        if not confirmed_segment:
            break
        i = end_stroke + 1
    return segments


# ---------------------------------------------------------------- 中枢
def find_centers(segments):
    """线段级中枢(第18-20课): 连续三线段重叠, 延伸不改变区间。

    - ZD = max(三线段低点), ZG = min(三线段高点), 要求 ZG > ZD;
    - 后续线段与 [ZD, ZG] 有重叠 → 中枢延伸: 区间不变, 结束段后移, 段数+1;
    - 某线段完全在 ZG 之上或 ZD 之下, 且下一段也不回到区间内 → 中枢结束(离开);
    - 段数超过 9 → 标记 upgrade=True(级别扩展, 本版只提示, 不建中枢树);
    - 离开后重新找连续三线段的重叠区 → 新中枢(新生)。
    """
    centers = []
    i, n = 0, len(segments)
    while i + 2 < n:
        trio = segments[i:i + 3]
        zd = max(s["low"] for s in trio)
        zg = min(s["high"] for s in trio)
        if zg <= zd:
            i += 1
            continue
        center = {"start_segment": i, "end_segment": i + 2, "zd": round(zd, 3),
                  "zg": round(zg, 3), "segments": 3, "up": trio[0]["dir"] == "up",
                  "start_date": trio[0]["start_date"], "end_date": trio[2]["end_date"],
                  "upgrade": False}
        j = i + 3
        while j < n:
            seg = segments[j]
            if not (seg["high"] < center["zd"] or seg["low"] > center["zg"]):
                center["end_segment"] = j
                center["segments"] += 1
                center["end_date"] = seg["end_date"]
                j += 1
                continue
            if j + 1 < n:
                nxt = segments[j + 1]
                if not (nxt["high"] < center["zd"] or nxt["low"] > center["zg"]):
                    center["end_segment"] = j + 1
                    center["segments"] += 2
                    center["end_date"] = nxt["end_date"]
                    j += 2
                    continue
            break
        center["upgrade"] = center["segments"] > LEVEL_UPGRADE_SEGMENTS
        centers.append(center)
        i = center["end_segment"] + 1
    return centers


# ---------------------------------------------------------------- 中枢树
def _center_overlap(a, b):
    """两个中枢的区间是否重叠(第20课中枢扩展的前提)。"""
    return not (a["zg"] < b["zd"] or b["zg"] < a["zd"])


def _merge_centers(group, level):
    """把一组相邻且重叠的中枢合并成高一级别中枢(中枢扩展)。"""
    zd = max(c["zd"] for c in group)
    zg = min(c["zg"] for c in group)
    if zg <= zd:  # 完全包含时退化为并集, 保证区间非空
        zd = min(c["zd"] for c in group)
        zg = max(c["zg"] for c in group)
    return {
        "level": level, "zd": round(zd, 3), "zg": round(zg, 3),
        "start_date": min(c["start_date"] for c in group),
        "end_date": max(c["end_date"] for c in group),
        "children": len(group),
        "segments": sum(c.get("segments", 0) for c in group),
    }


def build_center_tree(centers, max_levels=4):
    """中枢树(第20课中枢扩展 + 级别升级)。

    - 相邻且区间重叠的中枢合并为高一级别中枢;
    - 段数超过 9 的单中枢直接提升一级(它本身就是延伸/扩展出来的更大级别中枢);
    - 只做这两种升级, 不处理"两中枢靠一段走势连接"的更复杂情形; 最多 max_levels 层。
    """
    if not centers:
        return []
    first = [dict(c, level=1) for c in centers]
    levels = [{"level": 1, "centers": first}]
    current = first
    while len(levels) < max_levels:
        parents, group = [], [current[0]]
        for center in current[1:]:
            if _center_overlap(group[-1], center):
                group.append(center)
            else:
                if len(group) >= 2:
                    parents.append(_merge_centers(group, len(levels) + 1))
                group = [center]
        if len(group) >= 2:
            parents.append(_merge_centers(group, len(levels) + 1))
        merged_any = bool(parents)
        promoted = [dict(center, level=len(levels) + 1, children=1, promoted=True)
                    for center in current
                    if center.get("segments", 0) > LEVEL_UPGRADE_SEGMENTS]
        parents.extend(promoted)
        if not parents:
            break
        levels.append({"level": len(levels) + 1, "centers": parents})
        if not merged_any:
            break
        current = [parent for parent in parents if not parent.get("promoted")]
        if len(current) < 2:
            break
    return levels


# ---------------------------------------------------------------- MACD / 力度
def ema(series, n):
    if len(series) < n:
        return [None] * len(series)
    k = 2.0 / (n + 1)
    out = [None] * len(series)
    prev = sum(series[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(series)):
        prev = series[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd_of(closes, fast=12, slow=26, sig=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    valid = [d for d in dif if d is not None]
    seed = sum(valid[:sig]) / sig if len(valid) >= sig else 0.0
    dea, prev, first = [], seed, True
    for d in dif:
        if d is None:
            dea.append(None)
            continue
        if first:
            dea.append(seed)
            prev = seed
            first = False
        else:
            prev = d * (2.0 / (sig + 1)) + prev * (1 - 2.0 / (sig + 1))
            dea.append(prev)
    hist = [2 * (d - e) if (d is not None and e is not None) else None for d, e in zip(dif, dea)]
    return dif, dea, hist


def _raw_span(a, b, merged):
    """两个合并K线下标 → 覆盖的原始K线下标区间。"""
    start = merged[a]["src_start"] if a < len(merged) else 0
    end = merged[b]["src_end"] if b < len(merged) else merged[-1]["src_end"]
    return start, max(start, end)


def force_of(ia, ib, raw, merged, dif, hist):
    """一段走势(合并K线下标 ia→ib)的力度: 幅度/成交量/MACD柱面积/DIF 起止。"""
    oa, ob = _raw_span(ia, ib, merged)
    bars = raw[oa:ob + 1]
    area = sum(abs(h) for h in hist[oa:ob + 1] if h is not None)
    dif_values = [d for d in dif[oa:ob + 1] if d is not None]
    lows = [k["low"] for k in bars]
    highs = [k["high"] for k in bars]
    low, high = min(lows), max(highs)
    return {
        "amplitude": round(high - low, 3),
        "amplitude_pct": round((high - low) / low * 100, 2) if low else None,
        "volume": round(sum(k.get("volume") or 0 for k in bars), 1),
        "macd_hist_area": round(area, 3),
        "dif_start": round(dif_values[0], 4) if dif_values else None,
        "dif_end": round(dif_values[-1], 4) if dif_values else None,
    }


def stroke_force(stroke, raw, merged, dif, hist):
    return force_of(stroke["start"]["i"], stroke["end"]["i"], raw, merged, dif, hist)


def segment_force(segment, strokes, raw, merged, dif, hist):
    first = strokes[segment["start_stroke"]]
    last = strokes[segment["end_stroke"]]
    return force_of(first["start"]["i"], last["end"]["i"], raw, merged, dif, hist)


def _stroke_unit(stroke, index):
    """把笔包装成与线段同构的单元, 供 --center-level stroke 使用。"""
    high = max(stroke["start"]["high"], stroke["end"]["high"])
    low = min(stroke["start"]["low"], stroke["end"]["low"])
    return {
        "dir": stroke["dir"], "high": high, "low": low,
        "start_date": stroke["start"]["date"], "end_date": stroke["end"]["date"],
        "start_price": low if stroke["dir"] == "up" else high,
        "end_price": high if stroke["dir"] == "up" else low,
        "start_stroke": index, "end_stroke": index, "strokes": 1, "confirmed": True,
        "start_i": stroke["start"]["i"], "end_i": stroke["end"]["i"],
    }


def unit_force(unit, kind, strokes, raw, merged, dif, hist):
    """单元力度: 线段用首末笔映射, 笔级单元直接用自身合并K线下标。"""
    if kind == "segment":
        return segment_force(unit, strokes, raw, merged, dif, hist)
    return force_of(unit["start_i"], unit["end_i"], raw, merged, dif, hist)


# ---------------------------------------------------------------- 背驰
def _compare(prev, last):
    weak_price = last["amplitude"] < prev["amplitude"]
    weak_volume = last["volume"] < prev["volume"]
    weak_area = last["macd_hist_area"] < prev["macd_hist_area"]
    votes = sum([weak_price, weak_volume, weak_area])
    return weak_price, weak_volume, weak_area, votes >= 2


def detect_divergence(segments, centers, strokes, raw, merged, dif, hist, unit_kind="segment"):
    """背驰(第15/24课), 区分趋势背驰与盘整背驰。

    - 趋势背驰: 围绕同一个中枢的"进入段"与"离开段"同向(中枢前一段 vs 中枢后一段),
      后者力度更弱 → 构成一买/一卖;
    - 盘整背驰: 中枢内部相隔一段的两段同向走势比较, 只保证回抽中枢, 不构成一买/一卖。
    """
    out = []

    def add(prev_seg, last_seg, kind, center_index):
        prev = unit_force(prev_seg, unit_kind, strokes, raw, merged, dif, hist)
        last = unit_force(last_seg, unit_kind, strokes, raw, merged, dif, hist)
        weak_price, weak_volume, weak_area, divergence = _compare(prev, last)
        out.append({
            "kind": kind,
            "dir": last_seg["dir"],
            "center": center_index,
            "prev": {"start": prev_seg["start_date"], "end": prev_seg["end_date"],
                     "end_price": prev_seg["end_price"], **prev},
            "last": {"start": last_seg["start_date"], "end": last_seg["end_date"],
                     "end_price": last_seg["end_price"], **last},
            "weak_price": weak_price, "weak_volume": weak_volume, "weak_macd_area": weak_area,
            "divergence": divergence,
        })

    for ci, center in enumerate(centers):
        entry = center["start_segment"] - 1
        leave = center["end_segment"] + 1
        if entry >= 0 and leave < len(segments) and segments[entry]["dir"] == segments[leave]["dir"]:
            add(segments[entry], segments[leave], "trend", ci)
        for si in range(center["start_segment"], center["end_segment"] - 1):
            if segments[si]["dir"] == segments[si + 2]["dir"]:
                add(segments[si], segments[si + 2], "consolidation", ci)
    return out


# ---------------------------------------------------------------- 买卖点
def _signal(kind, level, state, date, price, trigger, invalidate, note):
    return {"type": kind, "level": level, "state": state, "date": date, "price": price,
            "trigger": trigger, "invalidate": invalidate, "note": note}


def _sub_level_pullback(sub_strokes, start_date, end_date, low_bound):
    """用次级别走势确认"回抽不进入中枢": 回抽窗口内要有向下的次级别笔, 低点高于 low_bound,
    且其后出现向上的次级别笔(回抽结束、转向上)。"""
    if not sub_strokes:
        return None
    window = [s for s in sub_strokes if start_date <= s["end"]["date"] <= end_date]
    downs = [s for s in window if s["dir"] == "down" and s["end"]["low"] > low_bound]
    if not downs:
        return None
    last = downs[-1]
    after = [s for s in sub_strokes[sub_strokes.index(last) + 1:] if s["dir"] == "up"]
    return {"low": last["end"]["low"], "date": last["end"]["date"], "turned_up": bool(after)}


def _sub_level_rebound(sub_strokes, start_date, end_date, high_bound):
    """用次级别走势确认"反抽不进入中枢": 反抽窗口内要有向上的次级别笔, 高点低于 high_bound,
    且其后出现向下的次级别笔(反抽结束、转向下)。"""
    if not sub_strokes:
        return None
    window = [s for s in sub_strokes if start_date <= s["end"]["date"] <= end_date]
    ups = [s for s in window if s["dir"] == "up" and s["end"]["high"] < high_bound]
    if not ups:
        return None
    last = ups[-1]
    after = [s for s in sub_strokes[sub_strokes.index(last) + 1:] if s["dir"] == "down"]
    return {"high": last["end"]["high"], "date": last["end"]["date"], "turned_down": bool(after)}


def detect_signals(segments, centers, divs, last_close, strokes, sub_strokes=None, level="main",
                   unit_kind="segment"):
    """三类买卖点(第21-27课), 状态机 + 互斥。

    - 一买/一卖: 只认**趋势背驰**(中枢进入段 vs 离开段同向且力度衰竭); 盘整背驰只出
      buy_short/sell_short, 不构成一买一卖;
    - 三买/三卖: 向上离开中枢(离开段完全在 ZG 之上)→ 下一段回抽低点 > ZG; 有次级别数据
      时用区间套确认(state=confirmed), 否则 state=candidate;
    - 二买/二卖: 一买后第一次次级别回调不破一买低点; 无次级别数据时退化为下一笔, state=approx;
    - 互斥: 同级别同一天不得同时出现买点与卖点, 冲突时两条都不给并记录到 conflicts。
    """
    signals, conflicts = [], []
    trend = [d for d in divs if d["kind"] == "trend" and d["divergence"]]
    trend.sort(key=lambda d: d["last"]["end"])
    buy1 = sell1 = None
    if trend:
        d = trend[-1]
        last_seg = segments[-1] if segments else None
        recent = d["last"]["end"]
        if last_seg is not None and recent >= (segments[-2]["end_date"] if len(segments) >= 2 else last_seg["end_date"]):
            if d["dir"] == "down":
                buy1 = _signal("buy1", level, "confirmed", recent, d["last"]["end_price"],
                               None, d["last"]["end_price"],
                               "趋势背驰: 下跌离开段力度弱于进入段(价%s 量%s MACD面积%s)" % (
                                   "↓" if d["weak_price"] else "→",
                                   "↓" if d["weak_volume"] else "→",
                                   "↓" if d["weak_macd_area"] else "→"))
            else:
                sell1 = _signal("sell1", level, "confirmed", recent, d["last"]["end_price"],
                                None, d["last"]["end_price"],
                                "趋势背驰: 上涨离开段力度弱于进入段(价%s 量%s MACD面积%s)" % (
                                    "↓" if d["weak_price"] else "→",
                                    "↓" if d["weak_volume"] else "→",
                                    "↓" if d["weak_macd_area"] else "→"))
    if buy1:
        signals.append(buy1)
    if sell1:
        signals.append(sell1)

    for d in divs:
        if d["kind"] != "consolidation" or not d["divergence"]:
            continue
        if d["dir"] == "down":
            signals.append(_signal("buy_short", level, "candidate", d["last"]["end"],
                                   d["last"]["end_price"], None, d["last"]["end_price"],
                                   "盘整背驰: 只保证回抽中枢; 跌破该段低点即失效"))
        else:
            signals.append(_signal("sell_short", level, "candidate", d["last"]["end"],
                                   d["last"]["end_price"], None, d["last"]["end_price"],
                                   "盘整背驰: 只保证回抽中枢; 升破该段高点即失效"))

    if centers:
        center = centers[-1]
        leave_i = center["end_segment"] + 1
        pull_i = leave_i + 1
        if leave_i < len(segments) and pull_i < len(segments):
            leave, pull = segments[leave_i], segments[pull_i]
            if leave["dir"] == "up" and leave["low"] > center["zg"] and pull["dir"] == "down" and pull["low"] > center["zg"]:
                state, note = "candidate", "离开段在 ZG 之上, 回抽低点未进中枢(未用次级别确认)"
                if sub_strokes is not None:
                    confirm = _sub_level_pullback(sub_strokes, pull["start_date"], pull["end_date"], center["zg"])
                    if confirm and confirm["turned_up"]:
                        state = "confirmed"
                        note = "次级别回抽笔低点 %.2f > ZG %.2f 且已转向上" % (confirm["low"], center["zg"])
                    elif sub_strokes:
                        note = "离开段成立, 但次级别回抽尚未确认(未转向上)"
                signals.append(_signal("buy3", level, state, pull["end_date"], pull["end_price"],
                                       pull["low"], center["zg"], note))
            if leave["dir"] == "down" and leave["high"] < center["zd"] and pull["dir"] == "up" and pull["high"] < center["zd"]:
                state, note = "candidate", "离开段在 ZD 之下, 反抽高点未进中枢(未用次级别确认)"
                if sub_strokes is not None:
                    confirm = _sub_level_rebound(sub_strokes, pull["start_date"], pull["end_date"], center["zd"])
                    if confirm and confirm["turned_down"]:
                        state = "confirmed"
                        note = "次级别反抽笔高点 %.2f < ZD %.2f 且已转向下" % (confirm["high"], center["zd"])
                    elif sub_strokes:
                        note = "离开段成立, 但次级别反抽尚未确认(未转向下)"
                signals.append(_signal("sell3", level, state, pull["end_date"], pull["end_price"],
                                       pull["high"], center["zd"], note))

    if buy1:
        low = buy1["price"]
        if low is not None:
            if sub_strokes:
                downs = [s for s in sub_strokes if s["dir"] == "down" and s["end"]["date"] > buy1["date"]]
                if downs:
                    first = downs[0]
                    if first["end"]["low"] > low:
                        turned = any(s["dir"] == "up" for s in sub_strokes[sub_strokes.index(first) + 1:])
                        signals.append(_signal("buy2", level, "confirmed" if turned else "candidate",
                                               first["end"]["date"], first["end"]["low"], first["end"]["low"], low,
                                               "一买后首次次级别回调不破前低"))
            elif len(strokes) >= 2:
                later = [s for s in strokes if s["dir"] == "down" and s["end"]["date"] > buy1["date"]]
                if later and later[0]["end"]["low"] > low:
                    signals.append(_signal("buy2", level, "approx", later[0]["end"]["date"],
                                           later[0]["end"]["low"], later[0]["end"]["low"], low,
                                           "未提供次级别数据, 用本级别下一笔近似二买"))
    if sell1:
        high = sell1["price"]
        if high is not None:
            if sub_strokes:
                ups = [s for s in sub_strokes if s["dir"] == "up" and s["end"]["date"] > sell1["date"]]
                if ups:
                    first = ups[0]
                    if first["end"]["high"] < high:
                        turned = any(s["dir"] == "down" for s in sub_strokes[sub_strokes.index(first) + 1:])
                        signals.append(_signal("sell2", level, "confirmed" if turned else "candidate",
                                               first["end"]["date"], first["end"]["high"], first["end"]["high"], high,
                                               "一卖后首次次级别反抽不破前高"))

    for signal in signals:
        signal["unit"] = unit_kind
    buys = [s for s in signals if s["type"].startswith("buy")]
    sells = [s for s in signals if s["type"].startswith("sell")]
    if buys and sells:
        dates_buy = {s["date"] for s in buys}
        dates_sell = {s["date"] for s in sells}
        same = sorted(dates_buy & dates_sell)
        if same:
            for date in same:
                conflicts.append({"date": date, "level": level,
                                  "note": "同日出现买点与卖点, 已同时丢弃"})
            signals = [s for s in signals if not (s["type"].startswith("buy") and s["date"] in same)
                       and not (s["type"].startswith("sell") and s["date"] in same)]
    return signals, conflicts


# ---------------------------------------------------------------- 关键价位
def describe_position(pending, strokes, fx, last_close, segments):
    """当前走势定位。候选笔的起点是上一笔的终点; 收盘价越过起点极值即判定候选失效。"""
    if pending is not None:
        direction = "上升" if pending["type"] == "top" else "下降"
        base = "%s笔候选(终点未确认, %s于%s)" % (
            direction, "顶分型" if pending["type"] == "top" else "底分型", pending["date"])
        anchor = strokes[-1]["end"] if strokes else (fx[0] if fx else None)
        if anchor is not None:
            if pending["type"] == "bottom" and last_close > anchor["high"]:
                return "下降笔候选已失效(收盘 %.2f 高于起点 %.2f), 方向待新分型确认" % (
                    last_close, anchor["high"])
            if pending["type"] == "top" and last_close < anchor["low"]:
                return "上升笔候选已失效(收盘 %.2f 低于起点 %.2f), 方向待新分型确认" % (
                    last_close, anchor["low"])
        return base
    if segments:
        return "%s线段(进行中, 未确认)" % ("上升" if segments[-1]["dir"] == "up" else "下降")
    return "中性"


def build_levels(segments, centers, strokes, signals):
    """支撑/阻力/触发/失效, 全部取自脚本自身的结构输出, 报告直接引用, 不手改数字。"""
    support, resistance = [], []
    if strokes:
        last = strokes[-1]
        support.append(last["start"]["low"] if last["dir"] == "up" else last["end"]["low"])
        resistance.append(last["end"]["high"] if last["dir"] == "up" else last["start"]["high"])
    if segments:
        support.append(segments[-1]["low"])
        resistance.append(segments[-1]["high"])
    if centers:
        support.append(centers[-1]["zd"])
        resistance.append(centers[-1]["zg"])
    trigger = invalidate = None
    for signal in signals:
        if signal["type"] in ("buy1", "buy2", "buy3", "buy_short"):
            if signal["trigger"] is not None:
                trigger = signal["trigger"]
            if signal["invalidate"] is not None:
                invalidate = signal["invalidate"]
    return {
        "support": sorted({round(v, 3) for v in support if v is not None}, reverse=True),
        "resistance": sorted({round(v, 3) for v in resistance if v is not None}),
        "trigger": trigger,
        "invalidate": invalidate,
    }



# ---------------------------------------------------------------- 多级别联立
def _date_only(value):
    """取日期部分, 便于日线与分钟线比较。"""
    return str(value).split(" ")[0]


def _latest(signals, side):
    items = [s for s in signals if s["type"].startswith(side)]
    return items[-1] if items else None


def compare_levels(main_result, sub_result):
    """大级别与次级别的共振/矛盾判断(第29-32课)。

    只比较**各自最近的一个信号**（按信号日期），避免把历史上的多空信号混在一起:
    - 共振: 两侧最新信号同向 → 置信度最高;
    - 矛盾: 方向相反 → 以大级别为准, 小级别信号只当回踩/反弹;
    - 待确认: 大级别有最新信号、次级别还没有。
    """
    def latest(signals):
        if not signals:
            return None
        return max(signals, key=lambda s: _date_only(s["date"]))

    main_signal, sub_signal = latest(main_result["signals"]), latest(sub_result["signals"])
    pairs = []
    if main_signal and sub_signal:
        main_side = "buy" if main_signal["type"].startswith("buy") else "sell"
        sub_side = "buy" if sub_signal["type"].startswith("buy") else "sell"
        if main_side == sub_side:
            pairs.append({"verdict": "共振", "main": main_signal["type"], "sub": sub_signal["type"],
                          "note": "大级别与次级别最新信号同向, 置信度最高"})
        else:
            pairs.append({"verdict": "矛盾", "main": main_signal["type"], "sub": sub_signal["type"],
                          "note": "次级别最新信号反向: 以大级别为准, 小级别只当回踩/反弹"})
    elif main_signal:
        pairs.append({"verdict": "待确认", "main": main_signal["type"], "sub": None,
                      "note": "大级别最新信号已成立, 等次级别同向确认"})
    if any(p["verdict"] == "共振" for p in pairs):
        state = "aligned"
    elif any(p["verdict"] == "矛盾" for p in pairs):
        state = "conflict"
    else:
        state = "none"
    return {"state": state, "pairs": pairs}


# ---------------------------------------------------------------- 历史信号回溯
def simulate_trade(entry_open, forward, is_buy, invalidate, fee_rate=0.0005, slippage=0.0005):
    """单笔交易模拟: 开盘入场(计滑点) → 触发失效价则止损, 否则到期收盘出场; 双边计手续费。"""
    entry_price = entry_open * (1 + slippage) if is_buy else entry_open * (1 - slippage)
    exit_price, exit_reason, bars_held = forward[-1]["close"], "horizon", len(forward)
    # 失效价必须在入场价的正确一侧: 多头止损低于入场、空头高于入场, 否则视为无效止损
    stop_valid = invalidate is not None and ((is_buy and invalidate < entry_price)
                                             or (not is_buy and invalidate > entry_price))
    if stop_valid:
        for index, bar in enumerate(forward):
            hit = (bar["low"] <= invalidate) if is_buy else (bar["high"] >= invalidate)
            if hit:
                exit_price = (invalidate * (1 - slippage) if is_buy
                              else invalidate * (1 + slippage))
                exit_reason, bars_held = "stop", index + 1
                break
    highs = [bar["high"] for bar in forward]
    lows = [bar["low"] for bar in forward]
    if is_buy:
        gross = (exit_price - entry_price) / entry_price
        max_gain = (max(highs) - entry_price) / entry_price
        max_drawdown = (min(lows) - entry_price) / entry_price
    else:
        gross = (entry_price - exit_price) / entry_price
        max_gain = (entry_price - min(lows)) / entry_price
        max_drawdown = (entry_price - max(highs)) / entry_price
    fee_cost = 2 * fee_rate
    return {
        "entry_price": round(entry_price, 4), "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason, "bars_held": bars_held,
        "max_gain_pct": round(max_gain * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "return_pct": round(gross * 100, 2),
        "fee_cost_pct": round(fee_cost * 100, 3),
        "net_return_pct": round((gross - fee_cost) * 100, 2),
        "invalidated": exit_reason == "stop",
    }


def backtest_signals(klines, horizon=20, stride=1, min_bars=120, center_level="segment",
                     stale_bars=20, fee_rate=0.0005, slippage=0.0005, entry_delay=1):
    """滑动窗口回溯 + 执行模拟。

    - 按"首次识别"记账: 信号日期距识别时点超过 stale_bars 根的丢弃(陈旧信号);
    - 执行: 识别后第 entry_delay 根的开盘价入场, 买卖价各加减 slippage;
    - 出场: 先触发 invalidate(止损/失效价)则按该价出场, 否则持有 horizon 根按收盘出场;
    - 成本: 双边各收 fee_rate(默认 5bp), 净收益 = 毛收益 - 2*fee_rate;
    - 买点按做多、卖点按做空折算, 净收益为正表示信号方向赚钱。
    """
    index_of = {k["date"]: i for i, k in enumerate(klines)}
    records, seen = [], set()
    for end in range(min_bars, len(klines), stride):
        window = klines[:end + 1]
        result = analyze(window, center_level=center_level)
        detected_at = window[-1]["date"]
        detected_index = index_of[detected_at]
        for signal in result["signals"]:
            key = (signal["type"], signal["date"])
            if key in seen:
                continue
            seen.add(key)
            signal_index = index_of.get(_date_only(signal["date"]))
            if signal_index is None or detected_index - signal_index > stale_bars:
                continue
            entry_index = end + entry_delay
            if entry_index >= len(klines):
                continue
            is_buy = signal["type"].startswith("buy")
            entry_open = klines[entry_index]["open"]
            forward = klines[entry_index:entry_index + horizon]
            if not forward or not entry_open:
                continue
            trade = simulate_trade(entry_open, forward, is_buy, signal["invalidate"],
                                   fee_rate, slippage)
            record = {
                "type": signal["type"], "state": signal["state"],
                "direction": "long" if is_buy else "short",
                "signal_date": signal["date"], "detected_at": detected_at,
                "entry": trade["entry_price"], "trigger": signal["trigger"],
                "invalidate": signal["invalidate"], "bars_forward": len(forward),
                "bars_to_invalidate": trade["bars_held"] if trade["invalidated"] else None,
                **trade,
            }
            records.append(record)
    return records


def summarize_backtest(records, horizon):
    """回溯统计: 类型计数、毛/净收益、胜率、止损与到期出场分布。"""
    by_type = Counter(record["type"] for record in records)
    evaluated = [record for record in records if record.get("net_return_pct") is not None]
    nets = [record["net_return_pct"] for record in evaluated]
    gross = [record["return_pct"] for record in evaluated]
    buys = [record for record in evaluated if record["type"].startswith("buy")]

    def mean(values):
        return round(sum(values) / len(values), 2) if values else None

    return {
        "count": len(records),
        "by_type": dict(sorted(by_type.items())),
        "evaluated": len(evaluated),
        "invalidated": sum(1 for record in records if record.get("invalidated")),
        "stop_exits": sum(1 for record in records if record.get("exit_reason") == "stop"),
        "horizon_exits": sum(1 for record in records if record.get("exit_reason") == "horizon"),
        "win_rate": round(sum(1 for value in nets if value > 0) / len(nets), 3) if nets else None,
        "avg_return_pct": mean(gross),
        "avg_net_return_pct": mean(nets),
        "avg_max_gain_pct": mean([record["max_gain_pct"] for record in evaluated]),
        "avg_max_drawdown_pct": mean([record["max_drawdown_pct"] for record in evaluated]),
        "avg_bars_held": mean([record["bars_held"] for record in evaluated]),
        "buy_avg_return_pct": mean([record["return_pct"] for record in buys]),
        "horizon": horizon,
    }


CSV_COLUMNS = ("type", "state", "direction", "signal_date", "detected_at", "entry_price",
               "exit_price", "exit_reason", "bars_held", "gross_return_pct", "fee_cost_pct",
               "net_return_pct", "max_gain_pct", "max_drawdown_pct", "invalidated",
               "bars_to_invalidate")


def trades_to_csv(records):
    """把回溯记录导出成 CSV 文本(供外部做统计/画图)。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for record in records:
        row = []
        for column in CSV_COLUMNS:
            key = "return_pct" if column == "gross_return_pct" else column
            row.append(record.get(key, ""))
        writer.writerow(row)
    return buffer.getvalue()


# ---------------------------------------------------------------- 主流程
def analyze(klines, name="", sub_klines=None, center_level="segment"):
    klines = sorted(klines, key=lambda k: k["date"])
    merged = process_include(klines)
    fx = _seed_initial_fractal(find_fractals(merged, klines), merged)
    strokes, pending = build_strokes(fx)
    segments = build_segments(strokes)
    units = segments if center_level == "segment" else [_stroke_unit(st, i) for i, st in enumerate(strokes)]
    centers = find_centers(units)
    closes = [k["close"] for k in klines]
    dif, dea, hist = macd_of(closes)
    divs = detect_divergence(units, centers, strokes, klines, merged, dif, hist, center_level)

    sub_strokes = None
    if sub_klines:
        sub_merged = process_include(sub_klines)
        sub_fx = _seed_initial_fractal(find_fractals(sub_merged, sub_klines), sub_merged)
        sub_strokes, _ = build_strokes(sub_fx)

    last_close = closes[-1]
    signals, conflicts = detect_signals(units, centers, divs, last_close, strokes, sub_strokes,
                                        unit_kind=center_level)
    position = describe_position(pending, strokes, fx, last_close, segments)

    result = {
        "name": name,
        "bars": len(klines),
        "date_range": [klines[0]["date"], klines[-1]["date"]],
        "last_close": last_close,
        "method": {
            "strokes": "new-bi (第77/78课)",
            "segments": "feature-sequence-fractal (第69-76课, 简化缺口确认)",
            "centers": "segment-level, extend-without-widening (第18-20课)",
            "divergence": "trend vs consolidation (第15/24课)",
            "signals": "state machine + mutex (第21-27课)",
        },
        "fractals": fx,
        "strokes": [{
            "dir": s["dir"],
            "start_date": s["start"]["date"],
            "start_price": s["start"]["low"] if s["dir"] == "up" else s["start"]["high"],
            "end_date": s["end"]["date"],
            "end_price": s["end"]["high"] if s["dir"] == "up" else s["end"]["low"],
            "start_low": s["start"]["low"], "start_high": s["start"]["high"],
            "end_low": s["end"]["low"], "end_high": s["end"]["high"],
            "force": stroke_force(s, klines, merged, dif, hist),
        } for s in strokes],
        "center_level": center_level,
        "unit_count": len(units),
        "segments": [{
            **{k: v for k, v in seg.items() if k != "strokes"},
            "stroke_count": seg["strokes"],
            "force": segment_force(seg, strokes, klines, merged, dif, hist),
        } for seg in segments],
        "pending": pending,
        "centers": centers,
        "center_tree": build_center_tree(centers),
        "divergences": divs,
        "signals": signals,
        "conflicts": conflicts,
        "levels": build_levels(segments, centers, strokes, signals),
        "position": position,
    }

    if sub_klines:
        sub_result = analyze(sub_klines, (name + "·次级别") if name else "次级别")
        result["multi_level"] = {
            "sub": {key: sub_result[key] for key in
                    ("bars", "date_range", "last_close", "position", "signals", "centers", "levels")},
            "confluence": compare_levels(result, sub_result),
        }
    return result


def _tail(items, last, label):
    if last and len(items) > last:
        return items[-last:], "（仅显示最后 %d 条 %s, 共 %d 条; --last 0 显示全部）" % (last, label, len(items))
    return items, None


def summarize(r, last=8):
    """文本摘要; last>0 时只显示最近 last 条笔/线段/中枢/背驰。"""
    L = []
    L.append("标的: %s  |  K线 %d 根 (%s ~ %s)" % (r["name"] or "—", r["bars"], *r["date_range"]))
    L.append("最新收盘: %s  |  当前走势定位: %s" % (r["last_close"], r["position"]))
    L.append("识别: 分型 %d 个(顶 %d/底 %d), 笔 %d 笔, 线段 %d 条, 中枢 %d 个" % (
        len(r["fractals"]),
        sum(1 for f in r["fractals"] if f["type"] == "top"),
        sum(1 for f in r["fractals"] if f["type"] == "bottom"),
        len(r["strokes"]), len(r["segments"]), len(r["centers"])))
    if r["strokes"]:
        shown, note = _tail(r["strokes"], last, "笔")
        L.append("笔:" + ("  " + note if note else ""))
        for s in shown:
            arrow = "↑" if s["dir"] == "up" else "↓"
            L.append("  %s %s(%.2f) → %s(%.2f)  幅度%.1f%% 量%.0f" % (
                arrow, s["start_date"], s["start_price"], s["end_date"], s["end_price"],
                s["force"]["amplitude_pct"], s["force"]["volume"]))
    if r["segments"]:
        shown, note = _tail(r["segments"], last, "线段")
        L.append("线段:" + ("  " + note if note else ""))
        for s in shown:
            arrow = "↑" if s["dir"] == "up" else "↓"
            L.append("  %s %s(%.2f) → %s(%.2f)  [%.2f, %.2f] %d笔%s" % (
                arrow, s["start_date"], s["start_price"], s["end_date"], s["end_price"],
                s["low"], s["high"], s["stroke_count"], "" if s["confirmed"] else " 未确认"))
    if r["centers"] and r["centers"][-1]["end_segment"] >= r.get("unit_count", 0) - 1:
        L.append("提示: 最后中枢仍在延伸(尚未出现离开段), 因此三买/三卖/一买/一卖都还没有成立条件")
    tree = r.get("center_tree") or []
    if len(tree) > 1:
        L.append("中枢树: " + " | ".join(
            "L%d: %d 个" % (level["level"], len(level["centers"])) for level in tree))
    if r["centers"]:
        shown, note = _tail(r["centers"], last, "中枢")
        L.append("中枢(线段级):" + ("  " + note if note else ""))
        for c in shown:
            L.append("  %s ~ %s  ZD=%.2f ZG=%.2f (%s中枢, %d段%s)" % (
                c["start_date"], c["end_date"], c["zd"], c["zg"],
                "上涨" if c["up"] else "下跌", c["segments"],
                ", ⚠级别升级" if c["upgrade"] else ""))
    if r["divergences"]:
        shown, note = _tail(r["divergences"], last, "背驰")
        L.append("背驰:" + ("  " + note if note else ""))
        for d in shown:
            kind = "趋势" if d["kind"] == "trend" else "盘整"
            mark = "%s背驰" % kind if d["divergence"] else "无背驰"
            L.append("  [%s] %s: %s→%s  价%.2f→%.2f 量%.0f→%.0f MACD面积%.2f→%.2f  [%s]" % (
                kind, d["dir"], d["prev"]["end"], d["last"]["end"],
                d["prev"]["amplitude"], d["last"]["amplitude"],
                d["prev"]["volume"], d["last"]["volume"],
                d["prev"]["macd_hist_area"], d["last"]["macd_hist_area"], mark))
    L.append("买卖点信号:")
    if not r["signals"]:
        L.append("  (当前没有符合口径的买卖点 —— 严格口径下这是正常结果)")
    for s in r["signals"]:
        L.append("  %s[%s]: %s  价格=%s 触发=%s 失效=%s — %s" % (
            s["type"], s["state"], s["date"], s["price"], s["trigger"], s["invalidate"], s["note"]))
    for c in r["conflicts"]:
        L.append("  ⚠冲突: %s %s" % (c["date"], c["note"]))
    lv = r["levels"]
    L.append("关键价位: 支撑 %s | 阻力 %s | 触发 %s | 失效 %s" % (
        lv["support"], lv["resistance"], lv["trigger"], lv["invalidate"]))
    multi = r.get("multi_level")
    if multi:
        sub = multi["sub"]
        L.append("次级别: %d 根 (%s ~ %s) 收盘 %s | %s" % (
            sub["bars"], sub["date_range"][0], sub["date_range"][1], sub["last_close"], sub["position"]))
        conf = multi["confluence"]
        if conf["pairs"]:
            for pair in conf["pairs"]:
                L.append("  联立[%s]: 大级别 %s / 次级别 %s — %s" % (
                    pair["verdict"], pair["main"], pair["sub"] or "无", pair["note"]))
        else:
            L.append("  联立: 两侧都没有买卖点, 不构成共振或矛盾")
    history = r.get("history")
    if history:
        summary = history["summary"]
        L.append("历史信号回溯(前瞻 %d 根): 共 %d 条 %s, 已失效 %d 条" % (
            summary["horizon"], summary["count"], summary["by_type"], summary["invalidated"]))
        L.append("  毛收益 %s%% | 净收益 %s%% | 胜率 %s | 止损出场 %d / 到期 %d" % (
            summary["avg_return_pct"], summary["avg_net_return_pct"], summary["win_rate"],
            summary["stop_exits"], summary["horizon_exits"]))
        L.append("  平均最大浮盈 %s%% | 平均最大回撤 %s%% | 平均持有 %s 根" % (
            summary["avg_max_gain_pct"], summary["avg_max_drawdown_pct"], summary["avg_bars_held"]))
        for record in history["records"][-5:]:
            L.append("  %s %s 识别于 %s 入场 %.2f 出场 %.2f(%s) → 净收益 %s%% 最大回撤 %s%%" % (
                record["type"], record["signal_date"], record["detected_at"],
                record["entry_price"], record["exit_price"], record["exit_reason"],
                record["net_return_pct"], record["max_drawdown_pct"]))
        if len(history["records"]) > 5:
            L.append("  （仅显示最后 5 条, 全部在 --out 的 JSON 里）")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="缠中说禅机械化分析")
    ap.add_argument("klines", help="主级别 K线 JSON 文件路径")
    ap.add_argument("--sub-level", default="",
                    help="次级别 K线 JSON(如 30 分钟), 用于三买/三卖/二买的区间套确认")
    ap.add_argument("--name", default="", help="标的名称")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    ap.add_argument("--last", type=int, default=8,
                    help="文本摘要最多显示的笔/线段/中枢/背驰条数, 0=全部(默认 8)")
    ap.add_argument("--min-bars", type=int, default=MIN_BARS,
                    help="主级别最少 K 线根数, 默认 %d" % MIN_BARS)
    ap.add_argument("--allow-short", action="store_true",
                    help="允许少于 --min-bars 根(结果仅供参考)")
    ap.add_argument("--center-level", choices=("segment", "stroke"), default="segment",
                    help="中枢构件: segment=线段级(默认, 需更长历史); stroke=笔级(短数据降级, 噪声更大)")
    ap.add_argument("--history", action="store_true",
                    help="滑动窗口回溯历史信号, 并统计其后表现")
    ap.add_argument("--history-horizon", type=int, default=20,
                    help="回溯时看信号后多少根 K 线, 默认 20")
    ap.add_argument("--history-stride", type=int, default=1,
                    help="回溯滑动步长, 默认 1(逐根)")
    ap.add_argument("--history-min-bars", type=int, default=120,
                    help="回溯从第几根开始(需要足够历史), 默认 120")
    ap.add_argument("--history-stale-bars", type=int, default=20,
                    help="信号日期距识别时点超过多少根就丢弃(陈旧信号), 默认 20")
    ap.add_argument("--history-fee", type=float, default=0.0005,
                    help="单边手续费率(默认 0.0005, 即 5bp; 双边合计计入净收益)")
    ap.add_argument("--history-slippage", type=float, default=0.0005,
                    help="单边滑点(默认 0.0005), 买价上浮、卖价下调各一次")
    ap.add_argument("--history-entry-delay", type=int, default=1,
                    help="识别后第几根开盘入场, 默认 1(避免用识别当根收盘偷跑)")
    ap.add_argument("--history-out", default="",
                    help="把回溯明细写成 CSV 的路径")
    args = ap.parse_args(argv)
    if args.last < 0:
        ap.error("--last 不能为负")
    if args.min_bars < 0:
        ap.error("--min-bars 不能为负")

    try:
        klines = load_klines(args.klines, args.min_bars, args.allow_short)
        sub_klines = None
        if args.sub_level:
            sub_klines = load_klines(args.sub_level, MIN_SUB_BARS, True)
            if len(sub_klines) < MIN_SUB_BARS:
                _warn("次级别仅 %d 根(<%d), 区间套确认可能不可靠" % (len(sub_klines), MIN_SUB_BARS))
    except DataError as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2

    r = analyze(klines, args.name, sub_klines, args.center_level)
    if args.history:
        if args.history_horizon < 1:
            ap.error("--history-horizon 必须为正")
        if args.history_stride < 1:
            ap.error("--history-stride 必须为正")
        if len(klines) <= args.history_min_bars:
            print("错误: K 线 %d 根不足以回溯(需要 > %d 根)" % (len(klines), args.history_min_bars), file=sys.stderr)
            return 2
        if args.history_fee < 0 or args.history_slippage < 0:
            ap.error("--history-fee / --history-slippage 不能为负")
        if args.history_entry_delay < 0:
            ap.error("--history-entry-delay 不能为负")
        records = backtest_signals(klines, args.history_horizon, args.history_stride,
                                   args.history_min_bars, args.center_level,
                                   args.history_stale_bars, args.history_fee,
                                   args.history_slippage, args.history_entry_delay)
        r["history"] = {"records": records,
                        "summary": summarize_backtest(records, args.history_horizon)}
        if args.history_out:
            try:
                with open(args.history_out, "w", encoding="utf-8", newline="") as handle:
                    handle.write(trades_to_csv(records))
            except OSError as exc:
                print("错误: 无法写入 %s: %s" % (args.history_out, exc), file=sys.stderr)
                return 2
            print("回溯明细已写入 %s" % args.history_out)
    print(summarize(r, args.last))
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(r, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            print("错误: 无法写入 %s: %s" % (args.out, exc), file=sys.stderr)
            return 2
        print("\n结果已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
