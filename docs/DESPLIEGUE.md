# Paso a paso: subir a GitHub y desplegar (Fases 0 y 1)

Orden recomendado: **A** subir el código → **B** respaldar el servidor → **C** preparar los secretos → **D** desplegar el backend → **E** desplegar el contrato en Testnet → **F** conectar n8n → **G** verificar.

---

## A. Subir los cambios a GitHub (sin reescribir el historial)

La carpeta local llegó como ZIP y no es un repositorio Git. Por eso se clona el repositorio real en otra carpeta y se copian los cambios encima.

```bash
# 1. Clonar en una carpeta APARTE (PowerShell o Git Bash)
cd ~/Downloads
git clone https://github.com/<usuario>/<repo>.git Blockchain-ZNE-repo
cd Blockchain-ZNE-repo

# 2. Identidad del commit (solo para este repositorio)
git config user.name  "<Tu nombre>"
git config user.email "mianmaca6@gmail.com"

# 3. Rama nueva
git checkout -b fase-0-1-libro-personal

# 4. Copiar los cambios desde la carpeta de trabajo (Git Bash), excluyendo lo privado y lo generado
rsync -a --exclude='.venv' --exclude='data' --exclude='backups' --exclude='Base Datos' \
      --exclude='contratos/target' --exclude='__pycache__' --exclude='.env' --exclude='secrets' \
      "../Blockchain-ZNE-main/Blockchain-ZNE-main/" ./
#   Si no tienes rsync, en PowerShell:
#   robocopy "..\Blockchain-ZNE-main\Blockchain-ZNE-main" . /E /XD .venv data backups "Base Datos" target __pycache__ secrets /XF .env

# 5. Los archivos renombrados como REVISAR_ quedan como "nuevos". Borrar en el clon los nombres viejos:
git rm --quiet --ignore-unmatch zne-stellar.patch templates/panel_control.html templates/reportes.html \
   app/routers/aprobaciones.py app/services/stellar_aprobaciones.py scripts/stellar_setup.py scripts/stellar_firmar.py

# 6. D1: dejar de rastrear Base Datos/ (NO borra tus archivos del disco)
git rm -r --cached --ignore-unmatch "Base Datos"

# 7. Revisar ANTES de hacer el commit: no debe aparecer nada de Base Datos, .env, .db ni llaves
git status
git add -A
git diff --cached --name-only | grep -Ei "base datos|\.env$|\.db$|secret|\.pem|\.key" && echo "¡DETENTE! Hay archivos sensibles" || echo "OK"

# 8. Commit y push
git commit -m "Fase 0 y 1: Alembic, respaldos, endurecimiento y libro personal privado con Soroban"
git push -u origin fase-0-1-libro-personal
```

Después abre un Pull Request en GitHub, revísalo y fusiónalo con `main`.

> **Importante:** `git rm --cached` evita que `Base Datos/` vuelva a subirse, pero **no la borra del historial**. Para borrarla del historial: `git filter-repo --path "Base Datos" --invert-paths` + `git push --force`. Es irreversible y obliga a los demás colaboradores a volver a clonar. Hazlo solo si lo decides expresamente.

---

## B. Respaldar la base de datos del servidor (D4), antes de desplegar

En el servidor donde corre hoy `docker compose`:

```bash
cd /ruta/del/proyecto
mkdir -p backups
docker compose exec api_colmena python -c "import sqlite3;s=sqlite3.connect('data/colmena.db');d=sqlite3.connect('backups/colmena_d4_antes_fase1.db');s.backup(d);d.close();s.close()"
cp -a backups/colmena_d4_antes_fase1.db ~/respaldo_seguro/         # copia fuera de la carpeta del proyecto
```

> La versión vieja todavía no tiene `scripts/respaldo_bd.py`; por eso se usa la API de backup de SQLite directamente. Anota la fecha y la ruta. Después del despliegue, `preparar_bd` crea además su propio respaldo `pre-migracion`.

---

## C. Preparar los secretos en el servidor (una sola vez)

```bash
cp .env.example .env
python3 -c "import secrets;print('COLMENA_API_TOKEN='+secrets.token_urlsafe(48))"
python3 -c "import secrets;print('COLMENA_SECRETO_SEUDONIMO='+secrets.token_urlsafe(48))"
python3 -c "import os,base64;print('COLMENA_CLAVE_CIFRADO='+base64.b64encode(os.urandom(32)).decode())"
```

1. Pega los tres valores en `.env`.
2. **Guarda una copia de `COLMENA_CLAVE_CIFRADO` y de `COLMENA_SECRETO_SEUDONIMO` fuera del servidor** (en un gestor de contraseñas). Si se pierden, los registros personales son irrecuperables.
3. Protege el archivo: `chmod 600 .env && mkdir -p secrets && chmod 700 secrets`.

