#!/usr/bin/env python3
"""生成 index.html 列出所有历史复盘页面，部署到 GitHub Pages 首页。"""
import os, glob, re

dir_path = os.path.dirname(os.path.abspath(__file__))
site_dir = os.path.join(os.path.dirname(os.path.dirname(dir_path)), "outputs", "review")
site_dir = os.environ.get("SITE_DIR", site_dir)

html_files = sorted(glob.glob(os.path.join(site_dir, "market-review-*.html")), reverse=True)

items = []
for f in html_files:
    fname = os.path.basename(f)
    m = re.search(r"(\d{4})(\d{2})(\d{2})", fname)
    if m:
        label = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    else:
        label = fname
    items.append(f'<li><a href="{fname}">{label}</a></li>')

index_html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股收盘复盘</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:680px;margin:40px auto;padding:0 20px;background:#f3f4f6;color:#1a1d24}}
h1{{font-size:20px;border-bottom:2px solid #e9ebef;padding-bottom:12px}}
ul{{list-style:none;padding:0}}
li{{padding:12px 0;border-bottom:1px solid #e9ebef}}
a{{color:#3742fa;text-decoration:none;font-size:16px}}
a:hover{{text-decoration:underline}}
.empty{{color:#8a909c;font-size:14px}}
</style></head><body>
<h1>A股收盘复盘 · 历史归档</h1>
<ul>{"".join(items) if items else '<li class="empty">暂无复盘记录</li>'}</ul>
</body></html>"""

out = os.path.join(site_dir, "index.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(index_html)
print(f"WROTE {out} ({len(items)} items)")
