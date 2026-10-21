"""Build the local, offline setup guide from the repository instructions."""
from pathlib import Path
import html
import re

ROOT=Path(__file__).resolve().parents[1]

def inline(text):
    text=html.escape(text)
    text=re.sub(r"\[([^]]+)\]\((https://[^\s)]+)\)",r'<a href="\2" target="_blank" rel="noreferrer">\1</a>',text)
    text=re.sub(r"`([^`]+)`",r"<code>\1</code>",text)
    return re.sub(r"\*\*([^*]+)\*\*",r"<strong>\1</strong>",text)

def main():
    result=[];code=False
    for line in (ROOT/"docs/SETUP.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            result.append("</code></pre>" if code else "<pre><code>");code=not code;continue
        if code: result.append(html.escape(line)+"\n");continue
        if not line.strip():continue
        heading=re.match(r"^(#{1,4}) (.+)$",line)
        if heading:
            level=len(heading[1]);result.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        else:result.append("<p>"+inline(line)+"</p>")
    body="\n".join(result)
    (ROOT/"frontend/public/guide.html").write_text('''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Настройка Playerok Manager</title><style>
@font-face{font-family:Guide;src:url('/fonts/nunito-sans-0.ttf') format('truetype');font-display:swap}*{box-sizing:border-box}body{margin:0;background:#f5f6f3;color:#22271e;font:17px/1.75 Guide,system-ui,sans-serif}main{max-width:900px;margin:40px auto;padding:36px;background:#fff;border-radius:24px}h1,h2,h3{line-height:1.3;scroll-margin-top:20px}h1{font-size:32px}h2{margin-top:42px;font-size:23px}a{color:#ce401e;text-underline-offset:4px}pre{padding:18px;overflow:auto;background:#f2f3ef;border-radius:12px}code{font-size:.88em;overflow-wrap:anywhere}p{margin:12px 0}@media(max-width:600px){main{margin:0;padding:24px;border-radius:0}h1{font-size:27px}}</style><main><a href="/settings">← Вернуться в настройки</a>'''+body+"</main></html>",encoding="utf-8")
    print("Offline setup guide generated.")

if __name__=="__main__":main()
