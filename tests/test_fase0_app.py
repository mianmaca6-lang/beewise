"""Fase 0: la app arranca, conserva sus rutas y ya no acepta el token antiguo escrito en el código."""
import logging

from fastapi.testclient import TestClient

from app.config import reiniciar_config
from app.logging_seguro import FormateadorJSON, redactar
from app.main import app

TOKEN_ANTIGUO = "TU_TOKEN_SECRETO_COLMENA_2026"


def test_app_arranca_y_rutas_existentes_responden():
    with TestClient(app) as c:  # ejecuta el lifespan (verificación de esquema)
        assert c.get("/zine").status_code == 200
        assert c.get("/", follow_redirects=False).status_code in (302, 307)
        assert c.get("/zine/estadisticas").status_code == 200


def test_webhook_rechaza_token_antiguo_y_vacio():
    with TestClient(app) as c:
        cuerpo = {"cliente_id": 1, "nombre_archivo": "x", "texto_extraido": "texto suficientemente largo"}
        assert c.post("/api/webhooks/ingesta-factura", json=cuerpo).status_code == 401
        r = c.post("/api/webhooks/ingesta-factura", json=cuerpo, headers={"X-Api-Token": TOKEN_ANTIGUO})
        assert r.status_code == 401


def test_sin_token_configurado_falla_cerrado(monkeypatch):
    monkeypatch.setenv("COLMENA_API_TOKEN", "")
    reiniciar_config()
    try:
        with TestClient(app) as c:
            r = c.post("/api/facturas/1/enviar-n8n", headers={"X-Api-Token": ""})
            assert r.status_code == 503
    finally:
        monkeypatch.undo()
        reiniciar_config()


def test_recordatorio_exige_token(token_api):
    with TestClient(app) as c:
        assert c.post("/api/ejecutar-recordatorio").status_code == 401
        # Con token: el script no existe en el repo → 404 explícito (antes: subproceso sin auth).
        assert c.post("/api/ejecutar-recordatorio", headers={"X-Api-Token": token_api}).status_code == 404


def test_logs_redactan_secretos_y_campos_financieros():
    llave = "S" + "A" * 55
    assert llave not in redactar(f"error con {llave}")
    reg = logging.LogRecord("colmena.x", logging.INFO, __file__, 1, "msg X-Api-Token: abc", None, None)
    reg.datos = {"monto": 12500, "concepto": "almuerzo", "registro_id": 7}
    salida = FormateadorJSON().format(reg)
    assert "12500" not in salida and "almuerzo" not in salida and "abc" not in salida
    assert '"registro_id": 7' in salida
