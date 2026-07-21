# ETF 跨行业轮动 (C01 冻结版)

模块化工程。生产策略 **C01_效15d** 已冻结，至少跑满 3 个月再调参。

## 目录

```
etf-rotation/
├── config/           # 策略与ETF池 (JSON)
│   ├── c01.json      # 生产主策略 (frozen)
│   ├── c13_shadow.json
│   └── pool.json
├── etf_rotation/     # Python 包
│   ├── data.py       # 取数
│   ├── factors.py    # 因子/打分
│   ├── backtest.py   # 回测引擎
│   ├── portfolio.py  # 模拟账户
│   ├── signal.py     # 信号主流程
│   ├── report.py     # 报告输出
│   ├── config.py
│   └── paths.py
├── scripts/
│   ├── run_signal.py     # 生产信号
│   ├── run_backtest.py   # 只读回测验证
│   └── run_daily.sh      # cron 入口
├── archive/          # 旧脚本与历史实验
└── docs/
```

运行时输出 (便于人工查看):

```
~/桌面/ETF轮动信号/
├── latest.txt        # 固定入口
├── latest.json
├── 模拟仓位.json
├── etf信号_*.txt
├── 迭代记录.md
└── logs/
```

## 日常使用 (统一入口)

```bash
cd /path/to/etf-rotation
export PYTHONPATH=.

# 今日一页速览 (动作 + live/xs/THIN) · 站点: output/site/today.html
python3 scripts/etf.py today

# 过旧时一键刷信号 (不改生产仓)
python3 scripts/etf.py refresh
# DATA_LAG=wall日>行情截至; live/xs 以 asof 为准
python3 scripts/etf.py check --checks data_asof
python3 scripts/etf.py asof   # 行情截至/DATA_LAG/live 取证 (仅 lag 时优先于 refresh)
python3 scripts/etf.py yield  # 有效收益 live%/xs%/THIN
python3 scripts/etf.py open --launch site  # 打开面板
python3 scripts/etf.py brief  # 三合一速览 yield+asof+today
python3 scripts/etf.py data   # 行情状态 asof/DATA_LAG/决策
python3 scripts/etf.py next   # 唯一推荐下一步
python3 scripts/etf.py pull   # 强刷行情缓存并复检 asof
python3 scripts/etf.py wait-asof --timeout 600  # 轮询直到 asof 推进
python3 scripts/etf.py go --timeout 600        # 一键闭环
python3 scripts/etf.py ready                  # 有效收益是否可判
./etf                                        # 默认 digest (免 PYTHONPATH)
./etf go --timeout 600                       # 等 asof 推进并重算有效收益
./etf eod --timeout 1800                     # 收盘后一键: 等 asof + daily + digest
./etf pulse --quiet                          # 收盘后/巡检: 单行可判摘要
./etf pulse                                  # 一键脉搏 data+ready+ETA
./etf progress                               # 可判性轨迹 Lrets→READY
./etf progress --json                        # 轨迹 JSON (脚本)
./etf pull --bench-only                      # 强刷; 推进则 follow+progress
# 示例 cron (交易日 15:30 后, 按需改时区/路径):
# 30 15 * * 1-5  cd /path/to/etf-rotation && ./etf eod --timeout 2400 >>logs/eod.log 2>&1
python3 scripts/etf.py digest                 # 人读一页摘要
python3 scripts/etf.py next --wait             # 决策后自动 wait-asof
# 日更默认 steps 以 pull 开头: pull,signal,...,next,go,ready,digest,email,pages
# 日更默认 steps 含 asof: signal,...,today,asof,yield,brief,data,next,email,pages

python3 scripts/etf.py doctor
python3 scripts/etf.py check          # 研究健康 (默认 quick; THIN 不失败)
# python3 scripts/etf.py check --full

# 状态 / 监控 / 暖机
python3 scripts/etf.py status
python3 scripts/etf.py monitor
python3 scripts/etf.py warmup --tail 120
# 主线影子对照 (sh/dd/live%/净值) — 日更也会自动跑
python3 scripts/etf.py compare
# 面板对照页: output/site/compare.html

# live 段真实收益 + 相对基准超额 (暖机末→今)
python3 scripts/etf.py live
# 面板: output/site/live.html · xs%=live%-基准同期

# 影子仓位摘要 (live/xs/持仓)
python3 scripts/etf.py summary

# 日更预演 (不改生产仓; 含 compare+live+summary+today)
python3 scripts/etf.py daily --dry-run

# 刷新面板 + 对照 + 状态 (本地预览路径)
python3 scripts/etf.py preview
# 打开 output/site/index.html · compare.html

# 仅信号 / 邮件预览 (含主线对照)
python3 scripts/etf.py signal --dry-run
python3 scripts/etf.py email-preview --append-all
```

