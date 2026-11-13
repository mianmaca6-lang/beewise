"""Fase 0: el respaldo se crea, se verifica, detecta alteraciones y se restaura."""
import os
import sqlite3

import pytest

from app.services.respaldo import RespaldoError, respaldar, restaurar, verificar_respaldo


def _bd(ruta, filas=3):
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(filas)])
    con.commit()
    return con  # se deja abierta: el respaldo debe funcionar con la BD en uso (WAL)


def _contar(ruta):
    con = sqlite3.connect(ruta)
    try:
        return con.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        con.close()


def test_respaldo_con_bd_en_uso_y_manifiesto(tmp_path):
    con = _bd(tmp_path / "a.db")
    res = respaldar(str(tmp_path / "a.db"), str(tmp_path / "bk"))
    con.close()
    assert os.path.isfile(res.archivo) and os.path.isfile(res.manifiesto)
    datos = verificar_respaldo(res.archivo)
    assert datos["sha256"] == res.sha256 and datos["integrity_check"] == "ok"
    assert _contar(res.archivo) == 3


def test_respaldo_alterado_se_detecta(tmp_path):
    _bd(tmp_path / "a.db").close()
    res = respaldar(str(tmp_path / "a.db"), str(tmp_path / "bk"))
    with open(res.archivo, "ab") as f:
        f.write(b"basura")
    with pytest.raises(RespaldoError):
        verificar_respaldo(res.archivo)


def test_restaurar_recupera_datos_y_guarda_copia_previa(tmp_path):
    ruta = tmp_path / "a.db"
    _bd(ruta, filas=3).close()
    res = respaldar(str(ruta), str(tmp_path / "bk"))

    con = sqlite3.connect(ruta)
    con.execute("DELETE FROM t")
    con.commit()
    con.close()
    assert _contar(ruta) == 0

    previa = restaurar(res.archivo, str(ruta))
    assert _contar(ruta) == 3
    assert previa and os.path.isfile(previa) and _contar(previa) == 0
    # El respaldo original sigue intacto.
    verificar_respaldo(res.archivo)


def test_respaldar_bd_inexistente_falla(tmp_path):
    with pytest.raises(RespaldoError):
        respaldar(str(tmp_path / "no.db"), str(tmp_path / "bk"))


def test_nunca_sobrescribe(tmp_path):
    _bd(tmp_path / "a.db").close()
    r1 = respaldar(str(tmp_path / "a.db"), str(tmp_path / "bk"))
    r2 = respaldar(str(tmp_path / "a.db"), str(tmp_path / "bk"))
    assert r1.archivo != r2.archivo
