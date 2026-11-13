# Operación: ejecución, respaldo, recuperación y claves

## 1. Instalación local

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows (Linux/Mac: .venv/bin/python)
cp .env.example .env                                        # y completar los secretos (ver §4)
python -m scripts.preparar_bd                               # respaldo + migraciones (idempotente)
uvicorn app.main:app --port 8001
```

La API **se niega a arrancar** si la BD no está en la última migración. El error indica que hay que ejecutar `python -m scripts.preparar_bd`.

Con Docker no hace falta nada más: `docker compose up --build`. El contenedor ejecuta `preparar_bd` antes de `uvicorn`.

## 2. Migraciones (Alembic)

| Acción | Comando |
|---|---|
| Ver revisión actual | `alembic current` |
| Ver historial | `alembic history` |
| Verificar que modelos y migraciones coinciden | `alembic check` (debe decir "No new upgrade operations detected") |
| Aplicar todo (con respaldo previo) | `python -m scripts.preparar_bd` |
| Crear una migración nueva | `alembic revision --autogenerate -m "descripcion"`, y **revisar el archivo generado** |

**Bases de datos anteriores a Alembic** (las creadas con `create_all`): `preparar_bd` hace lo siguiente:
1. Las respalda.
2. Comprueba que tengan las tablas de la baseline.
3. Las marca como `0001` (`stamp`).
4. Aplica el resto de migraciones.

Si faltan tablas, se detiene sin tocar nada.

### Rollback

1. Detén la API.
2. `python -m scripts.respaldo_bd respaldar --etiqueta pre-rollback`
3. `alembic downgrade <revision>`, por ejemplo `alembic downgrade 0001`.
   - **Atención:** bajar de `0002` a `0001` **borra** las tablas del libro personal y sus datos.
4. Despliega el código que corresponde a esa revisión. Si no, la API no arrancará.

Alternativa sin pérdida: restaurar el respaldo `pre-migracion` que `preparar_bd` creó automáticamente (§3).

## 3. Respaldos de SQLite

Los respaldos se guardan en `./backups/`, que está en `.gitignore` y `.dockerignore` y es un volumen en docker-compose. Cada uno va acompañado de un manifiesto `.json` con fecha UTC, SHA-256, tamaño e `integrity_check`.

```bash
python -m scripts.respaldo_bd respaldar --etiqueta manual
python -m scripts.respaldo_bd verificar backups/colmena_<fecha>_manual.db
# Restaurar (detener antes la API):
python -m scripts.respaldo_bd restaurar backups/colmena_<fecha>_manual.db
```

- Se usa la API de backup de SQLite, que da una copia consistente aunque haya WAL y procesos activos. **No copies `colmena.db` a mano**: perderías lo que está en `colmena.db-wal`.
- `restaurar` verifica el SHA-256 y la integridad. Además guarda una copia `pre-restauracion` de la BD actual antes de reemplazarla.
- Ningún respaldo se sobrescribe ni se borra automáticamente.
- Copia periódica fuera del servidor: copia los `.db` y sus `.json` a un almacenamiento cifrado. **Nunca al repositorio.**

### Respaldo D4 de la BD del servidor

En este equipo no había ninguna BD (`data/` no existía). La BD real está en el servidor, en el volumen `./data` de Docker. Para respaldarla antes de desplegar esta versión:

```bash
docker compose exec api_colmena python -m scripts.respaldo_bd respaldar --etiqueta d4-antes-fase1
```

Al desplegar, `preparar_bd` crea además su propio respaldo `pre-migracion`. Anota la ruta y la fecha de ambos en el registro de cambios.

Los datos de prueba (`transacciones`, `testers`, etc.) **no se migran ni se borran**. El libro nuevo (`registros_personales`) arranca vacío.

## 4. Claves y secretos

| Secreto | Dónde vive | Si se pierde | Si se filtra |
|---|---|---|---|
| `COLMENA_API_TOKEN` | `.env` del servidor y credencial de n8n | Generar otro y actualizar n8n | Rotarlo de inmediato |
| `COLMENA_CLAVE_CIFRADO` | `.env` del servidor + **copia offline** (gestor de contraseñas o caja fuerte), separada de los respaldos | **Los registros personales son irrecuperables.** Los respaldos tampoco sirven sin ella | Los registros cifrados se vuelven legibles para quien tenga también la BD. Rotar la clave (re-cifrar con una versión nueva, script en Fase 2) |
| `COLMENA_SECRETO_SEUDONIMO` | `.env` + copia offline | Los usuarios existentes no se reconocen (se crearían usuarios nuevos) | Permite confirmar si un id de Telegram concreto es usuario. No revela seudónimos on-chain, que son aleatorios |
| Llave de la cuenta de servicio Soroban | Archivo en `./secrets/` (montado de solo lectura), con permisos 600 | Rotar con `cambiar_escritor` (llave admin) | El admin ejecuta `cambiar_escritor` de inmediato |
| Llave admin del contrato | Offline (hardware wallet o papel). Nunca en el servidor | No se puede rotar el escritor. Redesplegar el contrato | Riesgo de que un tercero cambie el escritor. Redesplegar |

**Respaldo + clave = datos.** Guarda los dos, pero **por separado**. Para restaurar en otra máquina:
1. Copia el respaldo.
2. Configura la **misma** `COLMENA_CLAVE_CIFRADO` y la misma `COLMENA_CLAVE_CIFRADO_VERSION`.
3. Ejecuta `restaurar`.
4. Ejecuta `preparar_bd`.

## 5. Reintentos de Soroban

Los registros con `estado_sincronizacion = error_reintentable` se pueden reenviar con `POST /api/v1/registros/{id}/reintentar`. Reintentar es seguro: el contrato rechaza hashes duplicados, y el backend interpreta ese rechazo como "ya confirmado".

Los que están en `error` (definitivo) requieren revisión manual: consulta `historial_operaciones` y `transacciones_soroban.codigo_error`.
