import httpx

from ..config import obtener_config


class N8nError(Exception):
    """Fallo al comunicarse con n8n."""


def _base_url() -> str:
    return obtener_config().n8n_base_url.rstrip("/")


def _timeout() -> float:
    return obtener_config().n8n_timeout


def _headers() -> dict:
    secreto = obtener_config().n8n_api_token
    token = secreto.get_secret_value() if secreto else ""
    if not token:
        raise N8nError("N8N_API_TOKEN no está configurado")
    return {"X-Api-Token": token, "Content-Type": "application/json"}


def disparar_workflow(workflow: str, payload: dict) -> dict:
    """Dispara un webhook de n8n y devuelve su respuesta JSON (o {} si no hay)."""
    headers = _headers()
    url = f"{_base_url()}/webhook/{workflow}"
    try:
        with httpx.Client(timeout=_timeout()) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {"respuesta": data}
    except httpx.HTTPError as exc:
        raise N8nError(f"Error llamando a n8n: {exc}") from exc