固定阅读入口:
- 信号: `output/latest.txt` (或桌面 `ETF轮动信号/latest.txt`)
- 面板: `output/site/index.html`
- 生产仓: `output/模拟仓位.json` · 研究影子: `output/shadow_states/`

生产策略 **c01 冻结**; 研究主线影子默认 `c01_q10_vt08_soft_oh38_xgn` (黄金+纳指 extra, 信号侧已对齐取数)  
对照: `c01_q10_vt08_soft_oh38` / `vt09_oh35` / `vt11` · 成本敏感: `python3 scripts/cost_sensitivity.py`

## Cron (本机, 可选)

```
30 15 * * 1-5 /path/to/etf-rotation/scripts/run_daily.sh
```

## 云端定时 + 邮件 (推荐)

用 **GitHub Actions** 每个交易日 15:35(北京) 跑信号并发邮件, 本机可关 cron。

详见: [docs/云端定时与邮件.md](docs/云端定时与邮件.md)

摘要:
1. 推送到 GitHub
2. 配置 Secrets: `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_TO`
3. (可选) `MAIL_PAGES_URL` 邮件内嵌面板链接
4. Actions 里手动 Run workflow 试一次
5. (可选) 开 Pages: Settings → Pages → Source = GitHub Actions

## 升级约定

| 改什么 | 放哪 |
|--------|------|
| 调参 | `config/*.json` (C01 冻结期间勿改 weights/rb) |
| 加/减 ETF | `config/pool.json` |
| 信号逻辑 | `etf_rotation/signal.py` / `portfolio.py` |
| 因子 | `etf_rotation/factors.py` |
| 回测 | `etf_rotation/backtest.py` |
| 取数源 | `etf_rotation/data.py` |

## 策略摘要 (C01)

- 得分: `0.6 * z(eff) + 0.4 * z(mtf)`
- 调仓: 15 **交易日** (与回测对齐; 旧版误用自然日)
- 趋势: 沪深300 > MA20
- 止损: -8% · 迟滞 20% · 最小持仓 5 交易日
- 过热: 20 日涨幅 > 30% 跳过
- 回测默认成交: T+1 开盘 (`next_open`)

## 池子

| 文件 | 说明 |
|------|------|
| `config/pool.json` | **生产默认 = 去重版** (一赛道一只, ~52) |
| `config/pool_full.json` | 原多票池备份 (~72, 同主题可重复) |
| `config/pool_dedup.json` | 与生产池同内容 (对照别名) |
| `config/pool_quality.json` | 报告优选工具版 (实验, 未作生产) |

## 对照实验 A/B/C/D 是什么

`compare_improvements.py` 里的标签 (不是四套独立上线策略):

| 标签 | 策略参数 | 池子 | 成交假设 | 含义 |
|------|----------|------|----------|------|
| **A** | C01 冻结权重 | 原多票 `pool_full` | **收盘成交** | 旧乐观回测上界 |
| **B** | C01 冻结权重 | 原多票 `pool_full` | **T+1 开盘** | 可执行基线 (同参数) |
| **C** | C01 冻结权重 | **去重池** | T+1 开盘 | 只换池、不改因子 |
| **D** | `c01_improved` | 去重池 | T+1 开盘 | 方向效率+绝对动量 |
| **E** | `c01_improved` | 原多票 | T+1 开盘 | 改进因子+旧池 |

要点:
- **A vs B**: 只改成交假设 (close → next_open)
- **B vs C**: 只改池子 (多票 → 一赛道一只)
- **C vs D**: 只改因子规则 (无方向 eff → 方向 eff + 要求 m20>0)
- 生产当前: **C01 参数 + 去重池 + empty_free_entry** (空仓可立即再入)
- 对照 `c01_park.json`: 行业弱于300时停靠底仓 (实验; 长样本总收益通常低于纯C01)

