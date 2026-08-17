#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_market.py — A股收盘复盘 数据抓取层
多源兜底：东财 push2(主) / 腾讯 qt.gtimg.cn(指数) / 新浪(涨跌家数兜底)
任一源不通 -> 该字段置 None，由 generate_review 渲染"暂不可达"而非整页崩。
连板高度/封板率/晋级率 -> 东财涨停板专题结构化接口(akshare 封装)，真实可算。
"""
import urllib.request, urllib.parse, json, re, time, sys, datetime
from collections import Counter

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

CNY_TO_YI = 100000000

def _get(url, enc="utf-8", timeout=15, ref=None):
    headers = dict(HEADERS)
    if ref:
        headers["Referer"] = ref
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(enc, errors="replace")

def _em_json(url):
    """东财 JSON 接口；push2 被限频/封禁时自动回退到 push2delay 镜像节点（数据同源、字段一致）。"""
    last = None
    for host in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
        u = re.sub(r"push2(delay)?\.eastmoney\.com", host, url)
        try:
            return json.loads(_get(u))
        except Exception as e:
            last = e
    raise last

# ---------------- 指数（腾讯，稳定） ----------------
def fetch_indices_tencent():
    codes = {"sh000001": "上证指数", "sz399006": "创业板指", "sh000688": "科创50"}
    q = ",".join(codes.keys())
    txt = _get("https://qt.gtimg.cn/q=" + q, enc="gbk")
    out = []
    for seg in txt.split(";"):
        seg = seg.strip()
        if not seg.startswith("v_"):
            continue
        code = seg[2:seg.index("=")]
        body = seg[seg.index('"')+1:seg.rindex('"')]
        p = body.split("~")
        if len(p) < 6:
            continue
        name = p[1]
        price = float(p[3]) if p[3] else 0.0
        # 找 14 位时间戳后的 涨跌幅
        pct = None
        for i, x in enumerate(p):
            if re.fullmatch(r"20\d{12}", x):
                try:
                    pct = float(p[i+2])
                except (ValueError, IndexError):
                    pass
                break
        if pct is None:
            try:
                pct = (price / float(p[4]) - 1) * 100 if p[4] else 0.0
            except (ValueError, ZeroDivisionError):
                pct = 0.0
        out.append({"code": code, "name": name, "price": price, "pct": round(pct, 2)})
    return out

# ---------------- 涨跌家数（东财 ulist，按指数成分聚合） ----------------
def fetch_breadth_em():
    secids = "1.000001,0.399001,0.399006,1.000688"  # 上证/深证/创业/科创
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
           "&fields=f12,f14,f104,f105,f128&secids=" + secids)
    d = _em_json(url)
    diff = d.get("data", {}).get("diff", [])
    up = down = flat = lim_up = lim_down = 0
    for it in diff:
        up += int(it.get("f104", 0) or 0)
        down += int(it.get("f105", 0) or 0)
    flat = max(0, (up + down) and 0)  # 占位，下面用 total 推算
    return {"up": up, "down": down}

# ---------------- 板块（东财 clist m:90） ----------------
def fetch_sectors_em(ftype, pz=5):
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2&fid=f3"
           "&fs=m:90+t:%s&fields=f12,f14,f3,f62" % (pz, ftype))
    d = _em_json(url)
    diff = d.get("data", {}).get("diff", [])
    return [_sector_row(x) for x in diff]

def _sector_row(x):
    return {"code": x.get("f12"), "name": x.get("f14"),
            "pct": _f(x.get("f3")), "inflow": _yuan_to_yi(x.get("f62"))}

def _yuan_to_yi(v):
    """东财 f62 是元，页面统一展示为亿元。"""
    return round(_f(v) / CNY_TO_YI, 2)

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

# ---------------- 新浪 涨停家数兜底（顶部页扫描，涨停必在涨幅最前） ----------------
def fetch_limitup_sina(pages=7):
    """新浪 getHQNodeData 按涨幅降序，扫前若干页统计涨停家数（涨停恒居前，覆盖全）。"""
    lim = 0
    for page in range(1, pages + 1):
        try:
            url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "Market_Center.getHQNodeData?page=%d&num=80&sort=changepercent&asc=0&node=hs_a" % page)
            txt = _get(url, ref="https://finance.sina.com.cn/")
            rows = json.loads(txt)
            if not rows:
                break
            for r in rows:
                cp = float(r.get("changepercent", 0) or 0)
                if cp >= 9.8:
                    lim += 1
                else:
                    # 已降到 9.8% 以下，后续页不再有涨停，提前结束
                    return lim
        except Exception:
            break
        time.sleep(0.4)
    return lim

# ---------------- 跌停列表（新浪，与 limit_up_sina 同源，避开东财 push2 的 502） ----------------
def fetch_limitdown_sina(pages=12):
    """新浪 getHQNodeData 按跌幅升序，扫前若干页统计跌停家数 + 列表（跌停恒居前）。"""
    lim = 0
    rows = []
    for page in range(1, pages + 1):
        try:
            url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "Market_Center.getHQNodeData?page=%d&num=80&sort=changepercent&asc=1&node=hs_a" % page)
            txt = _get(url, ref="https://finance.sina.com.cn/")
            rws = json.loads(txt)
            if not rws:
                break
            for r in rws:
                cp = float(r.get("changepercent", 0) or 0)
                if cp <= -9.8:
                    lim += 1
                    rows.append({"code": r.get("code"), "name": r.get("name"),
                                 "pct": cp, "reason": ""})
                # 不早退：新浪 hs_a 升序首只未必是最跌股，需扫多页收集
        except Exception:
            break
        time.sleep(0.4)
    return lim, rows

# ---------------- 主入口 ----------------
def collect(demo=False):
    today = datetime.date.today()
    res = {
        "date": today.strftime("%Y-%m-%d"),
        "weekday": today.strftime("%A"),
        "gate": {"pass": True, "reason": ""},
        "indices": [],
        "breadth": {},
        "sectors_up": [], "sectors_down": [], "concepts_up": [], "inflow_top5": [],
        "limit_up_list": [], "limit_down_list": [], "broken_list": [],
        "board": {"height": None, "seal_rate": None, "promote_rate": None,
                  "ladder": [], "source": "东财涨停板专题(akshare)"},
        "status": {},
    }
    if demo:
        return _demo_data(today)

    # 1) 指数（腾讯）
    try:
        res["indices"] = fetch_indices_tencent()
        res["status"]["indices"] = "ok"
    except Exception as e:
        res["status"]["indices"] = "unreachable:%s" % e

    # 2) 涨跌家数：东财优先；涨停家数东财不通则新浪顶部扫描推导
    breadth = {}
    try:
        b = fetch_breadth_em()
        breadth.update(b)
        res["status"]["breadth_em"] = "ok"
    except Exception as e:
        res["status"]["breadth_em"] = "unreachable:%s" % e
    # 涨停家数（优先东财 board，下方 4) 会填；这里先准备新浪兜底
    try:
        lim_sina = fetch_limitup_sina()
        breadth["limit_up_sina"] = lim_sina
        res["status"]["limit_up_sina"] = "ok"
    except Exception as e:
        res["status"]["limit_up_sina"] = "unreachable:%s" % e
    res["breadth"] = breadth

    # 3) 板块
    try:
        res["sectors_up"] = fetch_sectors_em("2")
        res["status"]["sectors"] = "ok"
    except Exception as e:
        res["status"]["sectors"] = "unreachable:%s" % e
    try:
        res["concepts_up"] = fetch_sectors_em("3")
        res["status"]["concepts"] = "ok"
    except Exception as e:
        res["status"]["concepts"] = "unreachable:%s" % e
    # 行业涨幅榜（降序）& 跌幅榜（升序）& 资金流入（按 f62 降序）
    try:
        up = fetch_sectors_em("2")
        dn_url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=0&np=1&fltt=2&invt=2&fid=f3"
                  "&fs=m:90+t:2&fields=f12,f14,f3,f62")
        d = _em_json(dn_url); res["sectors_down"] = [_sector_row(x) for x in d.get("data", {}).get("diff", [])]
        inf_url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f62"
                   "&fs=m:90+t:2&fields=f12,f14,f3,f62&ord=f62")
        d2 = _em_json(inf_url); res["inflow_top5"] = [{"name": x.get("f14"), "inflow": _yuan_to_yi(x.get("f62"))}
                                                      for x in d2.get("data", {}).get("diff", [])]
    except Exception as e:
        res["status"]["sectors_detail"] = "unreachable:%s" % e

    # 4) 涨停/跌停/连板（真实结构化：东财涨停板专题 akshare 封装）
    ymd = today.strftime("%Y%m%d")
    board, bstat = fetch_board_akshare(ymd)
    res["board"].update(board)
    res["status"]["board_akshare"] = bstat
    # 涨停封死家数填入 breadth（供广度/温度使用）
    if board.get("limit_up_sealed") is not None:
        res["breadth"]["limit_up"] = board["limit_up_sealed"]
        res["breadth"]["limit_up_source"] = "东财涨停池"
        res["status"]["limit_up"] = "ok"
    else:
        # akshare 不通 -> 新浪推导的涨停家数兜底
        if res["breadth"].get("limit_up_sina"):
            res["breadth"]["limit_up"] = res["breadth"]["limit_up_sina"]
            res["breadth"]["limit_up_source"] = "新浪推导"
            res["status"]["limit_up"] = "sina_fallback"
    if board.get("broken") is not None:
        res["breadth"]["broken"] = board["broken"]
    # 跌停：新浪（与 limit_up_sina 同源，避开东财 push2 的 502）
    try:
        total_ld, ld = fetch_limitdown_sina()
        res["limit_down_list"] = ld
        res["breadth"]["limit_down"] = total_ld
        res["status"]["limit_down"] = "ok"
    except Exception as e:
        res["status"]["limit_down"] = "unreachable:%s" % e

    # gate: 涨停=0 或 日期不符 -> 跳过
    lim = res["breadth"].get("limit_up")
    if lim == 0:
        res["gate"] = {"pass": False, "reason": "涨停家数为 0，疑为非交易日"}
    return res

def fetch_board_akshare(date_ymd):
    """连板高度/封板率/晋级率 全部来自东财涨停板专题结构化数据，非推导。
    返回 (board_dict, status_str)。任一子步骤失败均优雅降级（对应字段置 None）。
    """
    out = {"height": None, "seal_rate": None, "promote_rate": None, "ladder": [],
           "limit_up_sealed": None, "broken": None,
           "source": "东财涨停板专题(akshare)", "promote_detail": ""}
    try:
        import akshare as ak
    except Exception as e:
        return out, "akshare未安装:%s" % e
    # --- 涨停池：连板高度 + 梯队 + 封死家数 ---
    try:
        df = ak.stock_zt_pool_em(date=date_ymd)
        if df is None or df.empty:
            return out, "涨停池为空(可能未收盘/非交易日)"
        sealed = int(len(df))
        heights = df["连板数"].dropna().astype(int).tolist()
        height = int(max(heights)) if heights else None
        cnt = Counter(heights)
        names_by = {}
        for _, r in df.iterrows():
            names_by.setdefault(int(r["连板数"]), []).append(r["名称"])
        ladder = [{"day": k, "count": cnt[k], "names": names_by[k][:6]} for k in sorted(cnt, reverse=True)]
        out.update(height=height, limit_up_sealed=sealed, ladder=ladder)
    except Exception as e:
        return out, "涨停池ERR:%s" % e
    # --- 炸板池：封板率 = 封死 / (封死 + 炸板) ---
    try:
        dfz = ak.stock_zt_pool_zbgc_em(date=date_ymd)
        broken = int(len(dfz)) if dfz is not None and not dfz.empty else 0
        out["broken"] = broken
        denom = sealed + broken
        out["seal_rate"] = round(sealed / denom * 100, 1) if denom else None
    except Exception as e:
        out.setdefault("_note", []).append("炸板池ERR:%s" % e)
    # --- 昨日池：晋级率（1进2 = 今日2板只数 / 昨日首板只数，样本最大最稳） ---
    try:
        dfp = ak.stock_zt_pool_previous_em(date=date_ymd)
        if dfp is not None and not dfp.empty and "昨日连板数" in dfp.columns:
            yest = dfp["昨日连板数"].dropna().astype(int)
            yest_n1 = int((yest == 1).sum())
            today_n2 = int((df["连板数"] == 2).sum())
            if yest_n1:
                out["promote_rate"] = round(today_n2 / yest_n1 * 100, 1)
                out["promote_detail"] = "1进2: 今日2板%d只 / 昨日首板%d只" % (today_n2, yest_n1)
    except Exception as e:
        out.setdefault("_note", []).append("昨日池ERR:%s" % e)
    return out, "ok"


def _build_ladder(cb):
    d = {}
    for x in cb:
        m = re.search(r"(\d+)连板", x.get("name", ""))
        if m:
            d.setdefault(int(m.group(1)), []).append(x.get("name"))
    return [{"day": k, "count": len(v), "names": v[:6]} for k, v in sorted(d.items(), reverse=True)]

def _demo_data(today):
    """结构预览用示例数据（明确标注非真实行情）。"""
    return {
        "date": today.strftime("%Y-%m-%d"), "weekday": today.strftime("%A"),
        "gate": {"pass": True, "reason": "示例数据"},
        "indices": [
            {"code": "sh000001", "name": "上证指数", "price": 3960.19, "pct": 0.84},
            {"code": "sz399006", "name": "创业板指", "price": 2418.55, "pct": 1.32},
            {"code": "sh000688", "name": "科创50", "price": 1024.77, "pct": -0.45},
        ],
        "breadth": {"up": 3210, "down": 1780, "flat": 230, "limit_up": 68, "limit_down": 12,
                    "total": 5220, "amount": 11280.0, "up_pct": 61.5},
        "sectors_up": [
            {"name": "半导体", "pct": 3.82, "inflow": 58.2}, {"name": "消费电子", "pct": 2.95, "inflow": 31.4},
            {"name": "军工", "pct": 2.41, "inflow": 22.8}, {"name": "光伏设备", "pct": 2.10, "inflow": 18.0},
            {"name": "机器人", "pct": 1.88, "inflow": 15.3}],
        "sectors_down": [
            {"name": "航空机场", "pct": -1.20, "inflow": -8.5}, {"name": "煤炭", "pct": -0.95, "inflow": -6.1},
            {"name": "银行", "pct": -0.72, "inflow": -5.4}, {"name": "地产", "pct": -0.58, "inflow": -3.9},
            {"name": "电力", "pct": -0.41, "inflow": -2.7}],
        "concepts_up": [
            {"name": "AI芯片", "pct": 4.12}, {"name": "先进封装", "pct": 3.55}, {"name": "CPO", "pct": 3.21},
            {"name": "存储", "pct": 2.88}, {"name": "低空经济", "pct": 2.44}],
        "inflow_top5": [
            {"name": "半导体", "inflow": 58.2}, {"name": "消费电子", "inflow": 31.4},
            {"name": "军工", "inflow": 22.8}, {"name": "光伏设备", "inflow": 18.0}, {"name": "机器人", "inflow": 15.3}],
        "limit_up_list": [
            {"name": "示例股A", "pct": 10.02, "reason": "半导体+业绩预增"},
            {"name": "示例股B", "pct": 20.01, "reason": "科创板+重组"},
            {"name": "示例股C", "pct": 10.00, "reason": "机器人概念"}],
        "limit_down_list": [{"name": "示例股X", "pct": -10.01, "reason": "-"}],
        "broken_list": [{"name": "示例股Y", "pct": 8.5, "reason": "-"}],
        "board": {"height": 7, "seal_rate": 78.5, "promote_rate": 62.0,
                  "ladder": [{"day": 7, "count": 1, "names": ["示例股B"]},
                             {"day": 4, "count": 2, "names": ["示例股C", "示例股D"]},
                             {"day": 3, "count": 5, "names": ["示例股E", "示例股F"]},
                             {"day": 2, "count": 12, "names": ["示例股G"]}],
                  "source": "示例数据（非真实）"},
        "status": {"demo": True},
    }

if __name__ == "__main__":
    import os
    demo = "--demo" in sys.argv
    out_path = "--out" in sys.argv
    data = collect(demo=demo)
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    OUT_DIR = os.path.join(BASE, "outputs", "review")
    os.makedirs(OUT_DIR, exist_ok=True)
    raw = os.path.join(OUT_DIR, "market_raw.json")
    with open(raw, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("WROTE", raw)
    print("indices:", [(x["name"], x["pct"]) for x in data["indices"]])
    print("breadth:", data["breadth"])
    print("limit_up_count:", data["breadth"].get("limit_up"), "source:", data["breadth"].get("limit_up_source"))
    print("status:", data["status"])
