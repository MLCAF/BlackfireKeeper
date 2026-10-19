"""Render the plain Markdown guide into a self-contained, readable HTML file."""
import html
from pathlib import Path

root = Path(__file__).resolve().parents[1]
blocks = []
code = False
for line in (root / '使用说明.md').read_text(encoding='utf-8').splitlines():
    if line.startswith('```'):
        blocks.append('</code></pre>' if code else '<pre><code>')
        code = not code
    elif code:
        blocks.append(html.escape(line) + '\n')
    elif line.startswith('#'):
        level = len(line) - len(line.lstrip('#'))
        blocks.append(f'<h{level}>{html.escape(line[level:].strip())}</h{level}>')
    elif line:
        blocks.append(f'<p>{html.escape(line)}</p>')
page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>黑火守望 · 使用说明</title><style>
body{max-width:920px;margin:40px auto;padding:0 24px 64px;font:16px/1.85 "Microsoft YaHei",sans-serif;color:#243146;background:#f5f7fa}
h1{font-size:32px;color:#102638}h2{margin-top:40px;border-bottom:2px solid #cad8e2;padding-bottom:8px}h3{margin-top:28px}
pre{padding:16px;background:#172332;color:#e8f3ff;overflow:auto;border-radius:8px;font:14px/1.6 Consolas,monospace}
@media print{body{background:white;margin:0;max-width:none}h2,h3{break-after:avoid}pre{white-space:pre-wrap}}
</style><body>''' + '\n'.join(blocks) + '</body></html>'
(root / '使用说明.html').write_text(page, encoding='utf-8')