### 为何有的年份跑输300?

长样本总体仍可跑赢 510300; 分年跑输常见于:
1. **趋势过滤空仓** — 300慢牛/结构牛时现金拖累
2. **单票行业轮动** — 宽基涨、行业主题不涨或轮动踩错
3. **旧逻辑空仓锁15日窗** — 已默认 `empty_free_entry=true` 修复

**不要**为「每年都赢300」去硬叠 `prefer_bench`: 对照显示会砍掉小牛段超额.
可接受(生产 C01): 熊市少亏、结构年可能跑输、主题年大赢.

### 全天候研究线 (C01_AW, 非生产)

目标: **结构牛/宽基年少输300**, 主题年保留超额, 并尽量经得起 10 年+ 压力测试. 生产 C01 继续冻结.

| 配置 | 文件 | 要点 | 角色 |
|------|------|------|------|
| **C01** | `c01.json` | eff0.6+mtf0.4, top1 | 生产冻结 |
| C01_AW_v3 | `c01_aw_v3.json` | pure_eff×2 + bm0.3 + soft | 短样本 ETF 稳健 |
| **C01_AW_v4** | `c01_aw_v4.json` | **C01权重 + prefer300 + soft + bm0.3** | **长周期主候选** |
| C01_park | `c01_park.json` | 弱行业停靠300 | v4 前身 |

#### 短样本 ETF (~2023-11~2026-07 核心池)

| 策略 | 收益 | 回撤 | 分年超额 | 最差年超额 |
|------|-----:|-----:|:--------:|----------:|
| C01 | +123% | -14.8% | 2/3 | -2.1% |
| **AW_v3** | +122% | **-11.0%** | **3/3** | **+7.0%** |
| AW_v4 | +166% | -17.3% | 2/3 | -4.7% |

#### 长历史股票代理 (本地缓存)

两套代理池交叉验证:
- `pool_long_proxy` : 24 只, 2013-05~2026-07
- `pool_long_proxy_v2`: 27 只一赛道一只, 2013-07~2026-07

| 策略 | 代理v1 收益/beat | 代理v2 收益/beat | 短ETF核心 beat | 角色 |
|------|----------------:|-----------------:|:--------------:|------|
| C01 | +172% / 8/14 | +489% / 3/11 | 2/3 | 生产冻结 |
| AW_v3 | +73% / 8/14 | +255% / 4/11 | **3/3** | 短样本稳健 |
| **AW_v4** | **+314% / 11/14** | **+784% / 6/11** | 2/3 | **长周期主候选** |
| AW_v4.1(stop-10%) | +272% / 11/14 | +1072% / 6/11 | 2/3 | 扩池更猛, 迁移一般 |

研究结论:
1. **ETF 短历史不够**, 长周期必须用股票/宽基代理压力测试
2. 跨代理池后, **v4 底仓卫星** 仍是最稳的长周期方向
3. 短样本最优 v3(持2) 与长周期最优 v4(底仓/相对强弱) 不是同一套
4. 任何版本都做不到每年赢 300; 目标是提高赢年比例、降低现金拖累
5. 生产继续冻结 C01; v4/v4.1 仅研究影子

#### 风险与质量现状 (务必区分)

1. **降仓 (DEF30/40)**: 只改账户风险预算, 夏普/卡玛几乎不变, **不算策略变强**.
2. **同仓位真质量 (Q 线)**: 固定 `ps=0.95`, 靠 `rb/overheat/inv_vol` 抬夏普与卡玛.

