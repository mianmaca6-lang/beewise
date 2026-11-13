# Blockchain-ZNE — Colmena Financiera

Arquitectura: **Telegram → n8n → FastAPI → Soroban**. Solo FastAPI firma y habla con Soroban.

- Puesta en marcha, migraciones, respaldos y claves: [docs/OPERACION.md](docs/OPERACION.md)
- Estado de Fases 0 y 1 (auditoría, cambios, pruebas, pendientes): [docs/INFORME_FASE_0_1.md](docs/INFORME_FASE_0_1.md)
- Libro de socios y votaciones (diseño): [docs/ARQUITECTURA_SOCIOS.md](docs/ARQUITECTURA_SOCIOS.md)
- Contrato `LibroPersonal`: [contratos/](contratos/)

```bash
pip install -r requirements-dev.txt && cp .env.example .env   # completar secretos
python -m scripts.preparar_bd && uvicorn app.main:app --port 8001
pytest                                  # pruebas backend
cd contratos && cargo test              # pruebas del contrato
```

### Registros personales (n8n): 503, idempotencia y diagnóstico

- **503 "Configuración de privacidad incompleta"**: la causa real va al log (`colmena.registros`, campo `datos.problemas`). Para verla sin arrancar el servidor, desde la carpeta del `.env`: `python -c "import sys; sys.path.insert(0,'.'); from app.services.diagnostico import estado_privacidad as e; print(e())"`. Solo muestra nombres de variables y mensajes, nunca valores.
- **`.env` relativo al cwd**: `env_file=".env"` se resuelve contra el directorio desde el que arranca el proceso, no contra la raíz del repo. Arranca siempre desde `App/` con su `.env` presente.
- **Clave de cifrado**: `COLMENA_CLAVE_CIFRADO` debe decodificar a exactamente 32 bytes (base64). Generar con `python -c "from app.services.cifrado import generar_clave_base64 as g; print(g())"`.
- **Secreto de seudónimos**: la variable se llama `COLMENA_SECRETO_SEUDONIMO` (≥ 32 caracteres). Generar con `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Idempotencia**: la huella de la solicitud incluye la fecha (normalizada a segundos UTC). Un reintento de n8n con la misma `Idempotency-Key` pero otra fecha responde **409**. Por eso n8n debe enviar la fecha del mensaje de Telegram (`message.date`) y no `$now`, y la clave debe derivarse del mensaje (`telegram-{from.id}-{message_id}`).
- **Soroban sin configurar**: el registro queda en `error_reintentable` con código `soroban_no_configurado`. Es comportamiento esperado, no un fallo.

---

## (REVISAR) Aprobaciones multifirma clásicas en Stellar (2 de 3)

> Flujo anterior basado en `manage_data`. Se reemplaza por el contrato de socios en una fase posterior.

Puerto de la API: **8001** (`docker compose up --build`). Copia `.env.example` a `.env` y completa:

| Variable | Descripción |
|---|---|
| `COLMENA_API_TOKEN` | Token para el header `X-Api-Token` |
| `N8N_BASE_URL`, `N8N_API_TOKEN`, `N8N_TIMEOUT` | Conexión a n8n (`/webhook/{workflow}`) |
| `STELLAR_NETWORK` | `testnet` (por defecto) o `public` |
| `STELLAR_HORIZON_URL` | URL de Horizon |
| `STELLAR_CUENTA_MULTISIG` | Cuenta pública (G...) con los 3 socios como firmantes |
| `STELLAR_SOCIOS` | Exactamente 3 llaves públicas separadas por coma |
| `STELLAR_UMBRAL` | Firmas requeridas (por defecto 2) |
| `APROBACION_TTL_HORAS` | Vigencia de la aprobación (por defecto 72) |

Las llaves secretas nunca se guardan en el servidor ni en disco.

Configuración inicial (una sola vez, fuera del servidor):
`STELLAR_SETUP_SECRET=S... STELLAR_SOCIOS=G1,G2,G3 python scripts/REVISAR_stellar_setup.py`

Flujo:
1. `POST /api/aprobaciones` con `{"registro_id": 1, "monto": 1000}` crea la aprobación y devuelve `xdr_base`.
2. Cada socio firma localmente: `SOCIO_SECRET=S... python scripts/REVISAR_stellar_firmar.py "<xdr_base>"`.
3. Se sube cada XDR firmado con `POST /api/aprobaciones/{id}/firmas` (`{"xdr_firmado": "..."}`).
4. Con 2 firmas válidas el estado pasa a `aprobada`; `POST /api/aprobaciones/{id}/enviar` la envía a Horizon.

Solo puede haber una aprobación abierta a la vez (comparten el número de secuencia de la cuenta).
