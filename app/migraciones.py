"""
Utilidades de esquema sobre Alembic.

Escenarios que cubre `preparar_bd`:
1. BD nueva (no existe o está vacía)          → upgrade head.
2. BD anterior a Alembic (tablas creadas por
   create_all, sin tabla alembic_version)     → respaldo → verificar que tiene las
                                                tablas de la baseline → stamp 0001
                                                → upgrade head.
3. BD ya gestionada por Alembic               → respaldo → upgrade head.

Nunca borra datos. Las migraciones de este proyecto solo agregan tablas/columnas;
si alguna futura migración fuera destructiva debe documentarse aparte.
"""
import os
from typing import Optional, Tuple

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from .config import obtener_config
from .logging_seguro import obtener_logger
from .services.respaldo import respaldar

log = obtener_logger("migraciones")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVISION_BASELINE = "0001"
TABLAS_BASELINE = {
    "clientes", "reglas_clasificacion", "registros_archivos", "testers", "logs_telemetria",
    "transacciones", "aprobaciones", "firmas_aprobacion",
}


class EsquemaError(RuntimeError):
    pass


def config_alembic(url: str) -> Config:
    cfg = Config(os.path.join(RAIZ, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(RAIZ, "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def revision_head(url: str) -> str:
    return ScriptDirectory.from_config(config_alembic(url)).get_current_head()


def estado_esquema(url: str) -> Tuple[Optional[str], str, set]:
    """(revisión actual o None, revisión head, tablas existentes)."""
    motor = create_engine(url)
    try:
        with motor.connect() as con:
            actual = MigrationContext.configure(con).get_current_revision()
            tablas = set(inspect(con).get_table_names())
    finally:
        motor.dispose()
    return actual, revision_head(url), tablas


def _ruta_sqlite(url: str) -> Optional[str]:
    u = make_url(url)
    if u.get_backend_name() == "sqlite" and u.database and u.database != ":memory:":
        return u.database
    return None


def _upgrade(url: str, revision: str = "head") -> None:
    motor = create_engine(url)
    try:
        with motor.begin() as con:
            cfg = config_alembic(url)
            cfg.attributes["connection"] = con
            command.upgrade(cfg, revision)
    finally:
        motor.dispose()


def _stamp(url: str, revision: str) -> None:
    motor = create_engine(url)
    try:
        with motor.begin() as con:
            cfg = config_alembic(url)
            cfg.attributes["connection"] = con
            command.stamp(cfg, revision)
    finally:
        motor.dispose()


def preparar_bd(url: Optional[str] = None, carpeta_respaldos: Optional[str] = None) -> dict:
    url = url or obtener_config().database_url
    carpeta_respaldos = carpeta_respaldos or obtener_config().colmena_backup_dir
    ruta = _ruta_sqlite(url)
    if ruta:
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)

    actual, head, tablas = estado_esquema(url)
    informe = {"revision_inicial": actual, "head": head, "respaldo": None, "stamp_baseline": False}

    if actual == head:
        informe["revision_final"] = head
        return informe

    tablas_usuario = tablas - {"alembic_version"}
    if ruta and tablas_usuario:
        res = respaldar(ruta, carpeta_respaldos, etiqueta="pre-migracion")
        informe["respaldo"] = res.archivo
        log.info("respaldo previo a migración creado", extra={"datos": {"archivo": res.archivo}})

    if actual is None and tablas_usuario:
        faltantes = TABLAS_BASELINE - tablas_usuario
        if faltantes:
            raise EsquemaError(
                "La BD existente no coincide con la baseline (faltan tablas: "
                + ", ".join(sorted(faltantes))
                + "). No se marca automáticamente; revisar a mano."
            )
        _stamp(url, REVISION_BASELINE)
        informe["stamp_baseline"] = True

    _upgrade(url, "head")
    informe["revision_final"] = estado_esquema(url)[0]
    log.info("esquema actualizado", extra={"datos": {k: v for k, v in informe.items() if k != "respaldo"}})
    return informe


def exigir_esquema_actualizado(url: Optional[str] = None) -> None:
    """Se llama al arrancar la API: si la BD no está en head, se niega a arrancar."""
    url = url or obtener_config().database_url
    actual, head, _ = estado_esquema(url)
    if actual != head:
        raise EsquemaError(
            f"La base de datos está en la revisión {actual!r} y el código espera {head!r}. "
            "Ejecuta: python -m scripts.preparar_bd"
        )
