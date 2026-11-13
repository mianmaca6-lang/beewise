# Informe: Fases 0 y 1. Colmena Financiera

Fecha: 2026-11-12 · Entorno de trabajo: Windows 11, Python 3.13.5 (Anaconda + `.venv`), Rust 1.99.1 (toolchain GNU), soroban-sdk 29.0.1.

Leyenda: ✅ **Implementado y probado** · 🟡 **Implementado, no probado** · ⏳ **Pendiente**

---

## 1. Estado inicial del proyecto (auditoría)

### Arquitectura encontrada

- **Backend:** FastAPI + SQLAlchemy + SQLite (WAL) en `data/colmena.db`. Las tablas se creaban con `create_all` y **no había Alembic**.
- **Routers:**
  - `dashboard`: vistas Zine y login de testers.
  - `webhooks`: ingesta de facturas desde n8n.
  - `facturas`: envío a n8n.
  - `reglas`: memoria de clasificación.
  - `aprobaciones`: multifirma clásica de Stellar con `manage_data`, renombrada `REVISAR_`.
- **n8n:** solo un cliente saliente (`n8n_client.py`) y el webhook de entrada. **No hay workflows exportados en el repositorio.**
- **Telegram:** **ninguna integración en el código** (solo un comentario).
- **Stellar/Soroban:** solo la multifirma clásica vía Horizon. **No había contratos Soroban.**
- **Git:** la carpeta **no es un repositorio Git** (no tiene `.git`); parece descargada como ZIP de GitHub. Por eso no se pudo comprobar qué archivos están rastreados.
- **SQLite:** **no había ninguna BD local** (`data/` no existía). La BD real está en el servidor, en el volumen de Docker.

### Problemas y riesgos detectados

| # | Hallazgo | Severidad | Tratamiento |
|---|---|---|---|
| 1 | `Base Datos/` (audios y transcripciones de WhatsApp) **no estaba en `.gitignore`**. Como viene dentro del ZIP de GitHub, **probablemente ya está publicada** en el repositorio remoto | Alta | Añadida a `.gitignore` y `.dockerignore`. No se borró ningún archivo. ⏳ Purgar el historial remoto requiere tu autorización |
| 2 | Token `TU_TOKEN_SECRETO_COLMENA_2026` escrito en el código (`seguridad.py` y `webhooks.py`) | Alta | Eliminado. Sin `COLMENA_API_TOKEN` la API responde 503 (fail-closed) |
| 3 | `/api/ejecutar-recordatorio` lanzaba un subproceso **sin autenticación**, y su script no existe | Alta | Ahora exige token, responde 404 si el script no está y tiene un timeout de 120 s |
| 4 | `Dockerfile` hace `COPY . /app/` sin `.dockerignore`, así que `.env`, la BD y `Base Datos/` entraban en la imagen | Alta | Se creó `.dockerignore` |
| 5 | Sin Alembic: no había versionado del esquema ni rollback | Media | Alembic con baseline + `stamp` automático para las BD existentes |
| 6 | Los tokens de testers están en claro y son adivinables (`tester01..20`) | Media | ⏳ No se tocó: es la autenticación de la web Zine y está fuera del alcance de las fases 0-1 |
| 7 | Montos en `Float` en la tabla `transacciones` | Baja | El libro nuevo usa `Decimal` normalizado a 2 decimales. Los datos de prueba no se migran (D4) |

### Componentes reutilizados

`verificar_token`, el patrón de configuración por entorno, `database.get_db`, los pragmas WAL, `stellar-sdk` y la estructura de routers. La web Zine y el pipeline de facturas/n8n **se conservan sin cambios funcionales**.

### Código duplicado o en conflicto

- Había dos implementaciones de verificación de token (`webhooks.py` y `seguridad.py`). Se unificaron en una.
- `REVISAR_zne-stellar.patch` ya estaba aplicado.
- Las plantillas `REVISAR_*` estaban vacías.

---

## 2. Cambios implementados

### Fase 0

| Cambio | Estado |
|---|---|
| `.gitignore` endurecido: `Base Datos/`, `data/`, `backups/`, `*.db`, `.env*` (excepto `.env.example`), `secrets/`, llaves, `contratos/target/` | ✅ (revisado; no hay Git para `git check-ignore`) |
| `.dockerignore` | ✅ |
| Configuración centralizada (`app/config.py`, pydantic-settings, secretos como `SecretStr`) | ✅ |
| Token sin valor por defecto (fail-closed) | ✅ |
| Alembic: `env.py` integrado con los modelos y `render_as_batch`. Migración `0001` baseline generada por autogenerate desde los modelos existentes | ✅ |
| `preparar_bd`: respaldo → `stamp 0001` si la BD es anterior a Alembic → `upgrade head`. Idempotente | ✅ |
| La API se niega a arrancar si el esquema no está en head | ✅ |
| Respaldo, verificación y restauración de SQLite (API de backup, SHA-256, `integrity_check`, manifiesto, nunca sobrescribe, copia `pre-restauracion`) | ✅ |
| Logs JSON estructurados con redacción de llaves `S...`, tokens y campos financieros | ✅ |
| `/api/ejecutar-recordatorio` protegido | ✅ |
| Docker: `preparar_bd` antes de `uvicorn`; volúmenes `backups/` y `secrets/` (solo lectura) | 🟡 (no hay Docker en este equipo) |
| Backup D4 de la BD real del servidor | ⏳ No hay BD local. El comando está en `docs/OPERACION.md` §3 y `preparar_bd` lo hace automáticamente al desplegar |

