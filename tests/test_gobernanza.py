"""
Pruebas de socios, credenciales, propuestas y votos.

Usan una BD SQLite temporal en /tmp (nunca data/colmena.db) y valores de prueba
generados en cada ejecución: no dependen de .env ni de secretos reales.
"""
import hashlib
import os
import tempfile

_CARPETA = tempfile.mkdtemp(prefix="colmena_test_")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_CARPETA}/colmena.db",
        "COLMENA_BACKUP_DIR": f"{_CARPETA}/backups",
        "COLMENA_API_TOKEN": "token-admin-de-prueba-0123456789",
        "COLMENA_CLAVE_CIFRADO": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "COLMENA_SECRETO_SEUDONIMO": "s" * 40,
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from stellar_sdk import Keypair  # noqa: E402

from app.config import reiniciar_config  # noqa: E402
from app.migraciones import preparar_bd  # noqa: E402

reiniciar_config()
preparar_bd(url=os.environ["DATABASE_URL"], carpeta_respaldos=os.environ["COLMENA_BACKUP_DIR"])

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CredencialSocio  # noqa: E402

ADMIN = {"X-Api-Token": os.environ["COLMENA_API_TOKEN"]}


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def bd_limpia():
    """Cada prueba parte de un padrón vacío (solo borra tablas de gobernanza de la BD de prueba)."""
    from sqlalchemy import text

    with SessionLocal() as db:
        for tabla in ("votos", "propuesta_socios_elegibles", "propuestas", "credenciales_socios", "socios"):
            db.execute(text(f"DELETE FROM {tabla}"))
        db.commit()
    yield


def _socio(cliente, nombre):
    r = cliente.post(
        "/api/v1/socios",
        headers=ADMIN,
        json={"nombre": nombre, "public_key": Keypair.random().public_key},
    )
    assert r.status_code == 201, r.text
    socio_id = r.json()["id"]
    cred = cliente.post(f"/api/v1/socios/{socio_id}/credenciales", headers=ADMIN, json={"nombre": nombre})
    assert cred.status_code == 201, cred.text
    return {"id": socio_id, "token": cred.json()["token"]}


def _h(socio):
    return {"X-Socio-Token": socio["token"]}


