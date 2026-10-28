# 缠论分析工作流(实操 checklist)

> 每次分析都按本流程执行，保证结论可复现、不遗漏。

## Step 0 — 明确标的与级别

- [ ] 确认标的代码与名称; 用户未指定级别时**默认日线为主级别**，
      辅以 30/60 分钟联立; 长线用户加周线。
- [ ] 确认数据区间: 至少覆盖"当前进行中一笔的起点"往前 1–2 个完整波段
      (经验值: 日线 ≥120 根，最好 200+ 根，保证 MACD 与中枢计算稳定)。
- [ ] **必须用前复权数据**（跨除权日不复权会让分型/背驰全部失真）。

## Step 1 — 取数(a-stock-data skill)

```python
# 典型取数(日线, 前复权)
from tencent_kline import tencent_kline   # a-stock-data skill §1.2
df = tencent_kline("600989", start="2025-08-01", adjust="qfq")
df.to_json("/tmp/chanlun/xxx.json", orient="records")
# 分钟级别: period="m30" / "m60"(最近 ≤320 根, 不复权)
```

- 港股/美股/指数同理取日线；无成交量口径的源也要尽量补上 volume 字段(背驰三指标之一)。
- 保存字段: `date, open, high, low, close, volume`。
- 次级别数据(区间套用)：`westock kline CODE --period m30 --start S --end E > m30.md`
  再 `python3 scripts/westock_kline_to_json.py m30.md > m30.json`，喂给 `--sub-level`。

## Step 2 — 机械化识别

```bash
python3 scripts/chanlun_analyze.py /tmp/chanlun/xxx.json --name "标的" --out /tmp/chanlun/result.json
```

可选参数:

| 参数 | 作用 |
|---|---|
| `--sub-level PATH` | 次级别 K 线（如 30 分钟）：三买/三卖/二买改用它做区间套确认，`state` 才会是 `confirmed`；同时输出多级别联立 |
| `--center-level segment\|stroke` | 中枢构件。默认 `segment`（线段级，原文口径）；250 根日线常凑不够 3 段，短数据可降级 `stroke`（笔级，噪声更大） |
| `--history` | 滑动窗口回溯历史信号，按"首次识别"记账并统计其后表现 |
| `--history-horizon N` | 回溯前瞻根数，默认 20 |
| `--history-stride N` | 回溯滑动步长，默认 1（逐根） |
| `--history-min-bars N` | 从第几根开始回溯，默认 120 |
| `--history-stale-bars N` | 信号日期距识别时点超过 N 根即丢弃（陈旧信号），默认 20 |
| `--history-fee R` | 单边手续费率，默认 0.0005（双边合计计入净收益） |
| `--history-slippage R` | 单边滑点，默认 0.0005（买价上浮、卖价下调） |
| `--history-entry-delay N` | 识别后第 N 根开盘入场，默认 1（避免用识别当根收盘偷跑） |
| `--history-out PATH` | 回溯明细导出 CSV（收益/回撤/出场原因/是否止损） |
| `--last N` | 文本摘要只显示最近 N 条笔/线段/中枢/背驰（默认 8，`0` 显示全部）；完整结果始终在 `--out` 的 JSON 里 |
| `--min-bars N` | 最少 K 线根数，默认 60 |
| `--allow-short` | 少于 60 根也分析，仅用于试跑；会打印警告 |

- 脚本会拒绝执行并给出可读错误（退出码 2）的情况：文件缺失/非 UTF-8/非法 JSON、
  顶层不是数组、缺 `date/open/high/low/close`、OHLC 非数字、`high < low`、日期重复、
  根数少于 `--min-bars`。
- 只警告不阻断：缺 `volume`（背驰量能比较会失真）、OHLC 与开收不自洽、根数不足但加了 `--allow-short`。
- 分型日期取**极值所在原始 K 线**的日期（合并区间可能跨几根），报告里的日期可以直接引用。
- [ ] 检查输出合理性: 笔数(日线 200 根通常 8–20 笔)、线段数(3–8 条)、中枢数(1–4 个)；
      量不为 0(为 0 说明取数或索引有问题)；日期无倒流(终点不早于起点)。
- [ ] 线段是否 `未确认`：末段未出现特征序列分型时会被标未确认，报告必须说明它可能被延伸。
- [ ] 中枢段数：接近或超过 9 段会标 `⚠级别升级`，此时本级别中枢即将升级，按大级别看。
- [ ] 记录关键量: 最后中枢 ZD/ZG、最后一笔/线段起止、背驰 `kind`(trend/consolidation)、
      买卖点 `type` 与 `state`。

## Step 3 — 结构化解读(按序回答)

1. **走势分解**: 用中文描述每一笔("1–3 月一波 +79% 的主升"，
   "3 月高点至 7 月低点 -62% 的主跌")，不要只报数字。
2. **当前位置**: 现价相对最后中枢(上/内/下)、当前一笔是否确认、
   处于什么阶段(上涨初段/上涨末端背驰/下跌中继/中阴震荡)。
