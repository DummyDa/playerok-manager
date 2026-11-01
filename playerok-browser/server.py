"""Independent, read-only browser of ordinary Playerok listings. No account required."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs,urlsplit
import webbrowser
from urllib.request import urlopen

from curl_cffi import requests

ROOT=Path(__file__).resolve().parent
CATEGORY="1efcc43d-d46b-6b10-9805-49c1107e28b3"
ITEMS_HASH="3f20c731f8f769a094ee3fa32e09f8e12250357e9a4f0ebb4e6988e7a0bb9260"
lock=threading.Lock()
cache={}
next_request=0
blocked_until=0


def normalize(node):
    if node.get("priority")!="DEFAULT" or node.get("status")!="APPROVED":return None
    slug=str(node.get("slug") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,300}",slug):return None
    price=node.get("price")
    if not isinstance(price,(int,float)) or isinstance(price,bool) or not math.isfinite(price) or price<0:return None
    raw=node.get("rawPrice")
    old_price=raw if isinstance(raw,(int,float)) and not isinstance(raw,bool) and math.isfinite(raw) and raw>price else None
    image=str((node.get("attachment") or {}).get("url") or "")
    parsed=urlsplit(image)
    if parsed.scheme!="https" or parsed.hostname not in {"i.playerok.com","sapi.playerok.com"}:image=None
    seller=node.get("user") or {}
    rating=seller.get("rating")
    rating=rating if isinstance(rating,(int,float)) and math.isfinite(rating) and 0<=rating<=5 else None
    reviews=seller.get("testimonialCounter")
    reviews=reviews if isinstance(reviews,int) and not isinstance(reviews,bool) and reviews>=0 else None
    return {"id":str(node.get("id") or slug),"title":str(node.get("name") or "Без названия"),"url":"https://playerok.com/products/"+slug,"image":image,"price":price,"old_price":old_price,"rating":rating,"reviews":reviews,"seller":str(seller.get("username") or "")}


def load_page(cursor=""):
    global next_request,blocked_until
    if len(cursor)>1000 or not re.fullmatch(r"[A-Za-z0-9+/=_:-]*",cursor):raise ValueError("Неверная страница")
    with lock:
        now=time.time()
        if cursor in cache and cache[cursor][0]>now-60:return cache[cursor][1]
        if blocked_until>now:raise RuntimeError("Playerok ограничил запросы. Попробуйте немного позже.")
        delay=next_request-now
        if delay>0:time.sleep(delay)
        next_request=time.time()+2
        variables={"pagination":{"first":24},"filter":{"gameCategoryId":CATEGORY,"status":["APPROVED"],"priority":"DEFAULT"},"showForbiddenImage":False}
        if cursor:variables["pagination"]["after"]=cursor
        try:
            response=requests.get("https://playerok.com/graphql",params={"operationName":"items","variables":json.dumps(variables),"extensions":json.dumps({"persistedQuery":{"version":1,"sha256Hash":ITEMS_HASH}})},headers={"accept":"application/json","content-type":"application/json","apollographql-client-name":"web","referer":"https://playerok.com/telegram/nft"},impersonate="chrome",timeout=20)
            if response.status_code==429:
                blocked_until=time.time()+60
                raise RuntimeError("Playerok ограничил запросы. Попробуйте через минуту.")
            if response.status_code in {401,403}:raise RuntimeError("Playerok временно ограничил просмотр. Попробуйте позже.")
            if response.status_code!=200:raise RuntimeError("Playerok временно недоступен. Попробуйте позже.")
            data=response.json()
            if data.get("errors"):raise RuntimeError("Playerok изменил ответ API. Загрузка временно недоступна.")
            connection=data["data"]["items"]
            items=[item for edge in connection.get("edges",[]) if (item:=normalize(edge.get("node") or {})) is not None]
            page=connection.get("pageInfo") or {}
            end=page.get("endCursor")
            value={"items":items,"next_cursor":end if page.get("hasNextPage") and end and end!=cursor else None,"checked_at":time.time()}
        except RuntimeError:raise
        except Exception:raise RuntimeError("Не удалось получить объявления Playerok. Повторите попытку.") from None
        if len(cache)>=100:cache.pop(next(iter(cache)))
        cache[cursor]=(time.time(),value)
        return value


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def send(self,status,body,content_type):
        self.send_response(status)
        for key,value in {"Content-Type":content_type,"Content-Length":str(len(body)),"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY","Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://i.playerok.com https://sapi.playerok.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"}.items():self.send_header(key,value)
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host","").split(":")[0] not in {"localhost","127.0.0.1"}:
            self.send(400,b"Invalid Host","text/plain");return
        path=urlsplit(self.path)
        if path.path=="/api/health":
            self.send(200,b'{"name":"playerok-browser","status":"ok"}',"application/json");return
        if path.path=="/api/lots":
            try:
                values=parse_qs(path.query)
                data=load_page(values.get("cursor",[""])[0]);status=200
            except ValueError as exc:data={"error":str(exc)};status=400
            except RuntimeError as exc:data={"error":str(exc)};status=503
            self.send(status,json.dumps(data,ensure_ascii=False).encode(),"application/json; charset=utf-8");return
        files={"/":("index.html","text/html; charset=utf-8"),"/style.css":("style.css","text/css; charset=utf-8"),"/app.js":("app.js","text/javascript; charset=utf-8")}
        if path.path not in files:self.send(404,b"Not found","text/plain");return
        name,mime=files[path.path];self.send(200,(ROOT/"web"/name).read_bytes(),mime)


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--open",action="store_true");parser.add_argument("--port",type=int,default=3190)
    args=parser.parse_args()
    url=f"http://127.0.0.1:{args.port}"
    try:server=LocalServer(("127.0.0.1",args.port),Handler)
    except OSError:
        try:
            with urlopen(url+"/api/health",timeout=2) as response:own=json.load(response).get("name")=="playerok-browser"
        except Exception:own=False
        if not own:raise SystemExit(f"Port {args.port} is occupied. Use --port with another port.")
        print("Playerok Browser already running: "+url,flush=True)
        if args.open:webbrowser.open(url)
        return
    print("Playerok Browser: "+url,flush=True)
    if args.open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":main()
