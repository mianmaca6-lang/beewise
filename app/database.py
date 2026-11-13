import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import obtener_config

# Por defecto apunta a ./data/colmena.db (configurable con DATABASE_URL)
SQLALCHEMY_DATABASE_URL = obtener_config().database_url


def _asegurar_directorio_sqlite(url: str) -> None:
    u = make_url(url)
    if u.get_backend_name() == "sqlite" and u.database and u.database != ":memory:":
        carpeta = os.path.dirname(os.path.abspath(u.database))
        os.makedirs(carpeta, exist_ok=True)


def crear_motor(url: str):
    _asegurar_directorio_sqlite(url)
    motor = create_engine(url, connect_args={"check_same_thread": False})

    # Habilita WAL para que múltiples procesos (scripts y API) no bloqueen la BD
    @event.listens_for(motor, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return motor


engine = crear_motor(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
