#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chanlun_analyze.py 的回归测试(标准库 unittest, 无第三方依赖)。

运行:
    python3 -m unittest discover -s tests -v
    # 或
    python3 tests/test_chanlun.py
"""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import chanlun_analyze as C  # noqa: E402
import westock_kline_to_json as W  # noqa: E402
import batch_scan as B  # noqa: E402
import param_sweep as P  # noqa: E402
import render_chart as R  # noqa: E402
import make_report as M  # noqa: E402


def bar(date, o, h, l, c, v=1000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def stroke(direction, start_date, start_price, end_date, end_price, high, low):
    return {
        "dir": direction,
        "start": {"date": start_date, "high": high, "low": low},
        "end": {"date": end_date, "high": high, "low": low},
    }


def segment(direction, start_date, end_date, low, high, start_stroke=0, end_stroke=1):
    return {
        "dir": direction, "start_date": start_date, "end_date": end_date,
        "start_price": low if direction == "up" else high,
        "end_price": high if direction == "up" else low,
        "high": high, "low": low, "start_stroke": start_stroke, "end_stroke": end_stroke,
        "strokes": end_stroke - start_stroke + 1, "confirmed": True,
    }


class LoadKlinesTest(unittest.TestCase):
    def write(self, payload):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            if isinstance(payload, str):
                fh.write(payload)
            else:
                json.dump(payload, fh, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def test_rejects_empty(self):
        with self.assertRaises(C.DataError):
            C.load_klines(self.write([]), 0, True)

    def test_rejects_missing_field(self):
        with self.assertRaises(C.DataError) as ctx:
            C.load_klines(self.write([{"date": "d", "open": 1, "low": 0, "close": 1}]), 0, True)
        self.assertIn("high", str(ctx.exception))

    def test_rejects_duplicate_dates(self):
        rows = [bar("2026-01-01", 1, 2, 0, 1)] * 3
        with self.assertRaises(C.DataError) as ctx:
            C.load_klines(self.write(rows), 0, True)
        self.assertIn("重复日期", str(ctx.exception))

    def test_rejects_short_input_by_default(self):
        rows = [bar("2026-01-%02d" % (i + 1), 10 + i, 11 + i, 9 + i, 10.5 + i) for i in range(20)]
        with self.assertRaises(C.DataError):
            C.load_klines(self.write(rows), 60, False)

    def test_allow_short(self):
        rows = [bar("2026-01-%02d" % (i + 1), 10 + i, 11 + i, 9 + i, 10.5 + i) for i in range(20)]
        self.assertEqual(len(C.load_klines(self.write(rows), 60, True)), 20)

    def test_sorts_by_date(self):
        rows = [bar("2026-01-02", 2, 3, 1, 2), bar("2026-01-01", 1, 2, 0, 1), bar("2026-01-03", 3, 4, 2, 3)]
        self.assertEqual([r["date"] for r in C.load_klines(self.write(rows), 0, True)],
                         ["2026-01-01", "2026-01-02", "2026-01-03"])


class InclusionTest(unittest.TestCase):
    def test_containment_merges_up(self):
        rows = [bar("d1", 10, 11, 9, 10), bar("d2", 10, 12, 9.5, 11), bar("d3", 11, 13, 10.5, 12)]
        merged = C.process_include(rows)
        self.assertEqual(len(merged), 3)

    def test_merges_contained_bar(self):
        rows = [bar("d1", 10, 12, 9, 11), bar("d2", 10.5, 11, 10, 10.5), bar("d3", 10, 11, 9, 10)]
        merged = C.process_include(rows)
        self.assertLess(len(merged), len(rows))


class FractalDateTest(unittest.TestCase):
    def test_fractal_date_is_extreme_bar(self):
        rows = [
            bar("2026-01-01", 10, 11, 9, 10),
            bar("2026-01-02", 10, 12, 9.5, 11),
            bar("2026-01-03", 11, 15, 10, 14),
            bar("2026-01-04", 14, 16, 13, 15),
            bar("2026-01-05", 15, 15.5, 12, 13),
        ]
        merged = C.process_include(rows)
        fx = C.find_fractals(merged, rows)
        tops = [f for f in fx if f["type"] == "top"]
        self.assertTrue(tops, "应识别出顶分型")
        self.assertEqual(tops[0]["date"], "2026-01-04")
        self.assertEqual(tops[0]["high"], 16)


class SegmentTest(unittest.TestCase):
    def zigzag(self):
        pivots = [100, 120, 105, 130, 110, 140]
        rows, day = [], 1
        for i in range(len(pivots) - 1):
            a, b = pivots[i], pivots[i + 1]
            for k in range(12):
                p = a + (b - a) * k / 12
                rows.append(bar("2026-01-%02d" % day, p, p + 0.5, p - 0.5, p))
                day += 1
        rows.append(bar("2026-01-%02d" % day, pivots[-1], pivots[-1] + 0.5, pivots[-1] - 0.5, pivots[-1]))
        return rows

    def test_segments_alternate_and_cover_strokes(self):
        rows = self.zigzag()
        merged = C.process_include(rows)
        fx = C._seed_initial_fractal(C.find_fractals(merged, rows), merged)
        strokes, _ = C.build_strokes(fx)
        segments = C.build_segments(strokes)
        self.assertTrue(segments, "至少有 1 条线段")
        for prev, cur in zip(segments, segments[1:]):
            self.assertNotEqual(prev["dir"], cur["dir"], "线段方向必须交替")
            self.assertEqual(cur["start_stroke"], prev["end_stroke"] + 1, "线段必须首尾相接")
            self.assertGreaterEqual(prev["strokes"], 3, "线段至少三笔")

    def test_seed_keeps_first_leg(self):
        rows = self.zigzag()
        merged = C.process_include(rows)
        fx = C.find_fractals(merged, rows)
        strokes_without, _ = C.build_strokes(fx)
        fx2 = C._seed_initial_fractal(fx, merged)
        strokes_with, _ = C.build_strokes(fx2)
        self.assertGreater(len(strokes_with), len(strokes_without), "补起始分型后应多出第一笔")


class CenterTest(unittest.TestCase):
    def test_overlap_forms_center_without_widening(self):
        segs = [
            segment("up", "d1", "d2", 90, 110, 0, 1),
            segment("down", "d2", "d3", 95, 105, 2, 3),
            segment("up", "d3", "d4", 98, 112, 4, 5),
            segment("down", "d4", "d5", 96, 108, 6, 7),   # 与中枢重叠 → 延伸
        ]
        centers = C.find_centers(segs)
        self.assertEqual(len(centers), 1)
        center = centers[0]
        self.assertEqual((center["zd"], center["zg"]), (98, 105))
        self.assertEqual(center["segments"], 4, "延伸只增加段数")

    def test_non_overlap_starts_new_center(self):
        segs = [
            segment("up", "d1", "d2", 90, 110, 0, 1),
            segment("down", "d2", "d3", 95, 105, 2, 3),
            segment("up", "d3", "d4", 98, 112, 4, 5),
            segment("down", "d4", "d5", 150, 170, 6, 7),   # 完全在 ZG 之上
            segment("up", "d5", "d6", 160, 180, 8, 9),
            segment("down", "d6", "d7", 155, 175, 10, 11),
        ]
        centers = C.find_centers(segs)
        self.assertEqual(len(centers), 2)
        self.assertGreater(centers[1]["zd"], centers[0]["zg"])

    def test_nine_plus_segments_marks_upgrade(self):
        segs = []
        for i in range(11):
            direction = "up" if i % 2 == 0 else "down"
            segs.append(segment(direction, "d%d" % i, "e%d" % i, 98, 105, i * 2, i * 2 + 1))
        centers = C.find_centers(segs)
        self.assertTrue(centers[0]["upgrade"])


class SignalTest(unittest.TestCase):
    def base_segments(self):
        return [
            segment("up", "2026-01-01", "2026-01-10", 90, 100, 0, 1),
            segment("down", "2026-01-10", "2026-01-20", 92, 100, 2, 3),
            segment("up", "2026-01-20", "2026-01-30", 94, 102, 4, 5),   # 中枢 94~100
            segment("up", "2026-01-30", "2026-02-10", 104, 120, 6, 7),  # 离开段, 完全在 ZG 上
            segment("down", "2026-02-10", "2026-02-20", 105, 118, 8, 9),  # 回抽低 105 > ZG 100
        ]

    def center(self):
        return {"start_segment": 0, "end_segment": 2, "zd": 94, "zg": 100, "segments": 3,
                "up": True, "start_date": "2026-01-01", "end_date": "2026-01-30", "upgrade": False}

    def test_third_buy_candidate_without_sub_level(self):
        signals, conflicts = C.detect_signals(self.base_segments(), [self.center()], [], 110, [])
        types = {s["type"]: s for s in signals}
        self.assertIn("buy3", types)
        self.assertEqual(types["buy3"]["state"], "candidate")
        self.assertEqual(types["buy3"]["trigger"], 105)
        self.assertEqual(types["buy3"]["invalidate"], 100)
        self.assertEqual(conflicts, [])

    def test_third_buy_confirmed_with_sub_level(self):
        subs = [
            stroke("down", "2026-02-10", 118, "2026-02-15", 106, 118, 105),
            stroke("up", "2026-02-15", 106, "2026-02-20", 115, 115, 106),
        ]
        signals, _ = C.detect_signals(self.base_segments(), [self.center()], [], 110, [], subs)
        types = {s["type"]: s for s in signals}
        self.assertEqual(types["buy3"]["state"], "confirmed")

    def test_no_third_buy_when_pullback_enters_center(self):
        segs = self.base_segments()
        segs[-1] = segment("down", "2026-02-10", "2026-02-20", 96, 118, 8, 9)  # 回抽进中枢
        signals, _ = C.detect_signals(segs, [self.center()], [], 110, [])
        self.assertNotIn("buy3", {s["type"] for s in signals})

    def test_same_day_buy_and_sell_conflict_dropped(self):
        divs = [
            {"kind": "trend", "dir": "down", "center": 0, "divergence": True,
             "weak_price": True, "weak_volume": True, "weak_macd_area": False,
             "prev": {"end": "2026-02-01"}, "last": {"end": "2026-02-20", "end_price": 104}},
            {"kind": "consolidation", "dir": "up", "center": 0, "divergence": True,
             "weak_price": True, "weak_volume": True, "weak_macd_area": False,
             "prev": {"end": "2026-02-01"}, "last": {"end": "2026-02-20", "end_price": 118}},
        ]
        signals, conflicts = C.detect_signals(self.base_segments(), [self.center()], divs, 110, [])
        self.assertTrue(conflicts, "同日买卖必须记为冲突")
        for s in signals:
            self.assertNotEqual(s["date"], "2026-02-20", "冲突日的信号应被丢弃")

    def test_consolidation_divergence_is_not_buy1(self):
        divs = [{"kind": "consolidation", "dir": "down", "center": 0, "divergence": True,
                 "weak_price": True, "weak_volume": True, "weak_macd_area": True,
                 "prev": {"end": "2026-01-05"}, "last": {"end": "2026-01-25", "end_price": 95}}]
        signals, _ = C.detect_signals(self.base_segments(), [self.center()], divs, 110, [])
        types = {s["type"] for s in signals}
        self.assertNotIn("buy1", types, "盘整背驰不构成一买")
        self.assertIn("buy_short", types)


class WestockConverterTest(unittest.TestCase):
    TABLE = """| date | open | last | high | low | volume | amount |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-27 15:00:00 | 10 | 11 | 12 | 9 | 100 | 1000 |
