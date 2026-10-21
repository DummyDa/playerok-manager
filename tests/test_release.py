from scripts.check_release import source_files


def test_public_archive_includes_fonts_and_excludes_runtime_and_credentials(tmp_path):
    allowed=["README.md", ".env.example", "frontend/public/fonts/font.ttf", "frontend/scripts/qa.cjs", "backend/app/config.py"]
    private=[".env", "data/manager.sqlite3", "data/secrets.key", "data/private/env.bak", "frontend/node_modules/pkg/index.js", "frontend/.next/static/private.js", "backend/app/session.session", "backend/app/secrets.key", "backend/app/capture.har", "backend/app/debug.log", "frontend/next-env.d.ts"]
    for name in allowed+private:
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text("fixture")
    assert {p.relative_to(tmp_path).as_posix() for p in source_files(tmp_path)}==set(allowed)
