"""Initialize the owner password from an interactive local/SSH terminal only."""
from pathlib import Path
import sys
import getpass
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import Config
from backend.app.db import Database,Base,Secret
from backend.app.auth import password_hash


def main():
    if not sys.stdin.isatty():raise SystemExit("Откройте интерактивный терминал; пароль не принимается из файла или аргументов.")
    db=Database(Config());Base.metadata.create_all(db.engine)
    if db.secret("password"):raise SystemExit("Пароль уже существует. Измените его в панели.")
    password=getpass.getpass("Новый пароль администратора (10–128 символов): ")
    if not 10<=len(password)<=128:raise SystemExit("Нужно от 10 до 128 символов")
    if getpass.getpass("Повторите пароль: ")!=password:raise SystemExit("Пароли не совпали")
    # A concurrent initializer cannot replace a password.
    from sqlalchemy.exc import IntegrityError
    try:
        with db.tx() as session:
            session.add(Secret(name="password",value=db.cipher.encrypt(password_hash(password).encode()).decode()))
    except IntegrityError:raise SystemExit("Пароль уже создан другим запросом")
    print("Пароль создан. Войдите в панель.")


if __name__=="__main__":main()
