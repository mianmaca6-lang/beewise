"""Fase 0: Alembic aplica, verifica, revierte y adopta BD anteriores sin perder datos."""
import os
import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.database import Base
from app.migraciones import (
    REVISION_BASELINE,
    EsquemaError,
    _upgrade,
    config_alembic,
    estado_esquema,
    exigir_esquema_actualizado,
    preparar_bd,
)
from tests.conftest import url_sqlite


def _diferencias(url):
    motor = create_engine(url)
    try:
        with motor.connect() as con:
            return compare_metadata(MigrationContext.configure(con, opts={"compare_type": True}), Base.metadata)
    finally:
        motor.dispose()


def test_upgrade_head_en_bd_vacia_y_esquema_coincide_con_modelos(tmp_path):
    url = url_sqlite(tmp_path / "nueva.db")
    informe = preparar_bd(url, str(tmp_path / "bk"))
    actual, head, _ = estado_esquema(url)
    assert actual == head == informe["revision_final"]
    assert informe["respaldo"] is None  # BD vacía: no hay nada que respaldar
    # Equivalente a `alembic check`: los modelos y las migraciones no divergen.
    assert _diferencias(url) == []


def test_downgrade_a_base_y_vuelta_a_head(tmp_path):
    url = url_sqlite(tmp_path / "rb.db")
    preparar_bd(url, str(tmp_path / "bk"))
    motor = create_engine(url)
    with motor.begin() as con:
        cfg = config_alembic(url)
        cfg.attributes["connection"] = con
        command.downgrade(cfg, "base")
    with motor.connect() as con:
        assert set(inspect(con).get_table_names()) <= {"alembic_version"}
    motor.dispose()
    _upgrade(url, "head")
    assert _diferencias(url) == []


def _bd_legacy(ruta):
    """Simula la BD de producción: creada por create_all (baseline) sin alembic_version."""
    url = url_sqlite(ruta)
    _upgrade(url, REVISION_BASELINE)
    con = sqlite3.connect(ruta)
    con.execute("DROP TABLE alembic_version")
    con.execute("INSERT INTO testers (token, activo) VALUES ('tester01', 1)")
    con.execute(
        "INSERT INTO transacciones (concepto, monto, tipo, cuenta_origen, cuenta_destino, fecha_hora) "
        "VALUES ('prueba', 1000, 'gasto', 'a', 'b', '2026-01-01')"
    )
    con.commit()
    con.close()
    return url


def test_bd_anterior_a_alembic_se_respalda_marca_y_conserva_datos(tmp_path):
    ruta = tmp_path / "legacy.db"
    url = _bd_legacy(ruta)
    carpeta = tmp_path / "bk"
    informe = preparar_bd(url, str(carpeta))

    assert informe["revision_inicial"] is None
    assert informe["stamp_baseline"] is True
    assert informe["respaldo"] and os.path.isfile(informe["respaldo"])
    assert os.path.isfile(informe["respaldo"] + ".json")
    assert estado_esquema(url)[0] == estado_esquema(url)[1]

    con = sqlite3.connect(ruta)
    # D4: los datos de prueba no se migran, pero tampoco se borran.
    assert con.execute("SELECT count(*) FROM transacciones").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM testers").fetchone()[0] == 1
    con.close()
    # El respaldo conserva el estado previo.
    bk = sqlite3.connect(informe["respaldo"])
    assert bk.execute("SELECT count(*) FROM transacciones").fetchone()[0] == 1
    bk.close()


def test_bd_incoherente_no_se_marca(tmp_path):
    ruta = tmp_path / "rara.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE otra (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(EsquemaError):
        preparar_bd(url_sqlite(ruta), str(tmp_path / "bk"))
    assert estado_esquema(url_sqlite(ruta))[0] is None  # no se tocó


def test_preparar_es_idempotente(tmp_path):
    url = url_sqlite(tmp_path / "idem.db")
    preparar_bd(url, str(tmp_path / "bk"))
    segundo = preparar_bd(url, str(tmp_path / "bk"))
    assert segundo["revision_inicial"] == segundo["revision_final"]
    assert segundo["respaldo"] is None


def test_arranque_rechaza_bd_sin_migrar(tmp_path):
    url = url_sqlite(tmp_path / "sin.db")
    with pytest.raises(EsquemaError):
        exigir_esquema_actualizado(url)