3. **背驰与买卖点**: 只看 `kind=trend` 的背驰谈一买/一卖；`consolidation` 只说明回抽中枢。
   每个信号都带 `state`:
   - `confirmed`：有次级别数据并已确认，可直接执行；
   - `candidate`：形态成立但缺次级别确认，等回抽完成或补数据；
   - `approx`：无次级别数据、用本级别近似，只能作为观察。
   同时检查 `conflicts`：非空说明处在临界状态，当日不给方向。
   **没有信号是正常结果**，不要为了"有结论"降低口径。
4. **级别联立**: 看输出的 `multi_level`:
   - `共振`：大级别与次级别同向买卖点 → 置信度最高；
   - `矛盾`：小级别反向 → 以大级别为准，小级别信号只当回踩/反弹，不反手；
   - `待确认`：大级别有信号、次级别没有 → 等次级别回踩确认再动手。
5. **关键价位清单**(必须给出):
   - 支撑: 最近底分型低点、最后中枢 ZD、前低；
   - 阻力: 最近顶分型高点、最后中枢 ZG、前高；
   - 买卖点触发价与失效价。

## Step 4 — 输出报告(结构模板)

```
一、走势结构复盘        —— 笔的分解 + 一句话概括每笔
二、当前位置判断        —— 走势类型/阶段 + 中枢关系
三、背驰与买卖点        —— 背驰 kind + 三指标对比表 + 买卖点(type/state/触发/失效)
四、级别联立(可选)      —— 小级别同向/矛盾
五、关键价位与操作策略  —— 支撑/阻力/触发价/止损价 + 仓位建议
六、风险提示            —— 笔未确认、小转大、背驰延长、级别混淆
```

## 常见误区(自查清单)

- [ ] 未做包含处理直接画笔 → 分型密度虚高、笔数虚多；
- [ ] 把"有重叠"当"包含" → 过度合并、笔结构丢失(脚本已按严格口径处理)；
- [ ] 一笔未确认就当下跌结束 → 被延伸打脸；必须等反向分型确认；
- [ ] 拿日线背驰做 5 分钟级别的止损 → 级别混淆，止损必须同级别或更小级别；
- [ ] 只看 MACD 金叉死叉当背驰 → 背驰是**两段同向走势的力度比较**，不是单次交叉；
- [ ] 盘整背驰当趋势背驰用 → 前者只保证回抽中枢，不保证转折；
- [ ] 不复权数据做长区间分析 → 除权日处结构全错；
- [ ] 结论只给"看涨/看跌" → 必须给触发条件与失效条件(完全分类)。

## 历史信号回溯(评估口径用)

```bash
python3 scripts/chanlun_analyze.py day.json --history --history-horizon 20
```

- 按"识别时点"记账（不是事后重新标注）；入场价取识别后 `entry_delay` 根的开盘价（默认下一根），
  并计入双边手续费 `fee` 与滑点 `slippage`；
- 买点按做多、卖点按做空折算，收益为正=信号方向赚钱；
- 出场：先触发 `invalidate` 按失效价止损，否则持有 `horizon` 根按收盘出场；
  失效价必须在入场价的正确一侧（多头低于入场、空头高于入场），否则忽略止损；
  一买/一卖的失效价就是背驰段极值，三买/三卖是中枢边界，盘整背驰是该段极值；
- 同时给最大浮盈/最大回撤、毛收益与净收益、是否止损及第几根触发；
- 结果为 `history.records`（逐条）+ `history.summary`（按类型计数与均值）；
- 样本少时（<10 条）不要据此下结论，只用来检查口径是否自洽。

## 批量 / 参数 / 出图

```bash
# 多标的: 同一套参数, 输出汇总 CSV
python3 scripts/batch_scan.py --symbols sh600519,hk00700,sz300750 --fetch \
  --center-level stroke --history --out summary.csv

# 参数矩阵: 多标的 × 多参数, 看参数是否稳定(为正的标的数 → 平均净收益)
python3 scripts/param_sweep.py --dir /tmp/chanlun '*.json' --center-levels segment,stroke \
  --entry-delays 1,2 --horizons 10,20 --stale-bars 10,20 --out matrix.csv --detail-out detail.csv

# 出图: SVG 或可交互 HTML(图层开关 + 悬停提示)
python3 scripts/render_chart.py day.json --center-level stroke --last 300 --out chart.svg
python3 scripts/render_chart.py day.json --center-level stroke --last 300 --html chart.html

# 一键报告(HTML 自带图, 另出 Markdown)
python3 scripts/make_report.py day.json --center-level stroke --sub-level m30.json \
  --history --title "标的 日线" --out report.html --md report.md
```

- 批量扫描单标的出错只记一行 `error`，不影响整批；
- 参数网格的排序只反映这段样本，样本 <10 笔时不要当结论；
- SVG 里的数字与 JSON 同源，报告直接引用，不手改。

## 与可视化报告的结合

- 用脚本输出的 `fractals`/`strokes`/`centers` 在 K 线图上标注:
  分型(小三角)、笔(起止连线+箭头)、中枢(ZD–ZG 矩形框)、背驰段(高亮)。
- 报告中的价位必须与脚本输出一致，禁止手改数字。
