#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_review.py — A股收盘复盘 单页 HTML 生成器
输入: fetch_market.py 输出的 market_raw.json (或 --demo 用示例数据)
输出: outputs/review/market-review-YYYYMMDD.html (自包含, 零外链)
      校验: 外链=0 / 标签平衡 / 内联JS过 node --check
      可选推送: 项目根 .env 配置 BARK_KEY(iOS Bark)/WECOM_WEBHOOK_URL(企微)/WXPUSHER_APP_TOKEN+WXPUSHER_UIDS 其一即自动推送；都未配则跳过
"""
import json, sys, os, re, datetime, urllib.request, urllib.parse

ROOT = "/Users/melody/WorkBuddy/2026-08-14-16-36-41"
RAW = f"{ROOT}/outputs/review/market_raw.json"
OUTDIR = f"{ROOT}/outputs/review"
NODE = "/Users/melody/.workbuddy/binaries/node/versions/22.22.2/bin/node"

RED = "#f5494d"; GREEN = "#00b070"; FLAT = "#b9c0cc"

# ---------------- 情绪温度模型 ----------------
def compute_temp(d):
    b = d.get("breadth", {})
    bd = d.get("board", {})
    lu = b.get("limit_up")
    if lu is None:
        return None, "数据不足"
    seal = bd.get("seal_rate"); height = bd.get("height"); promo = bd.get("promote_rate")
    lu_score = min(100.0, lu * 0.8)
    seal_score = seal if seal is not None else 60.0
    height_score = min(100.0, height * 14) if height else 50.0
    promo_score = promo if promo is not None else 55.0
    temp = 0.40 * lu_score + 0.25 * seal_score + 0.20 * height_score + 0.15 * promo_score
    temp = max(0, min(100, round(temp)))
    if temp < 35:
        q = "冰点"
    elif temp < 50:
        q = "弱修复"
    elif temp <= 65:
        q = "中性"
    else:
        q = "高温"
    return temp, q

def qualitative_css(q):
    return {"冰点": "#5b6b8c", "弱修复": "#ff9f43", "中性": "#2d6cf6", "高温": "#f5494d"}.get(q, FLAT)

# ---------------- 工具 ----------------
def pct_color(v):
    if v is None:
        return FLAT
    return RED if v > 0 else (GREEN if v < 0 else FLAT)

def fmt(v, suffix=""):
    if v is None:
        return "—"
    return f"{v}{suffix}"

def na_note(msg):
    return f'<div class="na">数据源暂不可达 · {msg}</div>'

# ---------------- SVG: 情绪温度计 ----------------
def svg_thermometer(temp):
    if temp is None:
        return na_note("情绪温度待涨停数据")
    h = 240; w = 90; top = 20; bottom = top + h
    fill_top = bottom - (temp / 100.0) * h
    # color by temp band
    if temp < 35:
        c = RED
    elif temp < 50:
        c = "#ffa502"
    elif temp <= 65:
        c = "#3742fa"
    else:
        c = "#e84118"
    svg = f'''
    <svg viewBox="0 0 {w+40} {bottom+30}" class="thermo" aria-label="情绪温度计">
      <rect x="35" y="{top}" width="20" height="{h}" rx="10" fill="#eceef2" stroke="#d4dae2"/>
      <rect x="35" y="{fill_top:.1f}" width="20" height="{bottom-fill_top:.1f}" rx="10" fill="{c}"/>
      <circle cx="45" cy="{bottom}" r="16" fill="{c}"/>
      <line x1="70" y1="{top}" x2="70" y2="{bottom}" stroke="#cfd6df"/>
      {''.join(f'<line x1="70" y1="{bottom - i*h/5:.1f}" x2="76" y2="{bottom - i*h/5:.1f}" stroke="#cfd6df"/><text x="80" y="{bottom - i*h/5+4:.1f}" class="ax">{i*20}</text>' for i in range(6))}
      <text x="45" y="{fill_top-8 if fill_top>top+10 else bottom+22:.1f}" text-anchor="middle" class="thermo-val">{temp}°</text>
    </svg>'''
    return svg

# ---------------- SVG: 涨停梯队 ----------------
def svg_ladder(ladder):
    if not ladder:
        return na_note("梯队数据待联网检索(财联社口径)")
    maxc = max((x["count"] for x in ladder), default=1) or 1
    rowh = 34; top = 10; w = 520
    bars = ""
    for i, lv in enumerate(ladder):
        y = top + i * rowh
        bw = max(8, lv["count"] / maxc * 360)
        bars += f'''
        <text x="6" y="{y+20}" class="lbl">{'连板' if lv['day']>1 else '首板'}·{lv['day']}</text>
        <rect x="60" y="{y+4}" width="{bw:.1f}" height="22" rx="4" fill="{RED}"/>
        <text x="{60+bw+6:.1f}" y="{y+20}" class="lbl">{lv['count']}只 {','.join(lv['names'][:3])}</text>'''
    svg = f'<svg viewBox="0 0 {w} {top+len(ladder)*rowh+6}" class="ladder">{bars}</svg>'
    return svg

# ---------------- 条形组件 ----------------
def bar_row(label, val, maxv, color, suffix="%"):
    if val is None:
        return f'<div class="barrow"><span class="bl">{label}</span><span class="bv na">—</span></div>'
    pct = (abs(val) / maxv * 100) if maxv else 0
    pct = min(100, pct)
    return f'''
    <div class="barrow">
      <span class="bl">{label}</span>
      <span class="btrack"><span class="bar-fill {'up' if val>0 else ('dn' if val<0 else 'flat')}" style="width:{pct:.1f}%"></span></span>
      <span class="bv" style="color:{color}">{val:+.2f}{suffix}</span>
    </div>'''

def breadth_bar(up, down, flat):
    up = up or 0; down = down or 0; flat = flat or 0
    tot = up + down + flat
    if not tot:
        return na_note("涨跌家数待东财/腾讯数据")
    u = up/tot*100; d = down/tot*100; f = flat/tot*100
    return f'''
    <div class="breadth">
      <div class="bseg" style="width:{u:.1f}%;background:{RED}"></div>
      <div class="bseg" style="width:{d:.1f}%;background:{GREEN}"></div>
      <div class="bseg" style="width:{f:.1f}%;background:{FLAT}"></div>
    </div>
    <div class="blegend"><span style="color:{RED}">涨 {up} ({u:.1f}%)</span>
      <span style="color:{GREEN}">跌 {down} ({d:.1f}%)</span>
      <span style="color:#9aa3b5">平 {flat}</span></div>'''

def sector_cards(d):
    def card(title, items, key, unit, rev=False):
        if not items:
            return f'<div class="card"><h4>{title}</h4>{na_note("数据待东财板块接口")}</div>'
        rows = ""
        for it in items[:5]:
            v = it.get(key)
            col = pct_color(v) if unit == "%" else (RED if (v or 0) > 0 else GREEN)
            rows += f'<div class="srow"><span>{it.get("name")}</span><span style="color:{col}">{fmt(v, unit)}</span></div>'
        return f'<div class="card"><h4>{title}</h4>{rows}</div>'
    su = d.get("sectors_up", [])
    cu = d.get("concepts_up", [])
    inf = d.get("inflow_top5", [])
    sd = d.get("sectors_down", [])
    return f'''
    {card("行业涨幅榜", su, "pct", "%")}
    {card("概念涨幅榜", cu, "pct", "%")}
    {card("主力资金流入 Top5", inf, "inflow", "亿")}
    {card("跌幅居前", sd, "pct", "%")}'''

def mainline_cards(d):
    su = d.get("sectors_up", [])
    bd = d.get("board", {})
    b = d.get("breadth", {})
    rel = su[0] if su else None
    rel_txt = f'<b>{rel["name"]}</b> 领涨（+{rel["pct"]}%，主力净流入 {rel.get("inflow")}亿）' if rel else na_note("行业涨幅待数据")
    # 退潮判定
    ld = b.get("limit_down"); lu = b.get("limit_up"); seal = bd.get("seal_rate")
    if lu and ld is not None and ld > lu * 0.3:
        retreat = "跌停数显著放大（跌停/涨停≈{:.0%}），接力意愿走弱，退潮信号确认。".format(ld/lu)
    elif seal is not None and seal < 60:
        retreat = "封板率 {:.0f}% 偏低，资金封板意愿不足，警惕退潮。".format(seal)
    else:
        retreat = "暂无明确退潮信号；连板高度 {}，梯队尚完整。".format(bd.get("height") or "—")
    # 高风险
    broken = b.get("broken")
    risk = "炸板 {} 只，追高日内回撤风险上升。".format(broken) if broken else "高位股需警惕分时炸板与缩量加速。"
    # 活口
    lad = bd.get("ladder") or []
    top_lad = lad[0] if lad else None
    live = ("活口聚焦 <b>{}连板</b> {}。".format(top_lad["day"], "、".join(top_lad["names"][:3])) if top_lad
            else na_note("活口待连板梯队数据"))
    return f'''
    <div class="card wide"><h4>相对强（主线）</h4><div class="mtext">{rel_txt}</div></div>
    <div class="card wide"><h4>退潮确认</h4><div class="mtext">{retreat}</div></div>
    <div class="card wide"><h4>高风险</h4><div class="mtext">{risk}</div></div>
    <div class="card wide"><h4>活口</h4><div class="mtext">{live}</div></div>'''

def build_html(d):
    today = d["date"]
    temp, q = compute_temp(d)
    b = d.get("breadth", {})
    bd = d.get("board", {})
    idx = d.get("indices", [])
    su = d.get("sectors_up", [])
    max_idx = max((abs(x["pct"]) for x in idx), default=1) or 1
    max_pct = max((abs(x.get("pct", 0) or 0) for x in (d.get("sectors_up", []) + d.get("concepts_up", []))), default=5) or 5

    idx_bars = "".join(bar_row(f'{x["name"]} {x["price"]:.2f}', x["pct"], max_idx, pct_color(x["pct"])) for x in idx) or na_note("指数待腾讯行情")

    status = d.get("status", {})
    src_lines = "".join(f'<li>{k}: {"✓" if ("ok" in str(v)) else "✗ "+str(v)}</li>' for k, v in status.items())

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股收盘复盘 {today}</title>
<style>
:root{{--red:{RED};--green:{GREEN};--flat:{FLAT};--bg:#f3f4f6;--card:#ffffff;--ink:#1a1d24;--mut:#8a909c;--line:#e9ebef;--soft:#f7f8fa}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;padding:20px;line-height:1.5}}
.wrap{{max-width:920px;margin:0 auto}}
header{{text-align:center;padding:14px 0 6px}}
header h1{{font-size:22px;letter-spacing:1px;color:#1a1d24}}
header .date{{color:var(--mut);font-size:13px;margin-top:4px}}
.verdict{{margin:14px 0;padding:18px;border-radius:14px;background:linear-gradient(135deg,#fff5f5,#fdeaea);border:1px solid #f6dada;text-align:center}}
.vtag{{display:inline-block;font-size:12px;color:#f5494d;background:#fdeaea;border-radius:20px;padding:3px 12px;margin-bottom:8px;letter-spacing:1px}}
.verdict .q{{font-size:30px;font-weight:800}}
.verdict .sub{{color:var(--mut);margin-top:6px;font-size:14px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}
.kpi{{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:14px 8px;text-align:center}}
.kpi .v{{font-size:24px;font-weight:700}}
.kpi .l{{color:var(--mut);font-size:12px;margin-top:4px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0}}
section h3{{font-size:15px;margin-bottom:12px;color:#2a2f3a;border-left:3px solid var(--red);padding-left:8px}}
.grid2{{display:grid;grid-template-columns:160px 1fr;gap:16px;align-items:center}}
.bar-fill{{display:block;height:12px;border-radius:6px}}
.bar-fill.up{{background:var(--red)}}
.bar-fill.dn{{background:var(--green)}}
.bar-fill.flat{{background:#b9c0cc}}
.barrow{{display:grid;grid-template-columns:140px 1fr 80px;gap:8px;align-items:center;margin:7px 0}}
.bl{{font-size:13px;color:#5a6373}}
.btrack{{background:#eef0f3;border-radius:6px;height:12px;overflow:hidden}}
.bv{{font-size:13px;text-align:right;font-variant-numeric:tabular-nums}}
.breadth{{display:flex;height:22px;border-radius:6px;overflow:hidden;background:#eef0f3}}
.bsseg{{height:100%}}
.blegend{{display:flex;gap:14px;margin-top:8px;font-size:12px;flex-wrap:wrap}}
.cards4{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}
.card{{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:12px}}
.card.wide{{grid-column:1/-1}}
.card h4{{font-size:13px;color:#6b7280;margin-bottom:8px}}
.srow{{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;border-bottom:1px solid #eef0f3}}
.mtext{{font-size:13px;line-height:1.7;color:#3a3f4a}}
.thermo .ax{{fill:#9aa3b5;font-size:9px}}
.thermo .thermo-val{{fill:#1a1d24;font-size:13px;font-weight:700}}
.thermo .lbl{{fill:#5a6373;font-size:11px}}
.ladder .lbl{{fill:#5a6373;font-size:11px}}
.na{{color:#b45309;font-size:12px;background:#fff7ed;border:1px solid #fde8c8;padding:8px 10px;border-radius:8px;margin-top:6px}}
.obs{{font-size:13px;line-height:1.8;color:#3a3f4a}}
.obs b{{color:var(--red)}}
footer{{color:var(--mut);font-size:11px;margin-top:18px;line-height:1.7}}
footer ul{{margin:6px 0 6px 18px}}
.disclaimer{{background:#fff1f1;color:#d43939;border:1px solid #f6dada;padding:10px 12px;border-radius:8px;margin-top:10px;font-size:12px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>A股收盘复盘 · 主线与情绪</h1>
  <div class="date">{today}（{d.get('weekday','')}）· 静态快照，非实时</div>
</header>

<section class="verdict">
  <div class="vtag">① 定性研判</div>
  <div class="q" style="color:{qualitative_css(q)}">{fmt(temp)+'° ' if temp is not None else ''}{q}</div>
  <div class="sub">{ '涨停 '+fmt(b.get('limit_up'))+' 家 · 封板率 '+fmt(bd.get('seal_rate'),'%')+' · 连板高度 '+fmt(bd.get('height'))+' 板 · 晋级率 '+fmt(bd.get('promote_rate'),'%') }</div>
</section>

<section>
  <h3>② 情绪四指标</h3>
  <div class="kpis">
    <div class="kpi"><div class="v" style="color:{RED}">{fmt(b.get('limit_up'))}</div><div class="l">涨停家数</div></div>
    <div class="kpi"><div class="v" style="color:{pct_color(bd.get('seal_rate'))}">{fmt(bd.get('seal_rate'),'%')}</div><div class="l">封板率</div></div>
    <div class="kpi"><div class="v" style="color:#ffd32a">{fmt(bd.get('height'))}</div><div class="l">连板高度(板)</div></div>
    <div class="kpi"><div class="v" style="color:{pct_color(bd.get('promote_rate'))}">{fmt(bd.get('promote_rate'),'%')}</div><div class="l">晋级率</div></div>
  </div>
</section>

<section>
  <h3>③ 情绪温度计 · 市场广度</h3>
  <div class="grid2">
    <div style="text-align:center">{svg_thermometer(temp)}</div>
    <div>
      {breadth_bar(b.get('up'), b.get('down'), b.get('flat'))}
      <div style="margin-top:14px">{idx_bars}</div>
    </div>
  </div>
</section>

<section>
  <h3>④ 指数表现</h3>
  {idx_bars}
</section>

<section>
  <h3>⑤ 板块强弱</h3>
  <div class="cards4">{sector_cards(d)}</div>
</section>

<section>
  <h3>⑥ 涨停梯队</h3>
  {svg_ladder(bd.get('ladder'))}
</section>

<section>
  <h3>⑦ 主线研判</h3>
  <div class="cards4">{mainline_cards(d)}</div>
</section>

<section>
  <h3>⑧ 隔夜 / 明日观察锚</h3>
  <div class="obs">
    · 情绪温度 <b>{temp}°（{q}）</b>，若次日回落至冰点区关注冰点反转；若冲高温区防一致后分化。<br>
    · 盯紧连板最高标 <b>{fmt(bd.get('ladder')[0]['day'] if bd.get('ladder') else None)}板</b> 报价与封单，断板即退潮信号。<br>
    · 主线 <b>{su[0]['name'] if su else '—'}</b> 次日量能是否持续，缩量加速慎追。<br>
    · 封板率 {fmt(bd.get('seal_rate'),'%')} 为日内接力强度锚，低于 60% 降低仓位预期。
  </div>
</section>

<footer>
  <div>⑨ 数据口径与免责：{ '腾讯 qt.gtimg.cn（指数）· 东财 push2（板块/涨跌/跌停）· 东财涨停板专题(akshare: 涨停池/炸板池/昨日池) 真实计算连板高度·封板率·晋级率 · 新浪（涨停家数兜底）· 推送：企业微信机器人 webhook / WxPusher（配置其一即发，未配跳过）。' }</div>
  <div>各源状态：</div>
  <ul>{src_lines}</ul>
  <div class="disclaimer">⚠️ 非投资建议：本页为市场情绪与数据的客观快照，不构成任何买卖建议。投资有风险，决策需独立判断、自负盈亏。</div>
</footer>
</div>
<script>
// 生成时间填充（无其他外链）
(function(){{
  var el=document.querySelector('header .date');
  if(el){{ el.textContent=el.textContent+' · 生成 '+new Date().toLocaleString('zh-CN'); }}
}})();
</script>
</body>
</html>'''
    return html

def validate(html, path):
    issues = []
    for tok in ["http", "https", "src=", "cdn", "xmlns"]:
        if tok in html:
            issues.append(f"外链/敏感标识 '{tok}' 出现 {html.count(tok)} 次")
    # tag balance
    for tag in ["section", "div", "svg", "script", "style", "footer", "header"]:
        o = len(re.findall(rf'<{tag}[\s>]', html)); c = html.count(f'</{tag}>')
        if o != c:
            issues.append(f"<{tag}> 开{o}/闭{c} 不平衡")
    # inline JS node --check
    m = re.search(r'<script>(.*?)</script>', html, re.S)
    if m:
        js = f"{path}.inline.js"
        with open(js, "w") as f:
            f.write(m.group(1))
        rc = os.system(f"{NODE} --check {js} 2>/dev/null")
        if rc != 0:
            issues.append("内联JS未通过 node --check")
    return issues

def load_env_file():
    """从项目根目录 .env 读取推送凭证到 os.environ(不覆盖已有变量)。零依赖。
    自动化运行时通过此方式注入凭证，无需平台环境变量配置界面。"""
    p = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

def push_bark(key, d, temp, q):
    """Bark iOS 推送。只需设备 key(从 Bark App 复制)；可选 BARK_SERVER 自建服务。"""
    b = d.get("breadth", {}); bd = d.get("board", {})
    main = d.get("sectors_up", [{}])[0].get("name", "—") if d.get("sectors_up") else "—"
    date_slug = d['date'].replace('-', '')
    # 在线链接（GitHub Pages 部署后手机可点开看完整版）
    pages_base = os.environ.get("PAGES_BASE_URL", "").rstrip("/")
    online_url = f"{pages_base}/market-review-{date_slug}.html" if pages_base else ""
    local_path = f"{OUTDIR}/market-review-{date_slug}.html"
    link_line = f"在线：{online_url}\n" if online_url else f"文件：{local_path}\n"
    body = (f"情绪 {temp}° {q}\n"
            f"涨停 {b.get('limit_up')} 家 · 封板率 {bd.get('seal_rate')}%\n"
            f"连板高度 {bd.get('height')} 板 · 晋级率 {bd.get('promote_rate')}%\n"
            f"主线：{main}\n"
            f"{link_line}"
            f"（非投资建议）")
    server = os.environ.get("BARK_SERVER", "https://api.day.app").rstrip("/")
    try:
        url = f"{server}/{key}"
        payload = json.dumps({"title": f"A股收盘复盘 {d['date']}", "body": body,
                               "level": "active"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        if resp.get("code") == 200:
            return "BARK_OK"
        return f"BARK_FAIL code={resp.get('code')} msg={resp.get('message')}"
    except Exception as e:
        return f"BARK_ERR {e}"

def push_wxpusher(d, temp, q):
    """WxPusher 微信推送。需 WXPUSHER_APP_TOKEN + WXPUSHER_UIDS（关注公众号创建应用获取）。contentType=2 纯文本保证换行。"""
    tok = os.environ.get("WXPUSHER_APP_TOKEN")
    uids = os.environ.get("WXPUSHER_UIDS")
    if not (tok and uids):
        return "skip(未配置凭证)"
    b = d.get("breadth", {}); bd = d.get("board", {})
    main = d.get("sectors_up", [{}])[0].get("name", "—") if d.get("sectors_up") else "—"
    content = (f"A股收盘复盘 {d['date']}\n"
               f"情绪 {temp}° {q}\n"
               f"涨停 {b.get('limit_up')} 家 · 封板率 {bd.get('seal_rate')}%\n"
               f"连板高度 {bd.get('height')} 板 · 晋级率 {bd.get('promote_rate')}%\n"
               f"主线：{main}\n"
               f"文件：{OUTDIR}/market-review-{d['date'].replace('-', '')}.html\n"
               f"（非投资建议）")
    try:
        url = "https://wxpusher.zjie.net.cn/api/send"
        payload = json.dumps({"appToken": tok, "content": content, "contentType": 2,
                               "uids": uids.split(",")}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        if resp.get("code") == 1000:
            return "WXPUSHER_OK"
        return f"WXPUSHER_FAIL code={resp.get('code')} msg={resp.get('msg')}"
    except Exception as e:
        return f"WXPUSHER_ERR {e}"

def push_wecom(webhook, d, temp, q):
    """企业微信群机器人 webhook 推送（markdown 卡片）。只需一个 webhook URL。"""
    b = d.get("breadth", {}); bd = d.get("board", {})
    main = d.get("sectors_up", [{}])[0].get("name", "—") if d.get("sectors_up") else "—"
    content = (f"## A股收盘复盘 {d['date']}\n"
               f"> 情绪温度 **{temp}° {q}**\n"
               f"> 涨停 {b.get('limit_up')} 家 · 封板率 {bd.get('seal_rate')}% · "
               f"连板高度 {bd.get('height')} 板 · 晋级率 {bd.get('promote_rate')}%\n"
               f"> 主线：{main}\n\n"
               f"> 非投资建议，仅供参考。")
    try:
        payload = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}).encode("utf-8")
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        if resp.get("errcode") == 0:
            return "WECOM_OK"
        return f"WECOM_FAIL errcode={resp.get('errcode')}"
    except Exception as e:
        return f"WECOM_ERR {e}"

def push_notify(d, temp, q):
    """可插拔推送：按 .env 配置依次发 Bark / 企微 / WxPusher；都未配则跳过（不阻断落盘）。"""
    res = []
    if os.environ.get("BARK_KEY"):
        res.append("bark:" + push_bark(os.environ["BARK_KEY"], d, temp, q))
    if os.environ.get("WECOM_WEBHOOK_URL"):
        res.append("wecom:" + push_wecom(os.environ["WECOM_WEBHOOK_URL"], d, temp, q))
    if os.environ.get("WXPUSHER_APP_TOKEN") and os.environ.get("WXPUSHER_UIDS"):
        res.append("wxpusher:" + push_wxpusher(d, temp, q))
    if not res:
        return "skip(未配置推送凭证)"
    return " | ".join(res)

def main():
    load_env_file()
    demo = "--demo" in sys.argv
    if demo:
        import fetch_market
        d = fetch_market.collect(demo=True)
    else:
        if not os.path.exists(RAW):
            print("NO RAW JSON — 先运行 fetch_market.py"); sys.exit(1)
        d = json.load(open(RAW, encoding="utf-8"))
    # gate
    if not d["gate"]["pass"]:
        print("GATE FAIL:", d["gate"]["reason"]); sys.exit(0)
    temp, q = compute_temp(d)
    html = build_html(d)
    ymd = d["date"].replace("-", "")
    out = f"{OUTDIR}/market-review-{ymd}.html"
    os.makedirs(OUTDIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    issues = validate(html, out)
    if issues:
        print("VALIDATION ISSUES:")
        for i in issues:
            print("  -", i)
    else:
        print("VALIDATION: OK (零外链/标签平衡/JS通过)")
    push = push_notify(d, temp, q)
    print(f"WROTE {out}")
    print(f"情绪温度={temp}° 定性={q} 主线={d.get('sectors_up',[{}])[0].get('name','—') if d.get('sectors_up') else '—'} 推送={push}")

if __name__ == "__main__":
    main()