### Fase 1

| Cambio | Estado |
|---|---|
| **Modelos:**<br>- `usuarios`: HMAC del id de Telegram + seudónimo aleatorio.<br>- `registros_personales`: contenido cifrado, hash, estado, idempotencia y huella HMAC.<br>- `transacciones_soroban`: cada intento.<br>- `historial_operaciones`: auditoría.<br>- `socios`, `propuestas` (con foto del umbral) y `votos` (`UNIQUE` por socio): estructura espejo para D7.<br>Migración `0002`. | ✅ |
| **Cifrado:** AES-256-GCM (`cryptography`), clave por entorno, nonce de 96 bits, AAD que liga cada blob a su usuario | ✅ |
| **Hash:** representación canónica v1 (NFC, espacios, minúsculas en categoría, monto con 2 decimales, fecha en UTC) + nonce secreto de 256 bits + separación de dominio + SHA-256. La especificación está en `app/services/integridad.py` | ✅ |
| **`POST /api/v1/registros`:** JSON, `X-Api-Token`, `Idempotency-Key`, validación estricta (`extra=forbid`), `ambito=socio` → 501 sin guardar | ✅ |
| **Publicación en segundo plano** con estado explícito (`pendiente → enviado → confirmado / error_reintentable / error`) | ✅ (con publicador simulado) |
| `GET /api/v1/registros/{id}` y `POST /api/v1/registros/{id}/reintentar` | ✅ |
| **Contrato `LibroPersonal`** (Rust, soroban-sdk 29.0.1): `registrar`, `existe`, `obtener`, `verificar`, `cambiar_escritor`, `escritor` y `admin`; evento `registro` | ✅ con `cargo test` |
| WASM de release (`stellar contract build`) | ⏳ Requiere stellar-cli, cuya descarga fue bloqueada por los permisos de este entorno |
| Cliente real de Soroban (`PublicadorSoroban`: simular → firmar → enviar → confirmar; idempotente por `YaRegistrado`) | 🟡 Sin contrato desplegado no se pudo probar contra Testnet |
| Despliegue en Testnet (`contratos/scripts/desplegar_testnet.sh`) | ⏳ No ejecutado (infraestructura externa) |
| Diseño del libro de socios y D7 (`docs/ARQUITECTURA_SOCIOS.md`) y `votos_requeridos()` | ✅ La función está probada; el contrato está ⏳ |

---

## 3. Archivos creados

