"""Fase 1 — Backend: autenticación, validación, idempotencia y manejo de errores de Soroban."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from app.config import reiniciar_config
from app.database import SessionLocal
from app.main import app
from app.models import HistorialOperacion, RegistroPersonal, TransaccionSoroban
from app.routers.registros import obtener_publicador
from app.services.soroban_libro import (
    PublicadorNoConfigurado,
    _exigir_32_bytes,
    codigo_error_contrato,
)
from tests.utilidades import PublicadorFalso, cabeceras, cuerpo, error_soroban

URL = "/api/v1/registros"


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _con(pub):
    app.dependency_overrides[obtener_publicador] = lambda: pub
    return pub


def _contar_registros():
    with SessionLocal() as db:
        return db.query(func.count(RegistroPersonal.id)).scalar()


# ---------- Autenticación y validación ----------

def test_token_invalido_o_ausente_rechazado(cliente):
    _con(PublicadorFalso())
    antes = _contar_registros()
    assert cliente.post(URL, json=cuerpo(), headers={"Idempotency-Key": "idem-12345678"}).status_code == 401
    r = cliente.post(URL, json=cuerpo(), headers={"X-Api-Token": "malo", "Idempotency-Key": "idem-12345678"})
    assert r.status_code == 401
    assert cliente.get(f"{URL}/1").status_code == 401
    assert _contar_registros() == antes


def test_idempotency_key_obligatoria_y_con_formato(cliente, token_api):
    _con(PublicadorFalso())
    assert cliente.post(URL, json=cuerpo(), headers={"X-Api-Token": token_api}).status_code == 422
    r = cliente.post(URL, json=cuerpo(), headers={"X-Api-Token": token_api, "Idempotency-Key": "corta"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "cambio",
    [{"monto": "0"}, {"monto": "-1"}, {"tipo": "otro"}, {"fecha": "2026-11-12T10:00:00"}, {"concepto": ""},
     {"monto": "10.001"}, {"campo_extra": "x"}, {"usuario": {"canal": "whatsapp", "id_externo": "1"}}],
)
def test_validacion_de_esquema(cliente, token_api, cambio):
    _con(PublicadorFalso())
    antes = _contar_registros()
    r = cliente.post(URL, json=cuerpo(**cambio), headers=cabeceras(token_api))
    assert r.status_code == 422, r.text
    assert _contar_registros() == antes


def test_ambito_socio_no_implementado_y_no_guarda(cliente, token_api):
    pub = _con(PublicadorFalso())
    antes = _contar_registros()
    r = cliente.post(URL, json=cuerpo(ambito="socio"), headers=cabeceras(token_api))
    assert r.status_code == 501
    assert _contar_registros() == antes and pub.llamadas == []


# ---------- Idempotencia y duplicados ----------

def test_solicitud_duplicada_no_crea_otro_movimiento(cliente, token_api):
    pub = _con(PublicadorFalso())
    h = cabeceras(token_api)
    r1 = cliente.post(URL, json=cuerpo(), headers=h)
    antes = _contar_registros()
    r2 = cliente.post(URL, json=cuerpo(), headers=h)
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r2.json()["duplicado"] is True and r2.json()["id"] == r1.json()["id"]
    assert r2.json()["hash_integridad"] == r1.json()["hash_integridad"]
    assert _contar_registros() == antes
    assert len(pub.llamadas) == 1  # tampoco se publica dos veces


def test_misma_clave_con_otro_contenido_es_conflicto(cliente, token_api):
    _con(PublicadorFalso())
    h = cabeceras(token_api)
    cliente.post(URL, json=cuerpo(), headers=h)
    antes = _contar_registros()
    assert cliente.post(URL, json=cuerpo(monto="1.00"), headers=h).status_code == 409
    assert _contar_registros() == antes


def test_misma_clave_en_otro_usuario_es_independiente(cliente, token_api):
    _con(PublicadorFalso())
    h = cabeceras(token_api)
    a = cliente.post(URL, json=cuerpo(id_externo="u-A"), headers=h)
    b = cliente.post(URL, json=cuerpo(id_externo="u-B"), headers=h)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]


def test_mismo_gasto_con_otra_clave_genera_hash_distinto(cliente, token_api):
    # Dos almuerzos idénticos el mismo día son dos movimientos legítimos.
    _con(PublicadorFalso())
    a = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    b = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    assert a["hash_integridad"] != b["hash_integridad"]


# ---------- Publicación y errores de Soroban ----------

def _estado_bd(registro_id):
    with SessionLocal() as db:
        reg = db.get(RegistroPersonal, registro_id)
        tx = reg.transaccion_soroban
        hist = [
            (h.accion, h.resultado)
            for h in db.query(HistorialOperacion).filter(HistorialOperacion.entidad_id == registro_id).order_by(HistorialOperacion.id)
        ]
        return reg.estado_sincronizacion, (tx.estado if tx else None), (tx.codigo_error if tx else None), hist


def test_publicacion_exitosa_confirma(cliente, token_api):
    _con(PublicadorFalso())
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    estado, tx, _, hist = _estado_bd(r["id"])
    assert (estado, tx) == ("confirmado", "exito")
    assert hist == [("crear", "ok"), ("publicar", "iniciado"), ("publicar", "confirmado")]
    consulta = cliente.get(f"{URL}/{r['id']}", headers={"X-Api-Token": token_api}).json()
    assert consulta["estado_sincronizacion"] == "confirmado" and consulta["ledger"] is not None


def test_error_transitorio_queda_visible_y_reintentar_lo_resuelve(cliente, token_api):
    _con(PublicadorFalso(error=error_soroban("rpc_ConnectionError", True)))
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    estado, tx, codigo, _ = _estado_bd(r["id"])
    assert (estado, tx, codigo) == ("error_reintentable", "fallida", "rpc_ConnectionError")

    _con(PublicadorFalso())
    rr = cliente.post(f"{URL}/{r['id']}/reintentar", headers={"X-Api-Token": token_api})
    assert rr.status_code == 200 and rr.json()["estado_sincronizacion"] == "confirmado"
    with SessionLocal() as db:  # cada intento queda registrado
        assert db.query(TransaccionSoroban).filter(TransaccionSoroban.estado == "fallida").count() >= 1


def test_tx_ambigua_queda_como_desconocida_con_hash(cliente, token_api):
    _con(PublicadorFalso(error=error_soroban("tx_sin_confirmar", True, tx_hash="cd" * 32)))
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    estado, tx, codigo, _ = _estado_bd(r["id"])
    assert (estado, tx, codigo) == ("error_reintentable", "desconocido", "tx_sin_confirmar")


def test_error_definitivo_no_se_reintenta_automaticamente(cliente, token_api):
    _con(PublicadorFalso(error=error_soroban("simulacion_contrato_2", False)))
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    assert _estado_bd(r["id"])[0] == "error"
    assert cliente.post(f"{URL}/{r['id']}/reintentar", headers={"X-Api-Token": token_api}).status_code == 409


def test_excepcion_inesperada_no_es_silenciosa(cliente, token_api):
    _con(PublicadorFalso(error=RuntimeError("fallo raro")))
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    estado, tx, codigo, hist = _estado_bd(r["id"])
    assert (estado, tx, codigo) == ("error_reintentable", "fallida", "interno_RuntimeError")
    assert hist[-1] == ("publicar", "error_reintentable")


def test_hash_ya_registrado_on_chain_se_confirma(cliente, token_api):
    _con(PublicadorFalso(ya_existia=True))
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api)).json()
    assert _estado_bd(r["id"])[0] == "confirmado"


def test_sin_configuracion_soroban_queda_pendiente_de_reintento(cliente, token_api):
    _con(PublicadorNoConfigurado())
    r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api))
    assert r.status_code == 201
    estado, _, codigo, _ = _estado_bd(r.json()["id"])
    assert (estado, codigo) == ("error_reintentable", "soroban_no_configurado")


def test_sin_clave_de_cifrado_no_guarda_nada(cliente, token_api, monkeypatch):
    _con(PublicadorFalso())
    antes = _contar_registros()
    monkeypatch.setenv("COLMENA_CLAVE_CIFRADO", "")
    reiniciar_config()
    try:
        r = cliente.post(URL, json=cuerpo(), headers=cabeceras(token_api))
        assert r.status_code == 503
        assert _contar_registros() == antes
    finally:
        monkeypatch.undo()
        reiniciar_config()


# ---------- Cliente Soroban (sin red) ----------

def test_interfaz_soroban_rechaza_cualquier_cosa_que_no_sea_32_bytes():
    for valor in (b"x" * 31, b"x" * 33, "a" * 32, 15000, None):
        with pytest.raises(TypeError):
            _exigir_32_bytes("v", valor)
    with pytest.raises(TypeError):
        PublicadorNoConfigurado().registrar(b"\x01" * 32, b"monto=15000")


def test_extrae_codigo_de_error_del_contrato():
    assert codigo_error_contrato("HostError: Error(Contract, #1)\nEvent log...") == 1
    assert codigo_error_contrato("Error(Auth, InvalidAction)") is None


def test_libro_nuevo_arranca_vacio(tmp_path):
    # D4: en una BD recién migrada el libro personal no tiene registros de prueba.
    from sqlalchemy import create_engine, text

    from app.migraciones import preparar_bd
    from tests.conftest import url_sqlite

    url = url_sqlite(tmp_path / "nuevo.db")
    preparar_bd(url, str(tmp_path / "bk"))
    motor = create_engine(url)
    with motor.connect() as con:
        for tabla in ("registros_personales", "usuarios", "transacciones_soroban", "propuestas", "votos", "socios"):
            assert con.execute(text(f"SELECT count(*) FROM {tabla}")).scalar() == 0
    motor.dispose()
