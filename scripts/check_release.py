"""Audit an explicit source allowlist; optionally produce a clean ZIP."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", ".gitignore", ".gitattributes", ".env.example", "alembic.ini", "pytest.ini", "requirements.txt", "requirements.lock.txt", "run.bat", "setup.bat", "status.bat", "stop.bat"}
ROOT_FILES.add("test-gift-delivery.bat")
TREES = {"backend", "frontend", "scripts", "tests", "docs", "extension", ".github"}
SKIP = {"node_modules", ".next", "__pycache__", ".pytest_cache", "test-results", "playwright-report"}
SUFFIXES = {".py", ".md", ".txt", ".ini", ".json", ".ts", ".tsx", ".css", ".mjs", ".cjs", ".js", ".html", ".svg", ".woff2", ".woff", ".ttf", ".yml", ".yaml", ".bat"}

def source_files(root=ROOT):
    for p in sorted(root.rglob("*")):
        relative = p.relative_to(root)
        if any(part in SKIP or part.startswith(".venv") or part in {"data", ".git"} for part in relative.parts): continue
        if not p.is_file(): continue
        if relative.as_posix() in ROOT_FILES:
            yield p; continue
        if relative.parts[0] not in TREES: continue
        if p.name in {"next-env.d.ts", "tsconfig.tsbuildinfo"} or p.suffix.lower() not in SUFFIXES: continue
        if p.name.startswith(".env") or any(x in p.name.lower() for x in (".session", ".sqlite", ".har", ".key", ".log")): continue
        yield p

def local_secrets():
    database, key = ROOT/"data/manager.sqlite3", ROOT/"data/secrets.key"
    if not database.exists() or not key.exists(): return []
    from cryptography.fernet import Fernet
    cipher=Fernet(key.read_bytes().strip())
    with sqlite3.connect(database.as_uri()+"?mode=ro", uri=True) as connection:
        rows=connection.execute("SELECT name,value FROM secrets").fetchall()
    result=[]
    for name,value in rows:
        if name in {"config:PLAYEROK_USER_AGENT", "config:MRKT_API_URL", "config:PUBLIC_ORIGIN", "config:OWNER_TELEGRAM_USERNAME"}: continue
        plain=cipher.decrypt(value.encode())
        if len(plain)>=16: result.append(plain)
        if name=="playerok_cookies":
            for part in plain.decode().split(";"):
                if "=" in part:
                    v=part.split("=",1)[1].strip().encode()
                    if len(v)>=24: result.append(v)
    return result

def audit(files):
    secrets=local_secrets();errors=[]
    token=re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{35}\b")
    built=list((ROOT/"frontend/.next/static").rglob("*.js")) if (ROOT/"frontend/.next/static").exists() else []
    for p in files+built:
        raw=p.read_bytes()
        if any(value in raw for value in secrets) or token.search(raw) or (b"-----BEGIN "+b"PRIVATE KEY-----") in raw:
            errors.append(str(p.relative_to(ROOT)))
    if errors: raise SystemExit("Possible secret in files (values suppressed): "+", ".join(errors))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args=parser.parse_args()
    files=list(source_files()); audit(files)
    if args.archive:
        archive=args.archive.resolve()
        if archive.suffix!=".zip": parser.error("Archive must have .zip extension")
        archive.parent.mkdir(parents=True,exist_ok=True);manifest={}
        with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as output:
            for p in files:
                name=p.relative_to(ROOT).as_posix();raw=p.read_bytes()
                manifest[name]=hashlib.sha256(raw).hexdigest()
                info=zipfile.ZipInfo(name, (2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                output.writestr(info,raw)
        archive.with_suffix(".manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(f"Release audit passed: {len(files)} source files; local secrets and frontend bundles checked.")

if __name__=="__main__": main()