---

## D. Desplegar el backend

```bash
git fetch && git checkout main && git pull         # después de fusionar el PR
docker compose up -d --build
docker compose logs -f api_colmena
```

En los logs deberías ver:
1. El respaldo `pre-migracion`.
2. `Running stamp_revision -> 0001` (la BD antigua se adopta sin perder datos).
3. `Running upgrade 0001 -> 0002`.
4. `Uvicorn running`.

Si algo falla, la API **no arranca** (por diseño). Revisa el mensaje y, si hace falta, restaura con `python -m scripts.respaldo_bd restaurar backups/<archivo>.db`.

Sin Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.preparar_bd
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --proxy-headers
```

---

## E. Desplegar el contrato LibroPersonal en Testnet

En una máquina con Rust (no hace falta que sea el servidor):

```bash
# 1. Herramientas
rustup target add wasm32v1-none
cargo install --locked stellar-cli         # o el instalador oficial de https://github.com/stellar/stellar-cli/releases

# 2. Identidades (las llaves quedan en el keychain de stellar-cli, nunca en el repo)
stellar keys generate colmena-admin    --network testnet --fund   # llave fría: solo rota al escritor
stellar keys generate colmena-servicio --network testnet --fund   # la usa el backend

# 3. Probar, compilar, desplegar y prueba de humo
cd contratos
sh scripts/desplegar_testnet.sh              # imprime SOROBAN_CONTRATO_LIBRO_PERSONAL=C...
```

4. Lleva la llave del servicio al servidor **como archivo**, nunca dentro de `.env`:
   ```bash
   stellar keys show colmena-servicio > soroban_servicio.txt
   scp soroban_servicio.txt servidor:/ruta/del/proyecto/secrets/ && rm soroban_servicio.txt
   # en el servidor:
   chmod 600 secrets/soroban_servicio.txt
   ```
5. En el `.env` del servidor:
   ```
   SOROBAN_CONTRATO_LIBRO_PERSONAL=C...
   SOROBAN_LLAVE_SERVICIO_ARCHIVO=./secrets/soroban_servicio.txt
   ```
   Luego ejecuta `docker compose up -d`.
6. Guarda offline la llave `colmena-admin` (`stellar keys show colmena-admin`) y bórrala de la máquina si quieres que sea realmente fría.

---

## F. Conectar n8n

En el workflow de Telegram, después de extraer los datos, agrega un nodo **HTTP Request**:

| Campo | Valor |
|---|---|
| Method / URL | `POST http://api_colmena:8001/api/v1/registros` (misma red de Docker) o la URL pública con HTTPS |
| Header `X-Api-Token` | Credencial de n8n tipo *Header Auth* con el `COLMENA_API_TOKEN`. **No lo escribas en texto dentro del nodo** |
| Header `Idempotency-Key` | `telegram-{{$json.message.chat.id}}-{{$json.message.message_id}}` |
| Body (JSON) | `{"ambito":"personal","usuario":{"canal":"telegram","id_externo":"{{chat_id}}"},"tipo":"gasto","monto":"12000","moneda":"COP","categoria":"comida","concepto":"...","fecha":"{{$now.toISO()}}"}` |

El nodo existente que llama a `/api/webhooks/ingesta-factura` también debe usar el **nuevo** token: el valor antiguo que estaba en el código ya no funciona.

---

## G. Verificación después del despliegue

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/zine                      # 200
docker compose exec api_colmena alembic current                                           # 0002 (head)
curl -s -X POST http://localhost:8001/api/v1/registros \
  -H "X-Api-Token: $COLMENA_API_TOKEN" -H "Idempotency-Key: humo-0000000001" -H "Content-Type: application/json" \
  -d '{"ambito":"personal","usuario":{"canal":"telegram","id_externo":"prueba"},"tipo":"gasto","monto":"1000","categoria":"prueba","concepto":"humo","fecha":"2026-11-12T12:00:00-05:00"}'
# A los pocos segundos:
curl -s http://localhost:8001/api/v1/registros/1 -H "X-Api-Token: $COLMENA_API_TOKEN"
#   estado_sincronizacion = "confirmado" y tx_hash ≠ null  → Soroban OK
#   "error_reintentable" → revisar SOROBAN_* y POST /api/v1/registros/1/reintentar
```

Para comprobarlo en la cadena: busca el `tx_hash` en https://stellar.expert/explorer/testnet. Solo verás el seudónimo y el hash, nunca el monto ni el concepto.

### Rollback rápido

```bash
docker compose down
git checkout <commit-anterior>
python -m scripts.respaldo_bd restaurar backups/colmena_d4_antes_fase1.db   # o el respaldo pre-migracion
docker compose up -d --build
```