`.dockerignore`, `.env.example`, `pytest.ini`, `requirements-dev.txt`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/README`, `alembic/versions/0001_baseline_esquema_existente.py`, `alembic/versions/0002_fase1_libro_personal_y_estructura_socios.py`, `app/config.py`, `app/logging_seguro.py`, `app/migraciones.py`, `app/routers/registros.py`, `app/services/{respaldo,cifrado,integridad,identidad,soroban_libro,registros_personales,votaciones}.py`, `scripts/preparar_bd.py`, `scripts/respaldo_bd.py`, `contratos/Cargo.toml`, `contratos/Cargo.lock`, `contratos/libro_personal/{Cargo.toml,src/lib.rs,src/test.rs}`, `contratos/scripts/desplegar_testnet.sh`, `tests/{__init__,conftest,utilidades,test_fase0_migraciones,test_fase0_respaldo,test_fase0_app,test_fase1_privacidad,test_fase1_integridad,test_fase1_api}.py`, `docs/{OPERACION,ARQUITECTURA_SOCIOS,INFORME_FASE_0_1}.md`.

## 4. Archivos modificados

| Archivo | Cambio |
|---|---|
| `.gitignore` | Endurecido |
| `app/main.py` | Sin `create_all`; *lifespan* que verifica el esquema; logs; router de registros; recordatorio protegido |
| `app/database.py` | URL desde la configuración; crea la carpeta de la BD; `foreign_keys=ON`; `busy_timeout` |
| `app/seguridad.py` | Sin token por defecto; 503 si no hay token configurado |
| `app/routers/webhooks.py` | Usa `verificar_token` |
| `app/models.py` | Modelos de la fase 1 añadidos; los existentes no se tocan |
| `crear_testers.py` | Exige el esquema migrado en lugar de `create_all` |
| `requirements.txt` | Versiones mínimas; nuevas dependencias |
| `Dockerfile`, `docker-compose.yml` | Migración al arrancar; volúmenes `backups/` y `secrets/` |
| `README.md`, `docs/PLAN_SOROBAN.md` | Enlaces; aviso de que D3, D4 y D7 reemplazan partes del plan anterior |

## 5. Dependencias

- **Nuevas en Python:** `alembic` 1.20.1, `pydantic-settings` 2.6.1 y `cryptography` 44.0.1. Para desarrollo, `pytest` 8.3.4.
- **Instaladas en `.venv`:** `fastapi` 0.162.5, `uvicorn` 0.54.0, `python-multipart` 0.0.33, `alembic` y `stellar-sdk` 17.0.1. El resto venía con Anaconda.
- **Rust:** toolchain estable 1.99.1 (GNU) en el perfil del usuario (`~/.cargo`), con el target `wasm32v1-none`. Crate `soroban-sdk = "=29.0.1"`.

## 6. Migraciones

| Revisión | Contenido | Pruebas |
|---|---|---|
| `0001` | Baseline: las 8 tablas existentes, idénticas a `create_all` | upgrade, downgrade, `stamp` de una BD anterior a Alembic con datos (los datos se conservan) |
| `0002` | Tablas nuevas de la fase 1 (solo creación). **Su `downgrade` borra esas tablas**, y así consta en el archivo | `alembic check` → "No new upgrade operations detected" |

## 7. Pruebas ejecutadas

### pytest: 82 pruebas, 82 pasan

**Fase 0 (16):**
- Migraciones: BD vacía → head, `check` sin diferencias, downgrade/upgrade, BD anterior a Alembic con respaldo + `stamp` sin pérdida de datos, BD incoherente sin tocar, idempotencia y arranque rechazado sin migrar.
- Respaldo: con WAL activo, manifiesto, alteración detectada, restauración con copia previa, nunca sobrescribe.
- App: arranca y conserva sus rutas, rechaza el token antiguo, fail-closed sin token, recordatorio protegido, logs redactados.

**Privacidad (9):**
- A Soroban solo llegan 2 × 32 bytes, sin monto, categoría, concepto ni tipo en ninguna codificación.
- El seudónimo no deriva del id de Telegram.
- La respuesta HTTP no contiene datos financieros.
- **El archivo SQLite (incluidos `-wal` y `-shm`) no contiene el concepto ni el monto en claro.**
- El registro se recupera con la clave correcta; con una clave incorrecta, falla.
- Un blob movido a otro usuario no se descifra.
- Los logs no contienen datos financieros.
- Una clave de cifrado inválida se rechaza.

**Integridad (32):**
- Mismo registro normalizado → mismo hash (incluidas variantes Unicode, mayúsculas y zona horaria).
- 8 tipos de modificación cambian el hash.
- El nonce hace que el mismo gasto produzca hashes distintos.
- 11 casos de entrada inválida.
- D7: tabla n = 1…7 para ambas modalidades.

**Backend (25):**
- 401 con token inválido o ausente; `Idempotency-Key` obligatoria; 8 casos de validación de esquema; `ambito=socio` → 501 sin guardar.
- Una solicitud duplicada no crea otro registro ni se publica dos veces; la misma clave con otro contenido → 409; la clave es por usuario; dos gastos idénticos con claves distintas son dos registros.
- Éxito confirmado con historial completo.
- Error transitorio → visible → reintentar lo confirma.
- Transacción ambigua → `desconocido` con su hash.
- Error definitivo → `error` y reintentar → 409.
- Excepción inesperada → `error_reintentable`.
- Hash ya registrado on-chain → `confirmado`.
- Sin configuración de Soroban → `error_reintentable`.
- Sin clave de cifrado → 503 sin guardar.
- El cliente Soroban rechaza cualquier valor que no sea de 32 bytes.
- El libro nuevo arranca vacío (D4).

### cargo test: 12 pruebas, 12 pasan

- Registrar, consultar y verificar.
- `verificar` falla con otro seudónimo o con un hash alterado.
- Duplicado rechazado sin sobrescribir.
- Valores en cero rechazados.
- `registrar` exige la firma exacta del escritor; un tercero o la ausencia de firma no registran.
- El admin rota al escritor y el anterior pierde el permiso; el escritor no puede rotarse a sí mismo.
- El evento contiene solo seudónimo, hash y ledger.
- Se extiende el TTL.
- **La especificación pública del contrato solo acepta `BytesN<32>` o `Address`, y ningún parámetro se llama monto, categoría, concepto, descripción, fecha ni tipo.**

### Pruebas manuales de punta a punta

- `preparar_bd` (vacía y repetida).
- `respaldo_bd respaldar/verificar`.
- `alembic check/current/history`.
- `uvicorn` real: `POST` → 201 `pendiente` → `error_reintentable` (sin Soroban); la repetición → 200; `/zine` → 200.

## 8. Configuración del `.env`

Ver `.env.example`. Variables obligatorias para el libro personal:
- `COLMENA_API_TOKEN`
- `COLMENA_CLAVE_CIFRADO` (32 bytes en base64)
- `COLMENA_SECRETO_SEUDONIMO` (≥ 32 caracteres)

Para publicar en Soroban:
- `SOROBAN_CONTRATO_LIBRO_PERSONAL`
- `SOROBAN_LLAVE_SERVICIO_ARCHIVO` (ruta a un archivo; la llave no va en el `.env`)

## 9. Backend

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env            # completar
python -m scripts.preparar_bd
uvicorn app.main:app --port 8001
python -m pytest
```

