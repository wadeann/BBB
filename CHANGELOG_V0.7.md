# CHANGELOG v0.7.0

## 1. 全市场动态 Point-in-Time 股票池

旧版本：周期开始时获得一个固定 `symbols`，整个回测周期重复扫描。

v0.7：每个 benchmark 交易日调用 `HistoricalDataProvider.active_records_on(date)`，当天 universe 独立重建。

修复的偏差：

- 今天仍上市的股票不再代表两年前股票池；
- 后来退市的股票可以保留在其历史有效期内；
- 未来才上市的股票不会提前出现在历史候选池；
- 历史 ST/停牌/退市整理状态可按有效期过滤。

## 2. 历史 Universe 双路径

新增：

- interval fast path；
- daily paginated PIT path；
- 每日 universe 缓存；
- `dataset_version / coverage / universe_hash` 记录；
- strict 模式禁止 silent fallback。

## 3. 历史 Sector Router

新增 `sector_info_on(symbol, as_of)`：

- 优先 historical universe 内行业字段；
- 支持历史 sector membership；
- 支持有效区间缓存；
- current F10 只作为非严格诊断 fallback；
- 报告历史 sector mapping coverage。

## 4. 合并 Gemini commit e2d700a 的 LLM structured-output 修复

合并内容：

- Prompt 明确根对象必须为 `{ "decisions": [...] }`；
- `structured_output=json_schema`；
- 兼容供应商输出 `candidates` 根字段；
- 兼容 `candidate_id -> object` map；
- `confidence 0..1` 自动转成 `0..100`；
- Research profile：`timeout=180s, retries=1, max_tokens=4096`。

额外保留 v0.6.1 安全修复：

- JSON 本地 Schema 强校验；
- LLM transport/schema failure = `ERROR`，绝不自动转成 `REJECT`；
- LLM 实验只有 `failures=0 && error_candidates=0` 才可解释；
- compact historical features；
- 匿名 security_id；
- LLM 只审核有机会占用组合槽位的候选。

## 5. 全市场性能优化

- 价格历史使用 bisect，仅切最近120根给信号引擎；
- Sector Context 按 `(sector_code, date)` 缓存；
- 历史 sector membership 有有效期时按 symbol 缓存；
- K线和每日 universe 落盘缓存。

## 6. Research Grade 判定加强

新增要求：

- `point_in_time_universe_all_days=true`
- `dynamic_universe_daily=true`
- 历史行业映射覆盖率阈值
- LLM error/failure 硬判无效

## 未做的事情

v0.7 **没有**因为收益目标而：

- 把单票仓位提高到30%~40%；
- 把总仓提高到80%~100%；
- 自动删除所有亏损策略；
- 把月收益30%设为强制交易KPI。

这些必须先通过全市场 PIT 回测、Walk-Forward 和 Paper Shadow。
