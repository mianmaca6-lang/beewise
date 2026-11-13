import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from .config import obtener_config
from .logging_seguro import configurar_logs
from .migraciones import exigir_esquema_actualizado
from .routers import dashboard, facturas, registros, reglas, socios, webhooks
from .routers import REVISAR_aprobaciones as aprobaciones  # REVISAR: multifirma clásica, se reemplaza por contrato Tesoreria (ver docs/PLAN_SOROBAN.md, fase 4)
from .seguridad import verificar_token

configurar_logs(obtener_config().colmena_log_nivel)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # El esquema lo gestiona Alembic (python -m scripts.preparar_bd).
    # Antes se usaba Base.metadata.create_all, que no versiona cambios.
    exigir_esquema_actualizado()
    yield


app = FastAPI(title="Colmena Financiera", lifespan=ciclo_de_vida)

# Registrar las rutas en la aplicación
app.include_router(webhooks.router)
app.include_router(dashboard.router)
app.include_router(reglas.router)
app.include_router(facturas.router)
app.include_router(aprobaciones.router)
app.include_router(registros.router)
app.include_router(socios.router)

# Resolución de ruta absoluta para la carpeta static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_STATIC = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=RUTA_STATIC), name="static")


# REVISAR: Programas/recordar.py no existe en el repositorio. Se conserva el endpoint
# para no romper integraciones, pero ahora exige X-Api-Token (antes cualquiera podía
# lanzar un subproceso en el servidor) y responde 404 si el script no existe.
@app.post("/api/ejecutar-recordatorio")
def ejecutar_script_recordar(x_api_token: Optional[str] = Header(None)):
    verificar_token(x_api_token)
    ruta_script = os.path.join(BASE_DIR, "Programas", "recordar.py")
    if not os.path.isfile(ruta_script):
        raise HTTPException(status_code=404, detail="Script de recordatorio no disponible")
    try:
        resultado = subprocess.run(
            [sys.executable, ruta_script],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        return {"status": "éxito", "salida": resultado.stdout}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "detalle": e.stderr}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="El script excedió el tiempo límite")
