"""Fase 1 — Privacidad (D3): a Soroban solo llegan seudónimo y hash; local, todo cifrado."""
import base64
import glob
import os

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import RegistroPersonal, Usuario
from app.routers.registros import obtener_publicador
from app.services import cifrado
from app.services.registros_personales import aad_registro, leer_registro
from tests.utilidades import PublicadorFalso, cabeceras, cuerpo

MONTO = "98765.43"
CONCEPTO = "ConceptoSecretoXYZ"
CATEGORIA = "comida"


@pytest.fixture
def cliente_y_publicador():
    pub = PublicadorFalso()
    app.dependency_overrides[obtener_publicador] = lambda: pub
    with TestClient(app) as c:
        yield c, pub
    app.dependency_overrides.clear()


def _variantes(texto: str):
    """Formas en que un dato podría filtrarse: texto, UTF-8, hex, base64."""
    b = texto.encode("utf-8")
    return [b, b.hex().encode(), base64.b64encode(b)]


def test_soroban_recibe_solo_dos_valores_de_32_bytes(cliente_y_publicador, token_api):
    c, pub = cliente_y_publicador
    r = c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    assert r.status_code == 201, r.text
    assert len(pub.llamadas) == 1
    args = pub.llamadas[0]
    assert len(args) == 2 and all(isinstance(a, bytes) and len(a) == 32 for a in args)

    enviado = b"".join(args)
    for dato in (MONTO, "98765", CONCEPTO, CATEGORIA, "Comida", "gasto"):
        for forma in _variantes(dato):
            assert forma not in enviado, f"se filtró {dato!r}"
    # Lo enviado es exactamente seudónimo + hash guardados localmente.
    with SessionLocal() as db:
        reg = db.get(RegistroPersonal, r.json()["id"])
        assert args[0].hex() == reg.usuario.seudonimo
        assert args[1].hex() == reg.hash_integridad


def test_seudonimo_no_deriva_del_id_de_telegram(cliente_y_publicador, token_api):
    c, pub = cliente_y_publicador
    c.post("/api/v1/registros", json=cuerpo(id_externo="777888999"), headers=cabeceras(token_api))
    seudonimo = pub.llamadas[0][0]
    for forma in _variantes("777888999"):
        assert forma not in seudonimo
    with SessionLocal() as db:
        u = db.query(Usuario).filter(Usuario.seudonimo == seudonimo.hex()).one()
        assert "777888999" not in u.id_externo_hmac


def test_respuesta_http_no_contiene_datos_financieros(cliente_y_publicador, token_api):
    c, _ = cliente_y_publicador
    r = c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    texto = r.text
    assert MONTO not in texto and CONCEPTO not in texto and "Comida" not in texto
    estado = c.get(f"/api/v1/registros/{r.json()['id']}", headers={"X-Api-Token": token_api}).text
    assert MONTO not in estado and CONCEPTO not in estado


def test_archivo_sqlite_no_contiene_texto_plano(cliente_y_publicador, token_api):
    c, _ = cliente_y_publicador
    unico = "TextoUnicoQueNoDebeVerse_42"
    c.post("/api/v1/registros", json=cuerpo(concepto=unico, monto="31415.92"), headers=cabeceras(token_api))
    ruta = os.environ["DATABASE_URL"].replace("sqlite:///", "")
    contenido = b"".join(open(p, "rb").read() for p in glob.glob(ruta + "*"))  # .db, -wal, -shm
    for dato in (unico, "31415.92", "31415"):
        for forma in _variantes(dato):
            assert forma not in contenido, f"{dato!r} está en claro en SQLite"


def test_recuperacion_local_con_la_clave_correcta(cliente_y_publicador, token_api):
    c, _ = cliente_y_publicador
    r = c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    with SessionLocal() as db:
        datos = leer_registro(db, r.json()["id"])
    assert datos["monto"] == MONTO
    assert datos["concepto"] == "ConceptoSecretoXYZ almuerzo"
    assert datos["categoria"] == CATEGORIA
    assert datos["fecha"] == "2026-11-12T17:30:00Z"  # normalizada a UTC


def test_clave_incorrecta_no_descifra(cliente_y_publicador, token_api):
    c, _ = cliente_y_publicador
    r = c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    otra = cifrado.clave_desde_base64(base64.b64encode(b"\x02" * 32).decode(), 1)
    with SessionLocal() as db:
        with pytest.raises(cifrado.ErrorDescifrado):
            leer_registro(db, r.json()["id"], clave=otra)


def test_blob_movido_a_otro_usuario_no_descifra(cliente_y_publicador, token_api):
    c, _ = cliente_y_publicador
    r = c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    with SessionLocal() as db:
        reg = db.get(RegistroPersonal, r.json()["id"])
        clave = cifrado.cargar_clave()
        with pytest.raises(cifrado.ErrorDescifrado):
            cifrado.descifrar(reg.contenido_cifrado, aad_registro(reg.usuario_id + 1), clave)


def test_logs_no_contienen_datos_financieros(cliente_y_publicador, token_api):
    import io
    import logging

    from app.logging_seguro import FormateadorJSON

    buffer = io.StringIO()
    manejador = logging.StreamHandler(buffer)
    manejador.setFormatter(FormateadorJSON())
    logger = logging.getLogger("colmena")
    logger.addHandler(manejador)
    try:
        c, _ = cliente_y_publicador
        c.post("/api/v1/registros", json=cuerpo(), headers=cabeceras(token_api))
    finally:
        logger.removeHandler(manejador)
    salida = buffer.getvalue()
    assert "registro personal creado" in salida  # sí hay logs
    assert MONTO not in salida and CONCEPTO not in salida and "Comida" not in salida


def test_clave_de_cifrado_invalida_se_rechaza():
    with pytest.raises(cifrado.ErrorClave):
        cifrado.clave_desde_base64(base64.b64encode(b"corta").decode(), 1)
    with pytest.raises(cifrado.ErrorClave):
        cifrado.clave_desde_base64("no-es-base64!!", 1)
    assert "material=***" in repr(cifrado.cargar_clave())
