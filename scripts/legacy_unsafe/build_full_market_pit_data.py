import json
import csv
from pathlib import Path
from collections import defaultdict

def safe_sym(sym: str) -> str:
    return sym.replace("/", "_").replace(".", "_").replace(":", "_")

def main():
    root = Path(__file__).resolve().parent.parent
    cache_bars_dir = root / "data" / "backtest" / "cache" / "bars"
    cache_sectors_dir = root / "data" / "backtest" / "cache" / "sectors"
    cache_bars_dir.mkdir(parents=True, exist_ok=True)
    cache_sectors_dir.mkdir(parents=True, exist_ok=True)

    sec_master_path = root / "data" / "backtest" / "security_master.csv"

    # 1. Existing security master entries
    existing_master = {}
    if sec_master_path.exists():
        with sec_master_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol")
                if sym:
                    existing_master[sym] = row

    # 2. Industry mapping
    INDUSTRY_CODE_MAP = {
        "交通运输": "BK1210",
        "传媒": "BK0486",
        "农林牧渔": "BK0433",
        "医药": "BK1216",
        "商贸零售": "BK1213",
        "国防军工": "BK0474",
        "基础化工": "BK1206",
        "家电": "BK0456",
        "建材": "BK1208",
        "建筑": "BK1209",
        "房地产": "BK0451",
        "有色金属": "BK0478",
        "机械": "BK0457",
        "汽车": "BK1211",
        "煤炭": "BK0437",
        "电力及公用事业": "BK0427",
        "电力设备": "BK1200",
        "电子": "BK1037",
        "石油石化": "BK0464",
        "纺织服装": "BK0436",
        "综合": "BK1217",
        "计算机": "BK1207",
        "轻工制造": "BK0440",
        "通信": "BK1215",
        "钢铁": "BK0479",
        "银行": "BK1283",
        "非银行金融": "BK1203",
        "食品饮料": "BK0438",
        "餐饮旅游": "BK0485",
        "制造与科技": "BK0001",
    }

    # Load Astock full_universe_raw.json
    universe_path = Path("/home/wade/workspace/ai/Astock/data/full_universe_raw.json")
    with universe_path.open("r", encoding="utf-8") as f:
        stock_industries = json.load(f)

    # Load Astock kline_cache.json
    kline_path = Path("/home/wade/workspace/ai/Astock/data/kline_cache.json")
    with kline_path.open("r", encoding="utf-8") as f:
        kline_cache = json.load(f)

    print(f"Loaded {len(stock_industries)} universe mappings and {len(kline_cache)} kline entries.")

    # 3. Process bars for each stock and save to cache/bars
    clean_kline_map = {}
    industry_to_stocks = defaultdict(list)
    stock_to_industry_info = {}

    for k, raw_bars in kline_cache.items():
        sym = k.split("_")[0]
        ind_name = stock_industries.get(sym)
        if not ind_name:
            if sym in existing_master:
                ind_name = existing_master[sym].get("industry_name") or "制造与科技"
            else:
                ind_name = "综合"

        ind_code = INDUSTRY_CODE_MAP.get(ind_name, "BK1217")
        stock_to_industry_info[sym] = (ind_code, ind_name)
        industry_to_stocks[ind_code].append(sym)

        norm_bars = []
        for b in raw_bars:
            d = b.get("time") or b.get("date")
            o = float(b.get("open", 0))
            c = float(b.get("close", 0))
            h = float(b.get("high", max(o, c)))
            l = float(b.get("low", min(o, c)))
            v = float(b.get("volume", 0))
            amt = float(b.get("amount", 0))
            pct = float(b.get("change_pct", b.get("pct", 0)))
            if d and o > 0 and c > 0:
                norm_bars.append({
                    "date": str(d), "open": o, "high": h, "low": l, "close": c,
                    "volume": v, "amount": amt, "pct": pct
                })
        norm_bars.sort(key=lambda x: x["date"])
        clean_kline_map[sym] = norm_bars

        # Write to cache/bars/{safe_sym}.json
        bar_file = cache_bars_dir / f"{safe_sym(sym)}.json"
        if not bar_file.exists() or bar_file.stat().st_size < 100:
            bar_file.write_text(json.dumps(norm_bars, ensure_ascii=False), encoding="utf-8")

    print(f"Processed bars for {len(clean_kline_map)} symbols.")

    # 4. Synthesize sector bars for each industry code
    all_dates = sorted({b["date"] for bars in clean_kline_map.values() for b in bars})
    print(f"Total trading dates: {len(all_dates)} from {all_dates[0]} to {all_dates[-1]}")

    # Build date -> symbol -> bar lookup
    date_sym_bar = defaultdict(dict)
    for sym, bars in clean_kline_map.items():
        for b in bars:
            date_sym_bar[b["date"]][sym] = b

    # Synthesize for all codes in INDUSTRY_CODE_MAP
    for ind_name, ind_code in INDUSTRY_CODE_MAP.items():
        symbols_in_ind = industry_to_stocks.get(ind_code, [])
        if not symbols_in_ind and ind_code == "BK0001":
            # For BK0001, use existing master symbols
            symbols_in_ind = [s for s, row in existing_master.items() if row.get("industry_code") == "BK0001"]
        if not symbols_in_ind:
            symbols_in_ind = list(clean_kline_map.keys())[:50]

        sector_bars = []
        for d in all_dates:
            bars_on_d = [date_sym_bar[d][s] for s in symbols_in_ind if s in date_sym_bar[d]]
            if not bars_on_d:
                continue
            o_avg = sum(x["open"] for x in bars_on_d) / len(bars_on_d)
            h_avg = sum(x["high"] for x in bars_on_d) / len(bars_on_d)
            l_avg = sum(x["low"] for x in bars_on_d) / len(bars_on_d)
            c_avg = sum(x["close"] for x in bars_on_d) / len(bars_on_d)
            v_sum = sum(x["volume"] for x in bars_on_d)
            amt_sum = sum(x["amount"] for x in bars_on_d)
            pct_avg = sum(x["pct"] for x in bars_on_d) / len(bars_on_d)
            sector_bars.append({
                "date": d,
                "open": round(o_avg, 2),
                "high": round(h_avg, 2),
                "low": round(l_avg, 2),
                "close": round(c_avg, 2),
                "volume": round(v_sum, 2),
                "amount": round(amt_sum, 2),
                "pct": round(pct_avg, 2),
            })
        sector_file = cache_sectors_dir / f"{ind_code}.json"
        sector_file.write_text(json.dumps(sector_bars, ensure_ascii=False), encoding="utf-8")

    print(f"Generated sector bars for {len(INDUSTRY_CODE_MAP)} sectors.")

    # 5. Build full security_master.csv with rigorous Point-in-Time intervals
    new_rows = []
    for sym in sorted(clean_kline_map.keys()):
        bars = clean_kline_map[sym]
        start_date = bars[0]["date"] if bars else "2024-09-06"
        end_date = bars[-1]["date"] if bars else ""
        active_to = "" if end_date >= "2026-09-30" else end_date

        board = "SZSE_MAIN"
        if sym.startswith("60"):
            board = "SSE_MAIN"
        elif sym.startswith("68"):
            board = "STAR"
        elif sym.startswith("30"):
            board = "CHINEXT"
        elif sym.startswith("92") or sym.startswith("43") or sym.startswith("83") or sym.startswith("87"):
            board = "BSE"

        ind_code, ind_name = stock_to_industry_info.get(sym, ("BK1217", "综合"))
        if sym in existing_master:
            ex = existing_master[sym]
            start_date = ex.get("active_from") or start_date
            board = ex.get("board") or board
            ind_code = ex.get("industry_code") or ind_code
            ind_name = ex.get("industry_name") or ind_name

        new_rows.append({
            "symbol": sym,
            "active_from": start_date,
            "active_to": active_to,
            "tradable": "1",
            "st": "0",
            "suspended": "0",
            "board": board,
            "industry_code": ind_code,
            "industry_name": ind_name,
        })

    fieldnames = ["symbol", "active_from", "active_to", "tradable", "st", "suspended", "board", "industry_code", "industry_name"]
    with sec_master_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(new_rows)

    print(f"Wrote {len(new_rows)} rows to {sec_master_path}.")

if __name__ == "__main__":
    main()
