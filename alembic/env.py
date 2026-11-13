"""
Entorno de Alembic de Colmena Financiera.

- La URL sale de DATABASE_URL (app/config.py). Puede forzarse con
  `alembic -x url=sqlite:///ruta.db upgrade head` (útil en pruebas).
- render_as_batch=True: obligatorio en SQLite para ALTER TABLE.
- target_metadata = Base.metadata de app.models: permite `alembic check`
  (detecta diferencias entre modelos y migraciones).
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import obtener_config
from app import models  # noqa: F401  (registra todos los modelos en Base.metadata)
from app.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Prioridad: -x url=...  >  URL fijada por código (app/migraciones.py)  >  DATABASE_URL
url_x = context.get_x_argument(as_dictionary=True).get("url")
config.set_main_option(
    "sqlalchemy.url", url_x or config.get_main_option("sqlalchemy.url") or obtener_config().database_url
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conexion_existente = config.attributes.get("connection")
    if conexion_existente is not None:
        _ejecutar(conexion_existente)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ejecutar(connection)


def _ejecutar(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
