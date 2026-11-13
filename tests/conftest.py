"""
Configuración común de pruebas.

Las variables de entorno se fijan ANTES de importar `app`, porque app.database
crea el motor al importarse. Todo ocurre en un directorio temporal: las pruebas
nunca tocan data/colmena.db ni backups/ reales.
"""
import base64
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

_TMP = tempfile.mkdtemp(prefix="colmena_pruebas_")
_BD = os.path.join(_TMP, "app.db").replace("\\", "/")

os.environ["DATABASE_URL"] = f"sqlite:///{_BD}"
os.environ["COLMENA_API_TOKEN"] = "token-de-prueba-" + "x" * 24
os.environ["COLMENA_CLAVE_CIFRADO"] = base64.b64encode(b"\x01" * 32).decode()
os.environ["COLMENA_CLAVE_CIFRADO_VERSION"] = "1"
os.environ["COLMENA_SECRETO_SEUDONIMO"] = "secreto-seudonimo-de-prueba-" + "y" * 16
os.environ["COLMENA_BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ.pop("SOROBAN_CONTRATO_LIBRO_PERSONAL", None)
os.environ.pop("SOROBAN_LLAVE_SERVICIO_ARCHIVO", None)

import pytest  # noqa: E402

from app.config import reiniciar_config  # noqa: E402

reiniciar_config()

from app.migraciones import preparar_bd  # noqa: E402

# La BD de la app de pruebas se lleva a head una sola vez.
preparar_bd(os.environ["DATABASE_URL"], os.environ["COLMENA_BACKUP_DIR"])


@pytest.fixture
def directorio_tmp(tmp_path):
    return tmp_path


def url_sqlite(ruta) -> str:
    return "sqlite:///" + str(ruta).replace("\\", "/")


@pytest.fixture
def token_api():
    return os.environ["COLMENA_API_TOKEN"]