| 策略 | 仓位 | 年化 | 回撤 | 夏普 | 卡玛 | 用途 |
|------|-----:|-----:|-----:|-----:|-----:|------|
| C01 | 95% | +10.0% | -33.2% | 0.52 | 0.30 | 生产冻结 |
| AW_v4 | 95% | +14.6% | -33.0% | 0.67 | 0.44 | 进攻研究 |
| **Q10** | 95% | +23.1% | -27.8% | 0.97 | 0.83 | 同仓质量基线 |
| **Q10_vt11** | 波动目标11% | +23.6% | -22.2% | 1.22 | 1.06 | **防过拟合主候选 (14/14)** |
| **Q10_vt08_soft_oh38** | soft+vt8+oh38 | +21.6% | **-13.3%** | **1.36** | **1.62** | **低回撤双端新主 (V29)** |
| **Q10_vt09_soft_oh40** | soft状态+vt9+oh40 | +21.6% | **-13.9%** | 1.30 | **1.55** | regime 双端 (V28) |
| **Q10_vt09_oh35** | vt9% + oh0.35 | +23.6% | **-17.6%** | **1.33** | **1.34** | **无状态机质量最优** |
| Q10_vt08_soft_oh40 | soft+vt8+oh40 | +21.6% | -13.3% | 1.35 | 1.62 | vt08 近同构对照 |
| Q10_vt09_flip_oh35 | flip状态+vt9+oh35 | +19.2% | -13.8% | 1.23 | 1.40 | riskoff 空仓对照 |
| Q10_vt09 | 波动目标9% | +23.3% | -21.6% | 1.29 | 1.08 | 双端候选 |
| Q10_vt11_oh35 | vt11 + oh0.35 | +22.7% | -18.0% | 1.20 | 1.26 | 回撤优于 vt11 |
| Q10_vt13 | 波动目标13% | +26.8% | -22.8% | **1.27** | **1.17** | 全样本更美, 多状态集中 |
| Q10_vt15 | 波动目标15% | +24.0% | -23.0% | 1.14 | 1.04 | 对照 |
| Q10_rg_flip | 状态现金 | +22.9% | -27.0% | 1.04 | 0.85 | 观察 |

**防过拟合硬验收** (样本外+前向+多状态): 长代理 14/14 族含 VT11 / VT09_OH35 / VT08_SOFT 等.
晋级另需 **ETF 宽松档** (`--preset etf_soft`); ETF 全套 14 项对短样本常结构性不合格.
```bash
python3 scripts/validate_robust.py --preset long_proxy --gate --require-pass c01_q10_vt11
python3 scripts/validate_robust.py --preset etf_soft --strategies c01_q10_vt11,c01_q10_vt08_soft_oh38
# 成本敏感性
python3 scripts/cost_sensitivity.py
# 研究状态面板 + 日更可视化 / HTML 邮件
python3 scripts/research_status.py
python3 scripts/build_pages.py --out output/site
python3 scripts/send_email.py --append-shadow --append-alerts --append-status --dry-print
# 预览: output/site/index.html (净值曲线+动作时间线) · output/email_preview.html
python3 scripts/run_pipeline.py --dry-run --require-trading-day --monitor-fail-on-alert --steps signal,monitor,email,status,pages --pages-out output/site --append-shadow-email
python3 scripts/research_healthcheck.py --quick
python3 scripts/research_healthcheck.py --checks etf_soft --skip-warmup
python3 scripts/run_weekly.py --quick




```bash
# 短样本 ETF 对照
python3 scripts/compare_allweather.py --count 640
# 长历史股票代理 (磁盘缓存)
python3 scripts/run_long_proxy.py --count 3200 --adjust none
# 同仓质量候选
python3 scripts/run_long_proxy.py --strategies c01,c01_aw_v4,c01_q10,c01_q8 --count 3200 --adjust none
python3 scripts/run_backtest.py --strategy c01_q10 --count 640 --fill next_open
# 单策略
python3 scripts/run_backtest.py --strategy c01_aw_v4 --count 640 --fill next_open
```



### 常用命令

```bash
# A/B/C/D 对照
python3 scripts/compare_improvements.py --count 500
# 全天候对照 (C01 vs AW)
python3 scripts/compare_allweather.py --count 640
# 长历史股票代理 (磁盘缓存, 10年+)
python3 scripts/run_long_proxy.py --count 3200 --adjust none
# 池子对照 (含 --align)
python3 scripts/compare_pools.py --count 500 --align
# 生产信号 (默认去重池)
python3 scripts/run_signal.py --dry-run
# 回测
python3 scripts/run_backtest.py --strategy c01 --fill next_open
python3 scripts/run_backtest.py --strategy c01_aw_v4 --fill next_open
```