| 2026-10-27 14:00:00 | 9 | 10 | 11 | 8 | 90 | 900 |
"""

    def test_parses_and_sorts(self):
        rows = W.parse(self.TABLE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-10-27 14:00:00")
        self.assertEqual(rows[1]["close"], 11)
        self.assertEqual(rows[1]["volume"], 100)

    def test_rejects_non_table(self):
        with self.assertRaises(SystemExit):
            W.parse("no table here")


class PositionTest(unittest.TestCase):
    def stroke_up(self, high, low):
        return {"dir": "up", "start": {"date": "d1", "high": low, "low": low},
                "end": {"date": "d2", "high": high, "low": low}}

    def test_pending_bottom_invalidated_by_close_above_start(self):
        pending = {"type": "bottom", "date": "d3"}
        text = C.describe_position(pending, [self.stroke_up(100, 90)], [], 101, [])
        self.assertIn("已失效", text)

    def test_pending_bottom_alive_below_start(self):
        pending = {"type": "bottom", "date": "d3"}
        text = C.describe_position(pending, [self.stroke_up(100, 90)], [], 99, [])
        self.assertIn("候选", text)
        self.assertNotIn("已失效", text)

    def test_pending_top_invalidated_by_close_below_start(self):
        down = {"dir": "down", "start": {"date": "d1", "high": 100, "low": 100},
                "end": {"date": "d2", "high": 60, "low": 60}}
        text = C.describe_position({"type": "top", "date": "d3"}, [down], [], 59, [])
        self.assertIn("已失效", text)

    def test_neutral_without_structure(self):
        self.assertEqual(C.describe_position(None, [], [], 100, []), "中性")


class ConfluenceTest(unittest.TestCase):
    def result(self, signals):
        return {"signals": signals}

    def sig(self, kind, date):
        return {"type": kind, "date": date, "state": "confirmed"}

    def test_aligned_when_both_buy(self):
        conf = C.compare_levels(self.result([self.sig("buy3", "d1")]),
                                self.result([self.sig("buy1", "d2")]))
        self.assertEqual(conf["state"], "aligned")
        self.assertEqual(conf["pairs"][0]["verdict"], "共振")

    def test_conflict_when_opposite(self):
        conf = C.compare_levels(self.result([self.sig("buy3", "d1")]),
                                self.result([self.sig("sell1", "d2")]))
        self.assertEqual(conf["state"], "conflict")
        self.assertEqual(conf["pairs"][0]["verdict"], "矛盾")

    def test_waiting_when_sub_has_no_signal(self):
        conf = C.compare_levels(self.result([self.sig("buy1", "d1")]), self.result([]))
        self.assertEqual(conf["pairs"][0]["verdict"], "待确认")
        self.assertEqual(conf["state"], "none")


class BacktestTest(unittest.TestCase):
    def test_summary_handles_empty(self):
        summary = C.summarize_backtest([], 20)
        self.assertEqual(summary["count"], 0)
        self.assertIsNone(summary["avg_return_pct"])

    def test_summary_counts_invalidations(self):
        records = [
            {"type": "buy1", "return_pct": 5.0, "net_return_pct": 4.9, "max_gain_pct": 8.0,
             "max_drawdown_pct": -2.0, "invalidated": False, "exit_reason": "horizon", "bars_held": 20},
            {"type": "sell1", "return_pct": -3.0, "net_return_pct": -3.1, "max_gain_pct": 1.0,
             "max_drawdown_pct": -4.0, "invalidated": True, "exit_reason": "stop", "bars_held": 7},
        ]
        summary = C.summarize_backtest(records, 20)
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["invalidated"], 1)
        self.assertEqual(summary["stop_exits"], 1)
        self.assertEqual(summary["horizon_exits"], 1)
        self.assertAlmostEqual(summary["avg_return_pct"], 1.0)
        self.assertAlmostEqual(summary["avg_net_return_pct"], 0.9)
        self.assertAlmostEqual(summary["win_rate"], 0.5)
        self.assertAlmostEqual(summary["buy_avg_return_pct"], 5.0)

    def test_backtest_runs_and_records_are_directional(self):
        rows, prices = [], 100
        for i in range(200):
            prices += 0.5 if (i // 20) % 2 == 0 else -0.4
            rows.append(bar("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), prices, prices + 1, prices - 1, prices))
        records = C.backtest_signals(rows, horizon=10, stride=5, min_bars=80, center_level="stroke")
        self.assertIsInstance(records, list)
        for record in records:
            self.assertIn(record["direction"], ("long", "short"))
            self.assertIn("return_pct", record)


class CenterTreeTest(unittest.TestCase):
    def center(self, zd, zg, start, end):
        return {"zd": zd, "zg": zg, "start_date": start, "end_date": end, "segments": 3}

    def test_overlapping_centers_merge_up(self):
        tree = C.build_center_tree([
            self.center(100, 110, "d1", "d5"),
            self.center(105, 115, "d6", "d10"),
            self.center(200, 210, "d11", "d15"),
        ])
        self.assertEqual(len(tree), 2, "两个重叠中枢应合并出 L2")
        self.assertEqual(tree[1]["level"], 2)
        self.assertEqual(tree[1]["centers"][0]["zd"], 105)
        self.assertEqual(tree[1]["centers"][0]["zg"], 110)

    def test_upgraded_center_is_promoted(self):
        big = self.center(100, 130, "d1", "d60")
        big["segments"] = 12
        tree = C.build_center_tree([big])
        self.assertEqual(len(tree), 2)
        self.assertTrue(tree[1]["centers"][0]["promoted"])
        self.assertEqual(tree[1]["centers"][0]["level"], 2)

    def test_no_overlap_keeps_single_level(self):
        tree = C.build_center_tree([self.center(100, 110, "d1", "d5"), self.center(200, 210, "d6", "d10")])
        self.assertEqual(len(tree), 1)


class CsvTest(unittest.TestCase):
    def test_header_and_row(self):
        text = C.trades_to_csv([
            {"type": "buy3", "direction": "long", "entry_price": 10, "exit_price": 11,
             "exit_reason": "horizon", "bars_held": 20, "return_pct": 10.0, "net_return_pct": 9.9},
        ])
        lines = text.strip().splitlines()
        self.assertEqual(lines[0].split(",")[0], "type")
        self.assertIn("buy3", lines[1])
        self.assertIn("9.9", lines[1])
        self.assertEqual(len(lines), 2)


class ExecutionTest(unittest.TestCase):
    def series(self):
        rows, price = [], 100.0
        for i in range(160):
            price += 0.8 if (i // 15) % 2 == 0 else -0.7
            rows.append(bar("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), price, price + 1, price - 1, price))
        return rows

    def test_fees_reduce_net_return(self):
        records = C.backtest_signals(self.series(), horizon=10, stride=20, min_bars=80,
                                     center_level="stroke", fee_rate=0.001, slippage=0.001)
        for record in records:
            if record["net_return_pct"] is not None:
                self.assertLessEqual(record["net_return_pct"], record["return_pct"])

    def test_entry_delay_uses_later_open(self):
        rows = self.series()
        records = C.backtest_signals(rows, horizon=10, stride=20, min_bars=80,
                                     center_level="stroke", fee_rate=0.0, slippage=0.0, entry_delay=2)
        self.assertIsInstance(records, list)


class SimulateTradeTest(unittest.TestCase):
    def test_buy_stop_hit(self):
        forward = [bar("d1", 100, 101, 94, 97), bar("d2", 97, 98, 90, 92)]
        trade = C.simulate_trade(100.0, forward, True, 95.0, fee_rate=0.0, slippage=0.0)
        self.assertEqual(trade["exit_reason"], "stop")
        self.assertEqual(trade["bars_held"], 1)
        self.assertAlmostEqual(trade["exit_price"], 95.0)
        self.assertAlmostEqual(trade["return_pct"], -5.0)

    def test_short_stop_hit(self):
        forward = [bar("d1", 100, 108, 99, 104)]
        trade = C.simulate_trade(100.0, forward, False, 105.0, fee_rate=0.0, slippage=0.0)
        self.assertEqual(trade["exit_reason"], "stop")
        self.assertAlmostEqual(trade["return_pct"], -5.0)

    def test_horizon_exit_with_fee(self):
        forward = [bar("d1", 100, 101, 99, 100)]
        trade = C.simulate_trade(100.0, forward, True, None, fee_rate=0.001, slippage=0.0)
        self.assertEqual(trade["exit_reason"], "horizon")
        self.assertAlmostEqual(trade["return_pct"], 0.0)
        self.assertAlmostEqual(trade["net_return_pct"], -0.2)

    def test_stop_above_entry_is_ignored_for_long(self):
        forward = [bar("d1", 100, 101, 99, 100)]
        trade = C.simulate_trade(100.0, forward, True, 120.0, fee_rate=0.0, slippage=0.0)
        self.assertEqual(trade["exit_reason"], "horizon", "多头失效价高于入场价时不应当止损")
        self.assertFalse(trade["invalidated"])

    def test_slippage_worsens_entry(self):
        forward = [bar("d1", 100, 101, 99, 100)]
        trade = C.simulate_trade(100.0, forward, True, None, fee_rate=0.0, slippage=0.01)
        self.assertAlmostEqual(trade["entry_price"], 101.0)
        self.assertAlmostEqual(trade["return_pct"], round((100 - 101) / 101 * 100, 2))


class CenterLevelTest(unittest.TestCase):
    def test_stroke_units_are_well_formed(self):
        stroke_unit = C._stroke_unit(
            {"dir": "up", "start": {"date": "d1", "high": 10, "low": 5, "i": 0},
             "end": {"date": "d2", "high": 12, "low": 9, "i": 3}}, 0)
        self.assertEqual(stroke_unit["dir"], "up")
        self.assertEqual((stroke_unit["low"], stroke_unit["high"]), (5, 12))
        self.assertEqual((stroke_unit["start_i"], stroke_unit["end_i"]), (0, 3))


def synthetic_series(count=200):
    """生成一段有涨有跌的合成日线, 供批量/网格/渲染测试复用。"""
    rows, price = [], 100.0
    for i in range(count):
        price += 0.9 if (i // 18) % 2 == 0 else -0.8
        rows.append(bar("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), price, price + 1, price - 1, price))
    return rows


class ScriptsTest(unittest.TestCase):
    def write_json(self, rows):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def test_batch_scan_writes_csv(self):
        first = self.write_json(synthetic_series(160))
        second = self.write_json(synthetic_series(180))
        handle, out = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        code = B.main([first, second, "--center-level", "stroke", "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["target"].split(".")[-1], "json")
        self.assertEqual(rows[0]["error"], "")

    def test_batch_scan_reports_error_per_file(self):
        handle, out = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        bad = self.write_json("not a list")
        code = B.main([bad, "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(rows[0]["error"])

    def test_param_sweep_grid_size(self):
        path = self.write_json(synthetic_series(200))
        handle, out = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        code = P.main([path, "--center-levels", "stroke", "--entry-delays", "1,2",
                       "--horizons", "5", "--stale-bars", "10", "--stride", "40",
                       "--min-bars", "80", "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)

    def test_render_chart_svg(self):
        path = self.write_json(synthetic_series(200))
        handle, out = tempfile.mkstemp(suffix=".svg")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        code = R.main([path, "--center-level", "stroke", "--out", out])
        self.assertEqual(code, 0)
        import xml.etree.ElementTree as ET
        root = ET.parse(out).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        rects = [element for element in root.iter() if element.tag.endswith("rect")]
        texts = [element for element in root.iter() if element.tag.endswith("text")]
        self.assertGreater(len(rects), 200)
        self.assertGreater(len(texts), 5)


class ReportTest(unittest.TestCase):
    def write_json(self, rows):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def test_report_contains_sections_and_svg(self):
        path = self.write_json(synthetic_series(200))
        handle, out = tempfile.mkstemp(suffix=".html")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        handle, md = tempfile.mkstemp(suffix=".md")
        os.close(handle)
        self.addCleanup(os.unlink, md)
        code = M.main([path, "--center-level", "stroke", "--out", out, "--md", md])
        self.assertEqual(code, 0)
        page = open(out, encoding="utf-8").read()
        for heading in ("结构", "买卖点", "关键价位", "风险提示"):
            self.assertIn("<h2>%s</h2>" % heading, page)
        self.assertIn("<svg", page)
        self.assertIn("不构成投资建议", page)
        self.assertTrue(open(md, encoding="utf-8").read().startswith("# "))

    def test_report_escapes_title(self):
        path = self.write_json(synthetic_series(200))
        handle, out = tempfile.mkstemp(suffix=".html")
        os.close(handle)
        self.addCleanup(os.unlink, out)
        M.main([path, "--center-level", "stroke", "--title", "<script>x</script>", "--out", out])
        page = open(out, encoding="utf-8").read()
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_interactive_html_has_layers(self):
        page = R.render_html("<svg></svg>", "标题")
        for layer in ("layer-strokes", "layer-segments", "layer-centers1",
                      "layer-centers2", "layer-signals", "layer-levels"):
            self.assertIn(layer, page)
        self.assertIn("toggleLayer", page)


class AnalyzeIntegrationTest(unittest.TestCase):
    def test_pipeline_on_synthetic_series(self):
        pivots = [100, 120, 105, 130, 110, 140, 120, 150]
        rows, day = [], 1
        for i in range(len(pivots) - 1):
            a, b = pivots[i], pivots[i + 1]
            for k in range(10):
                p = a + (b - a) * k / 10
                rows.append(bar("2026-01-%02d" % min(day, 28), p, p + 0.5, p - 0.5, p))
                day += 1
        rows.append(bar("2026-02-01", pivots[-1], pivots[-1] + 0.5, pivots[-1] - 0.5, pivots[-1]))
        result = C.analyze(rows, "synthetic")
        for key in ("fractals", "strokes", "segments", "centers", "divergences",
                    "signals", "conflicts", "levels", "position", "method"):
            self.assertIn(key, result)
        for center in result["centers"]:
            self.assertGreater(center["zg"], center["zd"])
        self.assertTrue(result["levels"]["support"])
        json.dumps(result, ensure_ascii=False)  # 必须可序列化


if __name__ == "__main__":
    unittest.main(verbosity=2)
