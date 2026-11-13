import secrets
from typing import Optional

from fastapi import HTTPException, status

from .config import obtener_config


def verificar_token(x_api_token: Optional[str]) -> None:
    """
    Valida el header X-Api-Token contra COLMENA_API_TOKEN.

    - 503 si el servidor no tiene token configurado (fail-closed: antes existía
      un valor por defecto escrito en el código, que era público).
    - 401 si el token no coincide (comparación en tiempo constante).
    """
    esperado = obtener_config().colmena_api_token
    if esperado is None or not esperado.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticación no configurada en el servidor",
        )
    if not secrets.compare_digest((x_api_token or "").encode(), esperado.get_secret_value().encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token no autorizado")