**Contrato que debe implementar n8n (nodo HTTP Request):**

```http
POST /api/v1/registros
X-Api-Token: <COLMENA_API_TOKEN>
Idempotency-Key: telegram-<chat_id>-<message_id>        # estable por mensaje: los reintentos de n8n no duplican
Content-Type: application/json

{"ambito":"personal","usuario":{"canal":"telegram","id_externo":"<chat_id>"},
 "tipo":"gasto","monto":"12000","moneda":"COP","categoria":"comida",
 "concepto":"almuerzo","fecha":"2026-11-12T12:30:00-05:00"}
```

Respuestas:
- `201`: creado.
- `200`: repetición con `"duplicado": true`.
- `401`: token inválido o ausente.
- `409`: clave reutilizada con otro contenido.
- `422`: datos inválidos.
- `501`: `ambito=socio`.
- `503`: falta configuración.

## 10. Contrato

```bash
cd contratos
cargo test                               # ✅ ejecutado
stellar contract build                   # ⏳ requiere stellar-cli >= 25.2
sh scripts/desplegar_testnet.sh          # ⏳ despliegue + prueba de humo en Testnet
```

## 11. Riesgos y limitaciones pendientes

1. **`Base Datos/` probablemente sigue en GitHub** (historial). Para borrarla del historial hace falta `git filter-repo` y un *force push*, con tu autorización. Mientras tanto, considera que ese contenido es público.
2. **WASM y despliegue sin probar:** falta stellar-cli. `PublicadorSoroban` no se ha probado contra la red.
3. **Pérdida de `COLMENA_CLAVE_CIFRADO`** = pérdida definitiva de los registros personales. Todavía no hay script de rotación de clave (el campo `version_clave` ya existe).
4. **Llave única de servicio:** si se filtra, se mitiga con `cambiar_escritor` (llave admin). No hay pausa de emergencia en el contrato porque no se pidió en esta fase.
5. **Metadatos on-chain:** el seudónimo es constante por usuario, así que la cadena revela cuántos registros hace y cuándo, aunque no qué ni cuánto. Para ocultarlo haría falta un seudónimo por registro (decisión de diseño de la fase 2).
6. **TTL:** las pruebas se archivan si no se tocan en ~120 días. Siguen existiendo y se pueden restaurar, pero `existe()` devolverá error hasta restaurarlas. Hay que definir la política de extensión.
7. Publicación con `BackgroundTasks`: si el proceso muere, el registro queda en `enviado` o `pendiente` (visible) y hay que reintentarlo a mano o con un job (fase 2).
8. Los tokens de los testers de la web Zine siguen en claro y son adivinables (hallazgo 6).
9. `foreign_keys=ON` ahora se aplica en SQLite. Las pruebas de las rutas existentes pasan, pero conviene vigilar si aparecen datos huérfanos antiguos.

## 12. Recomendaciones para la fase 2

1. Instalar stellar-cli, compilar el WASM, desplegar en Testnet y ejecutar una prueba de integración real con `PublicadorSoroban`.
2. Un worker o cron que reintente `pendiente`, `enviado` y `error_reintentable` con *backoff*, en lugar de depender de `BackgroundTasks`.
3. Un endpoint de consulta para n8n que responda en Telegram ("¿cuánto gasté hoy?"). Debe descifrar en el servidor y devolver solo agregados al usuario dueño.
4. Un script de rotación de la clave de cifrado (re-cifrar por versión).
5. El contrato `Tesoreria` según `docs/ARQUITECTURA_SOCIOS.md`, con pruebas de cada regla de D7.
6. Purgar `Base Datos/` del historial de GitHub y rotar cualquier credencial que haya estado en el repositorio.
7. Exportar los workflows de n8n (sin credenciales) al repositorio para versionarlos.