def _propuesta(cliente, proponente, modalidad="mayoria_simple"):
    r = cliente.post(
        "/api/v1/propuestas",
        headers=_h(proponente),
        json={"tipo": "compra", "modalidad": modalidad, "datos_publicos": {"titulo": "Compra de prueba"}},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_token_se_guarda_solo_como_hash(cliente):
    s = _socio(cliente, "Hash")
    with SessionLocal() as db:
        cred = db.query(CredencialSocio).filter(CredencialSocio.socio_id == s["id"]).one()
        assert cred.token_hash == hashlib.sha256(s["token"].encode()).hexdigest()
        assert cred.token_hash != s["token"]


def test_alta_de_socio_valida_public_key_y_no_duplica(cliente):
    r = cliente.post("/api/v1/socios", headers=ADMIN, json={"nombre": "X", "public_key": "G" + "A" * 55})
    assert r.status_code == 422
    clave = Keypair.random().public_key
    assert cliente.post("/api/v1/socios", headers=ADMIN, json={"nombre": "Y", "public_key": clave}).status_code == 201
    assert cliente.post("/api/v1/socios", headers=ADMIN, json={"nombre": "Y2", "public_key": clave}).status_code == 409


def test_admin_requerido_y_token_socio_invalido(cliente):
    assert cliente.post("/api/v1/socios", json={"nombre": "Z", "public_key": Keypair.random().public_key}).status_code == 401
    assert cliente.get("/api/v1/propuestas", headers={"X-Socio-Token": "no-existe"}).status_code == 401
    assert cliente.get("/api/v1/propuestas").status_code == 401


def test_credencial_revocada_expirada_o_socio_inactivo_no_autentica(cliente):
    s = _socio(cliente, "Revocable")
    assert cliente.get("/api/v1/propuestas", headers=_h(s)).status_code == 200
    with SessionLocal() as db:
        db.query(CredencialSocio).filter(CredencialSocio.socio_id == s["id"]).update({"revocado_en": "2026-01-01T00:00:00+00:00"})
        db.commit()
    assert cliente.get("/api/v1/propuestas", headers=_h(s)).status_code == 401

    e = _socio(cliente, "Expirada")
    with SessionLocal() as db:
        db.query(CredencialSocio).filter(CredencialSocio.socio_id == e["id"]).update({"expira_en": "2020-01-01T00:00:00+00:00"})
        db.commit()
    assert cliente.get("/api/v1/propuestas", headers=_h(e)).status_code == 401


def test_mayoria_simple_con_tres_socios_aprueba_con_dos_votos(cliente):
    a, b, _ = _socio(cliente, "A"), _socio(cliente, "B"), _socio(cliente, "C")
    p = _propuesta(cliente, a)
    assert p["socios_habilitados"] == 3
    assert p["votos_requeridos"] == 2
    assert p["estado"] == "abierta"

    v1 = cliente.post(f"/api/v1/propuestas/{p['id']}/votos", headers=_h(a), json={"a_favor": True})
    assert v1.status_code == 201 and v1.json()["mi_voto"] is True
    assert cliente.post(f"/api/v1/propuestas/{p['id']}/votos", headers=_h(a), json={"a_favor": True}).status_code == 409

    v2 = cliente.post(f"/api/v1/propuestas/{p['id']}/votos", headers=_h(b), json={"a_favor": True})
    assert v2.status_code == 201
    assert v2.json()["estado"] == "aprobada"
    assert v2.json()["a_favor"] == 2


def test_socio_agregado_despues_no_vota_en_propuestas_anteriores(cliente):
    base = [_socio(cliente, f"Base{i}") for i in range(3)]
    anterior = _propuesta(cliente, base[0])
    assert anterior["socios_habilitados"] == 3
    nuevo = _socio(cliente, "Nuevo")

    r = cliente.post(f"/api/v1/propuestas/{anterior['id']}/votos", headers=_h(nuevo), json={"a_favor": True})
    assert r.status_code == 403

    posterior = _propuesta(cliente, base[0])
    assert posterior["socios_habilitados"] == 4
    assert posterior["votos_requeridos"] == 3
    ok = cliente.post(f"/api/v1/propuestas/{posterior['id']}/votos", headers=_h(nuevo), json={"a_favor": False})
    assert ok.status_code == 201


def test_unanimidad_rechaza_con_un_voto_en_contra(cliente):
    socios = [_socio(cliente, f"U{i}") for i in range(2)]
    p = _propuesta(cliente, socios[0], modalidad="unanimidad")
    assert p["votos_requeridos"] == p["socios_habilitados"] == 2
    r = cliente.post(f"/api/v1/propuestas/{p['id']}/votos", headers=_h(socios[0]), json={"a_favor": False})
    assert r.status_code == 201
    assert r.json()["estado"] == "rechazada"
    # Votar sobre una propuesta ya cerrada
    assert cliente.post(f"/api/v1/propuestas/{p['id']}/votos", headers=_h(socios[1]), json={"a_favor": True}).status_code == 409


def test_listado_y_detalle_con_conteo(cliente):
    s = _socio(cliente, "Lector")
    p = _propuesta(cliente, s)
    lista = cliente.get("/api/v1/propuestas", headers=_h(s))
    assert lista.status_code == 200 and any(x["id"] == p["id"] for x in lista.json())
    d = cliente.get(f"/api/v1/propuestas/{p['id']}", headers=_h(s))
    assert d.status_code == 200
    body = d.json()
    assert body["a_favor"] + body["en_contra"] + body["pendientes"] == body["socios_habilitados"]
    assert cliente.get("/api/v1/propuestas/999999", headers=_h(s)).status_code == 404
