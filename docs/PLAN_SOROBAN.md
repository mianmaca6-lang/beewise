# Plan técnico: Colmena Financiera / Zine sobre Soroban

> **AVISO (2026-11-12):** el documento de fases ("PROMPT MAESTRO — Fase 0 y 1") fijó decisiones
> que **reemplazan** partes de este plan:
> - **D3:** en registros personales, Soroban recibe **solo el hash**. Nada de monto ni categoría en claro (anula la §3.1 y la §4 de este plan).
> - **D4:** los registros de prueba **no se migran** (anula la migración heurística `0004_migrar_legacy`).
> - **D7:** la votación de socios es **configurable** (Unanimidad / MayoríaSimple). Ver `docs/ARQUITECTURA_SOCIOS.md`.
> - **Arquitectura:** Telegram → n8n → FastAPI → Soroban.
>
> Lo implementado está en `docs/INFORME_FASE_0_1.md`.

> Documento para que **otro modelo o desarrollador** lo ejecute por fases.
> No contiene código final: contiene decisiones, contratos de interfaz, esquemas y criterios de verificación.
> Red objetivo: **Stellar Testnet**. Fecha: 2026-11-12.

---

## 0. Estado real del repositorio (leer antes de empezar)

El encargo describe algunas cosas que el código **todavía no tiene**. Hay que tenerlo en cuenta:

| Supuesto del encargo | Realidad en el código | Consecuencia para el plan |
|---|---|---|
| Hay Alembic | **No hay Alembic.** `app/main.py` usa `Base.metadata.create_all` | La fase 0 crea Alembic con una migración *baseline* y `alembic stamp` en las BD existentes |
| Los tokens de tester están guardados como hash SHA-256 | `Tester.token` está **en texto plano** (`crear_testers.py` crea `tester01..20`) y `/zine/login` compara en claro | Decisión D1: migrar a `token_hash` sin cambiar la experiencia de login |
| `/zine/registrar` sabe quién es el usuario | **No recibe identidad.** El token solo vive en `localStorage` y se envía únicamente a `/zine/telemetria` | El front debe enviar el token en cada request (header). El mecanismo de login no cambia |
| La colmena cuenta transacciones | Cuenta **todas** (`db.query(Transaccion).count()`) con umbrales 5/15 → niveles 1-3 | Se corrige a conteo por usuario y solo transacciones válidas |
| Botón "Evolucionar Colmena" con endpoint | No hay endpoint propio: es HTML (`colmena_juego.html` líneas 82-87) + JS (`colmena.js` líneas 50-51 y bloque de las líneas 249-263) que **rota niveles en local** | Se borra el HTML, el JS y la acción de telemetría `'Evolucionar Colmena'` |
| Montos | `Transaccion.monto` es `Float` | On-chain se usa `i128` en unidades mínimas; en SQLite, `Integer` (centavos) |
| Multifirma de socios | Hoy existe una multifirma **clásica** (manage_data 2 de 3) en `REVISAR_aprobaciones.py` | La sustituye el contrato Tesoreria (fase 4) |

### Archivos marcados con `REVISAR_` (no se necesitarán con la nueva arquitectura)

Se renombraron sin borrar nada. Los imports se ajustaron para que la app siga arrancando:

- `REVISAR_zne-stellar.patch`: el parche ya está aplicado (`git apply -R --check` solo falla por `.env.example`, que falta). Borrarlo después de recrear `.env.example`.
- `templates/REVISAR_panel_control.html` y `templates/REVISAR_reportes.html`: están vacíos (0 bytes) y nada los usa.
- `app/routers/REVISAR_aprobaciones.py`, `app/services/REVISAR_stellar_aprobaciones.py`, `scripts/REVISAR_stellar_setup.py` y `scripts/REVISAR_stellar_firmar.py`: multifirma clásica. Siguen funcionando hasta la fase 4 y luego se eliminan.
- En el código quedan comentarios `REVISAR` en `app/models.py` (`Aprobacion`, `FirmaAprobacion`) y en `app/main.py` (`/api/ejecutar-recordatorio`: llama a `Programas/recordar.py`, que **no existe**, y lanza un subproceso **sin autenticación**).

### Fuera de alcance: el usuario debe decidir si siguen

- El pipeline de facturas de clientes (`webhooks.py`, `facturas.py`, `reglas.py`, `foso_memoria.py`, `n8n_client.py` y los modelos `Cliente`, `ReglaClasificacion` y `RegistroArchivo`). No se tocan.
- **`Base Datos/` contiene audios de WhatsApp y transcripciones reales**, y `.gitignore` no lo excluye. Es un riesgo de privacidad (decisión D12).

---

## 1. Diagrama de arquitectura

```
                        ┌───────────────────────────── Navegador ─────────────────────────────┐
                        │ colmena_juego.html + colmena.js                                     │
                        │  · login tester (sin cambios)  · header X-Tester-Token en cada fetch │
                        │  · Idempotency-Key (uuid v4) por envío de formulario                 │
                        │  · tutorial overlay · HUD progreso · animación subida de nivel       │
                        └───────────────┬─────────────────────────────────────────────────────┘
                                        │ HTTPS
┌───────────────────────────────────────▼────────────────────────────────────────────────────────┐
│ FastAPI (proceso "api")                                                                          │
│  routers/zine.py ──► services/movimientos.py ──► (1) INSERT movimiento + outbox (1 tx SQLite)    │
│  routers/tesoreria.py, ahorro.py (socios: arma XDR sin firmar → socio firma en wallet → envía)  │
│  services/privacidad (HMAC seudónimo, AES-GCM descripciones)                                     │
│  services/resumenes.py, progreso.py, tutorial.py  ── leen SOLO el espejo SQLite                  │
└──────────────┬───────────────────────────────────────────────────────────────▲─────────────────┘
               │ SQLite (WAL) data/colmena.db                                    │
               │  testers · movimientos (espejo) · outbox_cadena · eventos_cadena│
               │  sync_cursor · claves_usuario · categorias · propuestas_compra  │
               │  depositos_ahorro · transacciones_legacy                        │
┌──────────────▼──────────────────┐                          ┌──────────────────┴───────────────┐
│ Worker "emisor" (proceso aparte)│                          │ Worker "sincronizador"            │
│  lee outbox pendiente           │                          │  getEvents (RPC) desde cursor     │
│  build → simulate → firma con   │                          │  guarda evento crudo (idempotente)│
│  cuenta de servicio → send →    │                          │  aplica al espejo: confirmado /   │
│  poll getTransaction            │                          │  anulado / huérfano               │
│  ÚNICO poseedor de la llave hot │                          │  avanza cursor en la misma tx     │
└──────────────┬──────────────────┘                          └──────────────────▲───────────────┘
               │ Stellar RPC (Soroban)                                           │ eventos
┌──────────────▼─────────────────────────────────────────────────────────────────┴───────────────┐
│ Testnet                                                                                         │
│  LibroPersonal  (escritor = cuenta de servicio, admin = llave fría)                             │
│  Tesoreria ──invoca──► Registro (solo acepta a Tesoreria)                                        │
│      └─ transfiere token SAC (XLM o USDC de prueba) al destinatario                             │
│  Ahorro (socios depositan/reclaman/retiran; puntos no transferibles)                             │
│  Billeteras de socios (Freighter / stellar CLI / Ledger) firman sus propias tx                   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Reglas de diseño**

1. La API web **nunca** habla con la red. Solo escribe en SQLite (movimiento + outbox) y responde `202`. La latencia del usuario no depende de Testnet.
2. El **emisor** es el único proceso que tiene la llave de servicio y el único que usa su número de secuencia, así se evitan los errores `tx_bad_seq`.
3. El **sincronizador** es el único que marca movimientos como `confirmado` o `anulado` en el espejo.
4. Los resúmenes y la colmena se calculan solo desde el espejo y nunca consultan el contrato para agregar.

---

## 2. Estructura del código

### 2.1 Workspace Rust: `contratos/` en la raíz del repo

```
contratos/
├── Cargo.toml                # [workspace] members = ["interfaces","libro_personal","registro","tesoreria","ahorro"]
│                             # [workspace.dependencies] soroban-sdk = "<última estable, fijar versión exacta>"
│                             # [profile.release] opt-level="z", overflow-checks=true, debug=0, strip="symbols",
│                             #   debug-assertions=false, panic="abort", codegen-units=1, lto=true
├── rust-toolchain.toml       # canal estable fijado + target wasm32v1-none (lo que pida `stellar contract build`)
├── interfaces/               # crate sin #[contract]: tipos compartidos y #[contractclient] trait RegistroTrait
│   └── src/lib.rs            #   así Tesoreria llama a Registro sin depender del .wasm compilado
├── libro_personal/src/{lib.rs, tipos.rs, almacenamiento.rs, errores.rs, eventos.rs, test.rs}
├── registro/src/{lib.rs, tipos.rs, almacenamiento.rs, errores.rs, eventos.rs, test.rs}
├── tesoreria/src/{lib.rs, tipos.rs, almacenamiento.rs, errores.rs, eventos.rs, test.rs}
├── ahorro/src/{lib.rs, tipos.rs, almacenamiento.rs, errores.rs, eventos.rs, test.rs}
└── scripts/
    ├── desplegar_testnet.sh  # stellar contract build/optimize/deploy + __constructor/init; guarda ids en contratos/desplegados.testnet.json
    └── costos.sh             # simula cada función y vuelca instrucciones/bytes/fee a una tabla
```

Convenciones:

- Errores con `#[contracterror] #[repr(u32)]` y códigos estables, que nunca se reutilizan.
- Eventos con la API de eventos tipados del SDK fijado (`#[contractevent]` si existe en esa versión; si no, `env.events().publish`).
- Todas las funciones de escritura extienden el TTL de la instancia.
- Cada contrato expone `version() -> u32` y `upgrade(wasm_hash)`, protegida por admin (decisión D9).

### 2.2 Módulos FastAPI nuevos o modificados

```
app/
├── config.py                       # NUEVO: pydantic-settings; carga contract ids, RPC url, rutas de secretos (no secretos en sí)
├── dependencias.py                 # NUEVO: tester_actual(X-Tester-Token) → Tester | 401 ; socio_actual(...)
├── logging_seguro.py               # NUEVO: filtro que redacta S[A-Z2-7]{55}, tokens, cabeceras X-*-Token
├── services/
│   ├── cadena/
│   │   ├── cliente_rpc.py          # NUEVO: wrapper SorobanServer: simulate/prepare, send, poll, get_events, get_ledger_entries
│   │   ├── firmante.py             # NUEVO: carga la llave de servicio desde archivo-secreto; expone firmar(tx) — nunca la llave
│   │   ├── libro_personal.py       # NUEVO: construir_registrar(), construir_anular(), decodificar_evento()
│   │   ├── tesoreria.py / ahorro.py / registro.py   # NUEVO (fases 4-5)
│   │   └── errores.py              # NUEVO: mapea códigos #[contracterror] → excepciones Python (definitivo / reintentable)
│   ├── privacidad.py               # NUEVO: seudonimo(tester_id), cifrar/descifrar descripción, commit de descripción
│   ├── movimientos.py              # NUEVO: crear_movimiento(), solicitar_anulacion(), listar()
│   ├── clasificador.py             # MOVIDO desde dashboard.py: clasificar_transaccion_nlp → devuelve tipo + código categoría (añade "ahorro")
│   ├── resumenes.py                # NUEVO
│   ├── progreso.py                 # NUEVO: nivel por usuario
│   └── tutorial.py                 # NUEVO
├── workers/
│   ├── emisor.py                   # NUEVO: python -m app.workers.emisor
│   └── sincronizador.py            # NUEVO: python -m app.workers.sincronizador
├── routers/
│   ├── dashboard.py                # MODIFICADO: queda solo "/" y admin; todo /zine/* se mueve a zine.py
│   ├── zine.py                     # NUEVO
│   ├── tesoreria.py, ahorro.py     # NUEVO (fases 4-5)
│   ├── ops.py                      # NUEVO: estado de sincronización (protegido con X-Api-Token)
│   └── REVISAR_aprobaciones.py     # ELIMINAR en fase 4
alembic/ + alembic.ini              # NUEVO (fase 0)
tests/                              # NUEVO: pytest
docker-compose.yml                  # MODIFICADO: servicios api, emisor, sincronizador (misma imagen, distinto CMD); secrets
```

Dependencias Python nuevas: `alembic`, `pydantic-settings`, `cryptography` (AES-GCM), `pytest`, `freezegun` o `time-machine`, y `httpx` (ya está). Para Soroban se usa `stellar-sdk>=11` (ya incluye `SorobanServer`); hay que fijar una versión compatible con el protocolo vigente en Testnet.

---

## 3. Contratos: storage, funciones, errores y eventos

### Convenciones comunes

- **Montos:** `i128` en unidades mínimas. Para COP en LibroPersonal se usa `escala = 100`, configurable en la instancia. Rechazar `monto <= 0` y `monto > MONTO_MAX` (p. ej. 10^15).
- **Tiempo:** `env.ledger().timestamp()`, en segundos UNIX. Un día equivale aproximadamente a 17.280 ledgers (~5 s por ledger).
- **TTL:**
  - Instancia: `extend_ttl(UMBRAL=~15 días, HASTA=~30 días)` en cada llamada de escritura.
  - Persistent: lo define cada contrato (ver abajo).
  - Las constantes se expresan en ledgers dentro de `almacenamiento.rs`.
- **Inicialización:** con `__constructor` si la versión del SDK lo soporta, para que sea atómica con el deploy. Si no, `init()` con guardia `YaInicializado`.

### 3.1 LibroPersonal (fase 1, la que desbloquea todo)

**Tipos**

- `Tipo: u32 enum { Gasto=0, Ingreso=1, Ahorro=2 }`.
- `categoria: u32`: código del catálogo `categorias`. Nunca texto libre on-chain.
- `Registro { tipo: Tipo, categoria: u32, monto: i128, desc_commit: BytesN<32>, fecha_usuario: u64, ledger_ts: u64, anulado_ts: u64 (0 = vigente) }`.
- `EstadoUsuario { total: u32, validas: u32 }`.

**Storage**

| Clave (`enum DataKey`) | Tipo storage | Contenido | TTL |
|---|---|---|---|
| `Admin` | instance | `Address` (llave fría) | instancia |
| `Escritor` | instance | `Address` (cuenta de servicio) | instancia |
| `Pausado` | instance | `bool` | instancia |
| `Usuario(BytesN<32>)` | persistent | `EstadoUsuario` | se extiende en cada escritura del usuario, hasta ~180 días |
| `Registro(BytesN<32>, u64)` | persistent | `Registro` | se escribe con ~45 días; ver nota |

> Nota de TTL: la anulación solo se permite durante **30 días** desde `ledger_ts` (decisión D5). Por eso un registro no necesita vivir mucho más. Si un registro se archiva, el dato sigue existiendo en el historial de eventos y en el espejo. Restaurarlo solo hace falta para anular algo viejo, y eso queda prohibido por la regla de los 30 días.

**Funciones**

| Función | Auth | Reglas | Retorno |
|---|---|---|---|
| `__constructor(admin, escritor)` | — | una sola vez | — |
| `registrar(usuario: BytesN<32>, id_registro: u64, tipo, categoria, monto, desc_commit: BytesN<32>, fecha_usuario: u64)` | `escritor.require_auth()` | no pausado; `id_registro` no existe para ese usuario (**idempotencia**); monto en rango; `fecha_usuario <= ledger_ts + 300` y `>= ledger_ts - 366 días`; `total += 1`, `validas += 1` | `EstadoUsuario` |
| `registrar_lote(entradas: Vec<Entrada>)` *(opcional, se activa según D6)* | escritor | máx. N=20 (ajustar por límites de footprint medidos); **las entradas duplicadas se omiten** sin abortar | `Vec<u32>` con el código de resultado por entrada |
| `anular(usuario, id_registro)` | escritor | el registro existe; `anulado_ts == 0`; `ledger_ts - registro.ledger_ts <= 30 días`; marca `anulado_ts`; `validas -= 1`. **No borra** | `EstadoUsuario` |
| `obtener(usuario, id_registro)` | — | lectura | `Option<Registro>` |
| `estado(usuario)` | — | lectura | `EstadoUsuario` |
| `set_escritor(nuevo)` | admin | rota la llave de servicio | — |
| `pausar(bool)` | admin | interruptor de emergencia | — |
| `upgrade(hash)` / `version()` | admin / — | | |

Sobre la **"anulación que compensa"**: el registro original queda intacto, se le añade `anulado_ts` y se emite un evento `anulacion`. El espejo crea la fila compensatoria. Con eso el historial on-chain conserva el registro y su anulación, sin borrar nada. No se crea un segundo registro con monto negativo porque falsearía `total`, complicaría las sumas y permitiría "anular la anulación".

**Errores (`u32`)**

| Código | Error | Código | Error |
|---|---|---|---|
| 1 | YaInicializado | 7 | YaAnulado |
| 2 | NoAutorizado | 8 | VentanaAnulacionVencida |
| 3 | Pausado | 9 | FechaInvalida |
| 4 | RegistroYaExiste | 10 | LoteDemasiadoGrande |
| 5 | RegistroNoExiste | 11 | Desbordamiento |
| 6 | MontoInvalido | | |

`RegistroYaExiste` es la señal de idempotencia: el emisor la trata como **éxito** si el contenido coincide.

**Eventos** (temas: símbolo corto y usuario; datos: estructura)

- `("registro", usuario)` → `{ id_registro, tipo, categoria, monto, desc_commit, fecha_usuario, ledger_ts, validas }`
- `("anulacion", usuario)` → `{ id_registro, anulado_ts, validas }`
- `("escritor", )` → `{ anterior, nuevo }` · `("pausa", )` → `{ pausado }`

### 3.2 Registro (compras del negocio)

- **Storage:**
  - Instancia: `Admin`, `Tesoreria: Address`, `Total: u64`.
  - Persistent: `Compra(u64)` → `{ id, descripcion: String(≤128), tipo: Symbol, monto: i128, token: Address, destinatario: Address, proponente, aprobador, timestamp }`, con TTL largo (~180 días) que se extiende al leer desde Tesoreria.
  - Es contabilidad del negocio: decisión D10 sobre archivado.
- **Funciones:**
  - `__constructor(admin, tesoreria)`.
  - `registrar(compra)`: llama a `tesoreria.require_auth()`. Esto solo se cumple cuando **Tesoreria es el invocador directo**. Rechaza ids duplicados.
  - `obtener(id)`, `total()`, `upgrade`.
- **Errores:** YaInicializado, NoAutorizado, CompraYaExiste, DescripcionInvalida.
- **Eventos:** `("compra", id)` con todos los campos.

### 3.3 Tesoreria (socios, 2 votos)

**Interpretación de "2 votos" (decisión D7).** Por defecto, proponer cuenta como voto 1 y la aprobación de **otro** socio es el voto 2. Esto encaja con que Registro guarde un único `aprobador`.

**Storage**

- Instancia: `Admin`, `Socios: Vec<Address>` (exactamente 3, sin repetidos), `Token: Address` (SAC), `Registro: Address`, `Umbral: u32 = 2`, `TtlPropuesta: u64` (p. ej. 7 días), `SiguienteId: u64`.
- Persistent: `Propuesta(u64)` → `{ id, proponente, descripcion, tipo, monto, destinatario, creada_ts, expira_ts, votos: Vec<Address>, estado: Abierta|Ejecutada|Cancelada }`.

**Funciones**

| Función | Auth | Reglas |
|---|---|---|
| `proponer_compra(proponente, descripcion, tipo, monto, destinatario)` | `proponente.require_auth()` | proponente ∈ socios; monto > 0; descripción de 1 a 128 bytes; destinatario ≠ dirección del contrato; `votos = [proponente]`; devuelve `id` |
| `aprobar(socio, id)` | `socio.require_auth()` | socio ∈ socios; `socio ≠ proponente` (**AutoAprobacion**); `socio ∉ votos` (**YaVoto**); estado Abierta; no expirada |
| `ejecutar(socio, id)` | `socio.require_auth()`, socio ∈ socios | `votos.len() >= umbral`; estado Abierta; no expirada. **Primero** se pone `estado = Ejecutada` y se guarda (checks-effects-interactions); **después** `token.transfer(contrato → destinatario, monto)` y `registro.registrar(...)` con `aprobador = votos[1]` |
| `cancelar(proponente, id)` | proponente | solo si está Abierta |
| `obtener(id)`, `socios()`, `saldo()` | — | |
| `upgrade` / `set_socios` | admin (o 2 de 3 socios, decisión D8) | |

**Riesgos que se cubren**

- **Doble voto:** lo bloquea `YaVoto`.
- **Auto-aprobación:** la bloquea `AutoAprobacion`.
- **Doble ejecución:** el estado deja de ser `Abierta` antes de la transferencia. Si la transferencia falla, toda la transacción revierte de forma atómica y el estado vuelve a `Abierta`.
- **Reentrada:** Soroban prohíbe la reentrada al mismo contrato durante su ejecución, y además se aplica CEI. Hay que probar con un token malicioso que intente llamar `ejecutar` desde `transfer`.

**Errores:** YaInicializado, NoEsSocio, AutoAprobacion, YaVoto, PropuestaNoExiste, NoAbierta, Expirada, VotosInsuficientes, MontoInvalido, DescripcionInvalida, DestinatarioInvalido, SociosInvalidos.

**Eventos:** `propuesta(id)`, `voto(id, socio)`, `ejecutada(id)`, `cancelada(id)`.

### 3.4 Ahorro (socios)

**Definición propuesta** (decisión D11):

- **Lotes independientes.** Cada `depositar` crea un lote `Lote { id, socio, monto, inicio_ts, ultimo_reclamo_ts, activo }`. Así se resuelve el caso de varios depósitos: cada uno madura por su cuenta y no se promedia nada.
- **Puntos.** Por cada período completo de 90 días (7.776.000 s) desde `ultimo_reclamo_ts` (al principio igual a `inicio_ts`), el lote genera `puntos = monto_en_unidades_enteras × periodos_completos`.
  - Al reclamar se avanza `ultimo_reclamo_ts += periodos × 90 días`. Nunca se usa `now`, para no perder la fracción.
  - Los puntos son un saldo **no transferible** guardado en el contrato (`Puntos(socio): u128`). No son un token.
- **Retirar.** `retirar(socio, id_lote)` devuelve el lote completo.
  - Si quedan períodos completos sin reclamar, **se reclaman automáticamente** antes del retiro.
  - Un período incompleto **no genera puntos**.
  - No hay retiro parcial: se simplifica a cambio de exigir depósitos por lote.

**Storage**

- Instancia: `Admin`, `Token`, `Socios`, `SiguienteLote`.
- Persistent: `Lote(u64)`, `LotesDe(Address) → Vec<u64>` (con tope, p. ej. 50 lotes activos) y `Puntos(Address)`.

**Funciones**

| Función | Auth | Reglas |
|---|---|---|
| `depositar(socio, monto)` | socio | ∈ socios; `transfer(socio → contrato)`; devuelve `id_lote` |
| `reclamar_puntos(socio, id_lote)` | socio | dueño del lote; ≥ 1 período completo; si no, `SinPeriodoCompleto` |
| `retirar(socio, id_lote)` | socio | dueño y activo. Orden: marcar inactivo → sumar puntos → transferir |
| `puntos(socio)`, `lotes(socio)`, `lote(id)` | — | |

**Errores:** NoEsSocio, MontoInvalido, LoteNoExiste, NoEsDueno, LoteInactivo, SinPeriodoCompleto, DemasiadosLotes, Desbordamiento.

**Eventos:** `deposito`, `reclamo{periodos, puntos}`, `retiro`.

---

## 4. Privacidad

| Dato | Dónde | Forma |
|---|---|---|
| Identidad del tester | Solo SQLite | `tester_id`. On-chain **nunca** |
| `id_seudonimo` | On-chain | `HMAC-SHA256(PEPPER, "zine:v1:" ‖ tester_id)` → `BytesN<32>`. El pepper es un secreto fuera de la BD. Al ser HMAC con clave, no se puede revertir por fuerza bruta sobre ids pequeños (1..20), como sí pasaría con un SHA-256 simple |
| Tipo, categoría (código), monto, fechas | On-chain **en claro** | Hacen falta para el contador, la auditoría y la verificación. **Riesgo:** con montos y horarios de un mismo seudónimo se puede perfilar a la persona (decisión D3) |
| Descripción (`concepto`) | SQLite **cifrada** | AES-256-GCM con una **clave por usuario (DEK)**, guardada envuelta por una KEK que vive fuera de la BD (tabla `claves_usuario`) |
| `desc_commit` | On-chain | `SHA-256(nonce ‖ ciphertext)`. No se usa el hash del texto plano: "chocorramo" se adivinaría por diccionario. Sirve para demostrar que la descripción del espejo no fue alterada |

- **Cómo ve el usuario sus descripciones:** `GET /zine/movimientos` autentica al tester, descifra en el servidor con su DEK y devuelve texto plano solo a ese tester.
- **Derecho al olvido:** borrar la DEK del usuario (*crypto-shredding*) deja ilegibles sus descripciones. Lo que está on-chain es seudónimo y no se puede borrar; esto se debe decir en el consentimiento.
- **Rotación:** el pepper lleva versión (`v1`). Cambiarlo equivale a crear seudónimos nuevos. No se rota salvo que haya un compromiso, y en ese caso se usa un evento de migración. La KEK se rota re-envolviendo las DEK, sin tocar la cadena.

## 5. Costos, límites y escala

- **Fees.** Cada invocación paga *inclusion fee* (mínimo 100 stroops) más *resource fee* (CPU, lectura/escritura de entradas y bytes, tamaño de eventos y renta).
  - **No hay que fiarse de cifras de memoria.** La fase 1 entrega una tabla medida con `simulateTransaction` (`contratos/scripts/costos.sh`) para `registrar`, `anular` y `registrar_lote(N)`.
  - Orden de magnitud esperado: milésimas a centésimas de XLM por invocación simple.
- **Renta.** Cada `Registro` es una entrada persistent pequeña (~100-150 bytes) y paga renta proporcional a bytes × ledgers de TTL.
  - Por eso se usa un TTL acotado (~45 días), alineado con la ventana de anulación.
  - El historial largo vive en los eventos y en el espejo, no en el storage.
- **Escala.** Ejemplo: 5.000 usuarios × 5 tx/día ≈ 25.000 tx/día.
  - Una sola cuenta de servicio emite en serie, a lo sumo **una tx por ledger** sin canales: ~17.000 tx/día. "Cientos al día" cabe sin problema; decenas de miles no.
  - Hay dos palancas:
    - **(a) Lotes.** `registrar_lote` de 10-20 entradas cada ~10 s: amortiza el inclusion fee y multiplica el throughput. A cambio, la confirmación tarda unos segundos más y hay un límite de entradas en el footprint por tx.
    - **(b) Cuentas canal.** Varias cuentas fuente que solo pagan fee, mientras el escritor autoriza vía auth entry; así hay secuencias en paralelo.
  - **Recomendación:** empezar sin lotes (simple y verificable) y medir. Activar lotes en el emisor (agrupar outbox pendientes) cuando haya > 1 tx por ledger sostenida.
- **Eventos y retención del RPC.** Los RPC públicos guardan eventos solo durante una ventana corta (días; hay que verificar la del proveedor).
  - El sincronizador **debe** correr de forma continua y alertar si su atraso supera 24 h.
  - Además se guarda cada evento crudo en `eventos_cadena`, que es nuestro archivo.
  - Para reconstruir más allá de la ventana: leer `obtener()` por cada `id_registro` conocido con `getLedgerEntries`, o usar un indexador (Galexie/Hubble o un servicio externo).
- **Saldo de la cuenta de servicio.** Alerta cuando baje de X XLM. En Testnet se recarga con Friendbot.

## 6. Consistencia, idempotencia y fuente de verdad

**Fuente de verdad**

- **Contrato:** manda sobre la existencia de cada registro, su anulación y el contador `validas`.
- **SQLite:** manda sobre la identidad (tester ↔ seudónimo) y el texto de las descripciones.
- **Espejo:** es una vista derivada que se puede reconstruir.

**Flujo de registro (patrón outbox)**

1. `POST /zine/registrar` con `Idempotency-Key`. En **una transacción SQLite**:
   - Si ya existe un `movimiento` con esa key para el tester, devuelve el mismo resultado.
   - Si no, inserta `movimiento(estado='pendiente', id_registro = movimiento.id)` y `outbox_cadena(op='registrar')`.
   - Responde `202 { id, estado:'pendiente' }`.
2. El emisor toma el outbox pendiente: simula, firma, envía y guarda `tx_hash` **antes** de hacer polling (`estado='enviada'`).
3. Resultados posibles:
   - **SUCCESS:** outbox `hecho`. El movimiento sigue `pendiente` hasta que llegue el evento.
   - **Error definitivo del contrato** (`MontoInvalido`, `FechaInvalida`, `Pausado`, etc.): movimiento `fallido` con mensaje. La UI ofrece reintentar o descartar. No cuenta para nada.
   - **`RegistroYaExiste`:** se llama `obtener()` y, si coincide, se trata como éxito. Esto cubre el caso de una tx que entró pero cuya respuesta se perdió.
   - **Ambiguo** (timeout, 5xx o `NOT_FOUND` después de enviar): se reintenta con *backoff* exponencial (1s, 2s, 4s… máx. 5 min), con el **mismo `id_registro`**. La idempotencia la garantiza el contrato. Se usa `timebounds` corto (60 s) para saber cuándo una tx no incluida ya no puede entrar.
4. El sincronizador recibe el evento `registro`, hace upsert por `(usuario, id_registro)`, pasa el movimiento a `confirmado` y guarda `ledger` y `tx_hash`.

**Casos de borde**

| Situación | Resultado |
|---|---|
| Guardado en SQLite, falla on-chain de forma definitiva | `fallido`, visible para el usuario, excluido de resúmenes y conteos |
| Guardado en SQLite, el emisor está caído | Queda `pendiente`; la UI muestra "sincronizando 🐝". Alerta si una fila lleva > 10 min pendiente |
| Está on-chain pero no en SQLite (BD restaurada de un backup, otro entorno) | El sincronizador crea un movimiento `origen='cadena_huerfana'` con descripción "no disponible", resuelve el tester por seudónimo (tabla de seudónimos) y emite una alerta |
| Evento duplicado o reprocesado | Upsert idempotente con `UNIQUE(contract_id, tx_hash, evento_idx)` en `eventos_cadena` |
| Anulación | Igual que el registro: movimiento `anulacion_pendiente` → outbox `anular` → evento → `anulado`. Si el registro original aún está `pendiente`, la anulación espera en el outbox (dependencia ordenada por `movimiento_id`) |

**Conteo para la colmena:** solo se cuentan los movimientos `confirmado`. El HUD muestra los pendientes aparte ("+3 en camino").

**Reconciliación nocturna:** se compara `estado(usuario).validas` del contrato con el conteo del espejo para los usuarios activos del día. Si hay diferencia, se emite una alerta y se re-sincroniza.

## 7. Custodia de llaves

- **Cuenta de servicio (llave "hot"):**
  - En Docker se monta como *secret* (`/run/secrets/stellar_servicio`). En producción va en un gestor de secretos (Vault o el KMS de la nube).
  - **Solo el contenedor `emisor` la monta.** La API y el sincronizador no la tienen.
  - Nunca va en `.env` versionado, en argumentos de línea de comandos (se ven con `ps`) ni en variables de entorno heredadas por subprocesos.
  - `firmante.py` la carga una vez, expone solo `firmar()` y `public_key` y **sin `__repr__`** que la muestre.
  - En el contrato solo es `Escritor`. **No es admin**, así que si se compromete, la llave fría del admin la rota con `set_escritor` y puede `pausar`.
  - Saldo mínimo: solo lo que haga falta para fees.
- **Admin (llave fría):** se genera offline y se guarda en Ledger o papel en caja fuerte. Solo se usa para `set_escritor`, `pausar` y `upgrade`. Decisión D8: un multisig de los 3 socios puede ser el admin.
- **Socios:** cada uno firma en su billetera (Freighter en el navegador, `stellar` CLI con llave en el keychain del SO, o Ledger). El backend solo construye XDR sin firmar, **simulado y preparado**, y luego recibe el XDR firmado. El flujo es el mismo que hoy en `REVISAR_stellar_firmar.py`, pero sin pasar `SOCIO_SECRET` por variable de entorno.
- **Pepper y KEK:** son secretos aparte del de Stellar y los montan **la API y el sincronizador** (no el emisor).
- **Controles:**
  - `logging_seguro.py`: filtro global que redacta `S[A-Z2-7]{55}`, `X-Tester-Token`, `X-Api-Token` y el cuerpo de `/zine/login`.
  - `gitleaks` como *pre-commit* y en CI.
  - Un test que falla si `repr()` o un log de excepción contiene una llave secreta.
  - Quitar el valor por defecto `TU_TOKEN_SECRETO_COLMENA_2026` de `seguridad.py` y `webhooks.py`: sin variable de entorno, la app no arranca.

## 8. Cambios de modelos y migraciones Alembic

**Modelos (SQLAlchemy)**

- **`Tester`:**
  - Nuevas columnas: `token_hash` (String(64), único; según D1), `tutorial_completado_en` (nullable), `tutorial_version` (int, default 1), `nivel_animado` (int, default 1) y `creado_en`.
- **`Movimiento`** (tabla `movimientos`, el espejo):
  - Identidad: `id` PK, que también es el `id_registro` on-chain; `tester_id` FK con índice; `idempotency_key`.
  - Datos: `tipo`, `categoria_codigo` FK→`categorias`, `monto_minor` Integer, `fecha_usuario`, `desc_cifrada` BLOB, `desc_nonce`, `desc_commit` (hex).
  - Estado: `estado` ∈ {pendiente, confirmado, fallido, anulacion_pendiente, anulado} y `origen` ∈ {app, legacy, cadena_huerfana}.
  - Trazabilidad on-chain: `tx_hash_registro`, `ledger_registro`, `tx_hash_anulacion`, `ledger_anulacion`, `ultimo_error`, `creado_en`, `actualizado_en`.
  - Restricción `UNIQUE(tester_id, idempotency_key)`. Índices `(tester_id, estado, fecha_usuario)` y `(tester_id, categoria_codigo)`.
- **`OutboxCadena`:** `id`, `movimiento_id`, `operacion` (registrar|anular), `contrato`, `payload_json`, `estado` (pendiente|enviada|hecho|fallido), `intentos`, `proximo_intento`, `tx_hash`, `error`.
- **`EventoCadena`:** `contract_id`, `ledger`, `tx_hash`, `evento_idx`, `tema`, `payload_json`, `procesado_en`. Restricción `UNIQUE(contract_id, tx_hash, evento_idx)`.
- **`SyncCursor`:** `contract_id` PK, `cursor`, `ultimo_ledger`, `actualizado_en`.
- **`ClaveUsuario`:** `tester_id` PK, `dek_envuelta`, `kek_version`.
- **`Seudonimo`:** `tester_id`, `id_seudonimo` (hex, único), `version`. Sirve para resolver eventos huérfanos.
- **`Categoria`:** `codigo` int PK, `nombre`, `tipo`. Semilla: 1 Comida, 2 Transporte, 3 Servicios fijos, 9 Varios, 100 Ingreso general, 200 Ahorro general.
- **Fase 4-5:** `Socio` (public_key, nombre), `PropuestaCompra` (espejo), `DepositoAhorro` (espejo).
- **`Transaccion`:** se renombra a `transacciones_legacy` (solo lectura). Al final se elimina (decisión D4).
- **`LogTelemetria`:** sin cambios. Se añade una lista blanca de acciones en el endpoint.

**Migraciones** (con `render_as_batch=True` en `env.py`, obligatorio en SQLite para ALTER)

1. `0001_baseline`: refleja exactamente el esquema actual. En BD existentes se ejecuta `alembic stamp 0001` y **no** `upgrade`. Se quita `create_all` de `main.py` y `crear_testers.py` pasa a usar la sesión normal.
2. `0002_tester_campos`: añade las columnas de `Tester`. Migración de datos: `token_hash = sha256(token)`. `token` queda nullable y se borra en `0002b`, una vez verificado (D1).
3. `0003_espejo_y_outbox`: crea `movimientos`, `outbox_cadena`, `eventos_cadena`, `sync_cursor`, `claves_usuario`, `seudonimos` y `categorias` (con semilla).
4. `0004_migrar_legacy`: migración de **datos**. Las filas de `transacciones` no tienen dueño:
   - **Atribución heurística:** para cada transacción se busca en `logs_telemetria` el evento `clic_boton / 'Abrir registro de gasto'` más cercano **antes** de `fecha_hora`, dentro de 15 minutos. Si hay **un único** tester candidato, se le asigna. Si no hay ninguno o hay varios, queda sin asignar.
   - Las asignadas se copian a `movimientos` con `origen='legacy'`, `monto_minor = round(monto*100)`, la descripción cifrada con la DEK del tester y `estado='pendiente'`, además de un **outbox `registrar`**. Así entran on-chain con su `fecha_usuario` original y el contador de la cadena coincide (decisión D4).
   - Las no asignadas se quedan en `transacciones_legacy` y no cuentan para nadie.
   - La migración escribe un informe (asignadas, ambiguas y huérfanas). **Antes** se hace backup de `data/colmena.db`.
5. `0005_renombrar_legacy`: `transacciones` → `transacciones_legacy`.
6. `0006_tesoreria_ahorro` (fase 4-5) y `0007_eliminar_aprobaciones` (cuando se confirme D2).

## 9. Endpoints

Todos los `/zine/*` de datos usan la dependencia `tester_actual` (header `X-Tester-Token`, el mismo token del login). Si falta o no es válido, responden 401. **Nunca se acepta `tester_id` desde el cliente.**

| Método | Ruta | Estado | Descripción |
|---|---|---|---|
| POST | `/zine/login` | modificado | Igual para el usuario. Compara contra `token_hash` y responde además `{ mostrar_tutorial: bool }` |
| GET | `/zine` | modificado | Ya no calcula el nivel de forma global; el front pide `/zine/progreso` |
| POST | `/zine/registrar` | modificado | Form `concepto`, `monto`, `tipo?` (gasto/ingreso/ahorro; si falta, decide el NLP), `categoria?`, `fecha?`, más el header `Idempotency-Key`. Responde `202 {id, estado, tipo, categoria}`. Valida `monto > 0`, límite diario por tester (anti-farming, p. ej. 60/día) y ignora `archivo` (decisión D13) |
| GET | `/zine/movimientos?limite=&antes_de=` | nuevo | Lista propia con descripción descifrada, `estado` y `eliminado: estado in (anulado, anulacion_pendiente)` |
| DELETE | `/zine/movimientos/{id}` | nuevo | Anulación: `202 {estado:'anulacion_pendiente'}`. Responde 404 si no es del tester (no 403, para no revelar que existe), 409 si ya está anulado o fuera de la ventana de 30 días, y 409 si está `fallido` (en ese caso se descarta localmente con `/descartar`) |
| POST | `/zine/movimientos/{id}/reintentar` y `/descartar` | nuevo | Para movimientos `fallido` |
| GET | `/zine/estadisticas` | modificado | Igual que hoy (formato Chart.js), pero filtrado por tester, `estado='confirmado'` y agrupado por categoría. Incluye ahorro |
| GET | `/zine/resumen` | nuevo | `{ hoy_gasto, semana_pasada_ahorro, mes_actual_vs_anterior: {gasto, variacion_pct}, categoria_principal_mes, frases: [..] }` |
| GET | `/zine/progreso` | nuevo | `{ validas, pendientes, nivel, nivel_max, umbral_siguiente, faltan, subio_de_nivel: nivel > nivel_animado }` |
| POST | `/zine/progreso/animacion-vista` | nuevo | `{nivel}` → `nivel_animado = max(nivel_animado, nivel)` |
| POST | `/zine/tutorial/completado` | nuevo | Guarda `tutorial_completado_en` (también si se salta) |
| POST | `/zine/telemetria` | modificado | Lista blanca de acciones. `'Evolucionar Colmena'` desaparece del front y deja de aceptarse |
| GET | `/api/ops/sync` | nuevo | `X-Api-Token`: atraso del cursor, outbox pendientes o fallidos, saldo de la cuenta de servicio |
| POST | `/api/tesoreria/propuestas/preparar` | nuevo (F4) | `{socio_pk, descripcion, tipo, monto, destinatario}` → XDR simulado sin firmar |
| POST | `/api/tesoreria/{id}/aprobar/preparar`, `/ejecutar/preparar` | nuevo (F4) | Ídem |
| POST | `/api/tesoreria/enviar` | nuevo (F4) | `{xdr_firmado}`: verifica que la fuente sea un socio y que invoque **nuestro** contrato y función; envía y hace polling |
| GET | `/api/tesoreria/propuestas` | nuevo (F4) | Desde el espejo |
| POST/GET | `/api/ahorro/...` | nuevo (F5) | `depositar`, `reclamar` y `retirar` con el mismo patrón preparar → firmar → enviar |
| — | `/api/aprobaciones/*` | eliminar (F4) | Junto con los archivos `REVISAR_` |

## 10. Requisitos de producto: cómo se implementa cada uno

**Borrar por error = anulación**

- Cada fila de la lista "Mis movimientos" (nuevo modal) tiene un botón 🗑 → confirmación → `DELETE`.
- La UI muestra la fila tachada con la etiqueta "Eliminado" mientras está `anulacion_pendiente` y la oculta por defecto cuando está `anulado`, con un filtro "ver eliminados".
- Los resúmenes y el conteo la excluyen desde que pasa a `anulacion_pendiente`. Si la anulación falla, se revierte.

**Tutorial de primer inicio**

- `login` devuelve `mostrar_tutorial = tutorial_completado_en IS NULL`. Lo decide el servidor, así que se muestra una sola vez aunque cambie de navegador.
- Overlay de 4-5 pasos que resalta botones reales:
  1. Registrar.
  2. Tipo (gasto/ingreso/ahorro).
  3. Balance.
  4. "Tu colmena crece cada 100 registros".
  5. "¿Te equivocaste? Elimínalo".
- Botones *Siguiente / Saltar*. Al terminar o saltar → `POST /zine/tutorial/completado`.
- Un botón "?" en el HUD lo **repite** del lado del cliente, sin tocar el flag.
- Telemetría: `tutorial_inicio`, `tutorial_fin` y `tutorial_saltado`.

**Resúmenes**

- Se calculan en `services/resumenes.py` sobre `movimientos` con `estado='confirmado'`, siempre en zona `America/Bogota` (configurable).
- Definiciones:
  - "hoy" = día calendario local.
  - "semana pasada" = lunes a domingo ISO anterior.
  - "mes vs mes anterior" = del día 1 a hoy contra **el mismo número de días** del mes anterior, para comparar de forma justa.
  - "categoría principal" = la categoría de gasto con mayor monto del mes en curso.
- Frases cortas en plantilla según el caso: sin datos, primera semana, variación de ±0 %.
- Se muestran como *toasts* rotativos y en un panel arriba del gráfico.

**Eliminar "Evolucionar Colmena"**

- HTML: el bloque `<!-- Botón Inferior de Evolución -->`. En su lugar va una barra de progreso.
- JS: `getElementById('btn-evolucionar')` de telemetría, `const btnEvolucionar` y su *listener*.
- Backend: la lista blanca de telemetría.
- La animación de ese *listener* se reutiliza en `animarSubidaNivel()`.

**Evolución automática por usuario**

- Fórmula: `nivel = min(1 + floor(validas / 100), NIVEL_MAX)` con `NIVEL_MAX = 3` hoy, porque solo hay 3 ilustraciones. Umbrales: N1 = 0-99, N2 = 100-199, N3 = 200+. Añadir arte permite subir `NIVEL_MAX` sin migrar.
- `validas` = movimientos del tester con `estado='confirmado'` en el espejo. Coincide con `EstadoUsuario.validas` on-chain, que se verifica en la reconciliación.
- **Anulaciones (recomendación, D5):** el nivel **sí puede bajar**, pero con **histéresis de 5**. Solo baja si `validas < umbral_del_nivel - 5`.
  - Corregir 1-2 errores no castiga.
  - Registrar 100 basura y anularlos no "farmea" niveles, que sí se podría si el nivel nunca bajara.
  - La bajada no tiene animación: solo cambia la imagen y la barra.
  - Para la histéresis se guarda `nivel_actual` en `Tester`. Se recalcula en `progreso.py`.
- **Animación de subida** (`colmena.js`, `colmena.css`). Se dispara cuando `subio_de_nivel = true`, al cargar o tras confirmar un registro (el front hace polling de `/zine/progreso` cada 5 s mientras haya pendientes):
  1. La colmena tiembla (`@keyframes` de 600 ms) con brillo dorado (`drop-shadow` pulsante).
  2. Ráfaga de 15 abejas que ignora `MAX_ABEJAS`.
  3. Fundido con *scale* hacia la nueva imagen (la transición actual de opacity/translate).
  4. Lluvia de hexágonos de miel en CSS.
  5. *Toast*: "¡Tu colmena creció a Nivel N: {desc}!".
  6. `POST /animacion-vista`.
  - Con `prefers-reduced-motion` solo se cambia la imagen y se muestra el *toast*.
  - Un HUD fijo muestra la barra "137 / 200 · faltan 63".

---

## 11. Seguridad: ataques y casos de prueba por contrato

### LibroPersonal

| # | Ataque o riesgo | Prueba |
|---|---|---|
| L1 | Alguien distinto del escritor registra | `registrar` sin auth del escritor o con otra cuenta → falla la auth |
| L2 | Repetir la tx (*replay*) o reintento duplicado | Mismo `(usuario, id_registro)` dos veces → `RegistroYaExiste`; el contador no cambia |
| L3 | Anular dos veces | → `YaAnulado`; `validas` no baja dos veces |
| L4 | Anular un registro de otro usuario | `anular(usuarioB, id_de_A)` → `RegistroNoExiste` (la clave incluye al usuario) |
| L5 | Desbordamiento del contador o monto extremo | Monto ≤ 0 o > MAX → `MontoInvalido`; `validas` en 0 nunca queda en negativo (`checked_sub` → `Desbordamiento`) |
| L6 | Fecha futura o muy antigua | → `FechaInvalida` |
| L7 | Anular fuera de la ventana | Viajar 31 días con `env.ledger().set_timestamp` → `VentanaAnulacionVencida` |
| L8 | Escritor comprometido | `set_escritor` por admin, y el antiguo ya no puede escribir; con `pausar`, todo `registrar` falla |
| L9 | Reinicializar | Segunda init → `YaInicializado` |
| L10 | TTL | Tras escribir, `get_ttl` ≥ lo esperado; la instancia se extiende |
| L11 | Lote con duplicados o demasiado grande | Se omiten los duplicados y se reportan; más de N → `LoteDemasiadoGrande` |
| L12 | Eventos | Cada operación emite exactamente el evento esperado con los datos correctos |
| L13 | *Upgrade* no autorizado | `upgrade` sin admin → falla |
| L14 | Propiedad | Proptest: con cualquier secuencia de registrar/anular, `validas == total - anulados` |

### Registro

| # | Ataque | Prueba |
|---|---|---|
| R1 | Escritura directa por un tercero o un socio | `registrar` llamado por una cuenta → falla la auth |
| R2 | Contrato impostor que se hace pasar por Tesoreria | Llamada desde otro contrato → falla |
| R3 | Id duplicado | → `CompraYaExiste` |
| R4 | Descripción vacía o gigante | → `DescripcionInvalida` |

### Tesoreria

| # | Ataque | Prueba |
|---|---|---|
| T1 | Un no-socio propone, aprueba o ejecuta | → `NoEsSocio` |
| T2 | Auto-aprobación | El proponente aprueba → `AutoAprobacion` |
| T3 | Doble voto | El mismo socio aprueba dos veces → `YaVoto` |
| T4 | Ejecutar con un solo voto | → `VotosInsuficientes` |
| T5 | Doble ejecución | Segundo `ejecutar` → `NoAbierta`; el saldo se descuenta una sola vez |
| T6 | Reentrada | Token SAC simulado malicioso cuyo `transfer` llama `ejecutar(id)` → la tx falla y no se paga dos veces |
| T7 | Saldo insuficiente | `ejecutar` revierte todo: el estado sigue `Abierta` y no hay registro en Registro |
| T8 | Expiración | Viajar más allá de `expira_ts` → aprobar o ejecutar da `Expirada` |
| T9 | Suplantar a un socio (auth) | `mock_auths` específicos: aprobar como B firmando como A → falla |
| T10 | Monto ≤ 0, destinatario = contrato | → errores |
| T11 | Cancelar y luego ejecutar | → `NoAbierta` |
| T12 | Registro se escribe con el `aprobador` correcto | Verificar con `obtener()` en Registro |
| T13 | Configuración inválida | Socios ≠ 3 o repetidos → `SociosInvalidos` |

### Ahorro

| # | Ataque | Prueba |
|---|---|---|
| A1 | Reclamar antes de 90 días | → `SinPeriodoCompleto` |
| A2 | Reclamar dos veces el mismo período | Segundo reclamo → `SinPeriodoCompleto`; los puntos no se duplican |
| A3 | Varios depósitos | Cada lote madura independiente; los puntos son correctos con depósitos en t=0, t=30 y t=60 días |
| A4 | Retirar el lote de otro | → `NoEsDueno` |
| A5 | Retirar dos veces | → `LoteInactivo`; el saldo se transfiere una sola vez |
| A6 | Retiro y redepósito para "reiniciar" el reloj | No aporta puntos extra (el período incompleto no cuenta) |
| A7 | Pérdida de fracción | Reclamar en el día 100 y luego en el 180 → 2 períodos en total, no 1 |
| A8 | Desbordamiento | Monto máximo × muchos períodos → `Desbordamiento`, no un *wrap* |
| A9 | Muchos lotes (crecimiento del storage) | Más de 50 → `DemasiadosLotes` |

### Backend (riesgos transversales)

- **IDOR:** anular o ver movimientos de otro tester → 404.
- **Doble envío del formulario:** la misma `Idempotency-Key` crea un único movimiento.
- **Inyección en la descripción:** se guarda cifrada y se escapa en Jinja/JS (sin `innerHTML` con datos del usuario).
- **Farming de niveles:** límite diario e histéresis.
- **Fugas en logs:** test de redacción.
- **XDR malicioso de un socio en `/api/tesoreria/enviar`:** verificar contrato, función y argumentos contra lo preparado. Solo se acepta un XDR cuyo hash de la parte sin firmar coincida con uno emitido por `/preparar` y guardado en BD, como hace hoy `validar_firma`.
- **`/api/ejecutar-recordatorio`:** eliminarlo.

---

## 12. Plan de pruebas

1. **Unitarias Soroban** (`cargo test` en `contratos/`):
   - Un `test.rs` por contrato con `Env::default()`.
   - **No usar `mock_all_auths()` en las pruebas de autorización**: usar `mock_auths` específicos y verificar `env.auths()`.
   - Tokens con `register_stellar_asset_contract_v2`.
   - Tiempo con `env.ledger().set_timestamp/set_sequence_number`.
   - Eventos con `env.events().all()`. TTL con `get_ttl`.
   - `proptest` para L14 y A3/A7.
   - Cubrir todos los casos de la sección 11.
2. **Integración local:** contenedor `stellar/quickstart --local` en CI (determinista y sin Friendbot), `stellar contract deploy` y luego pytest marcado `@integracion` contra RPC local.
3. **Integración Testnet** (manual o nocturna):
   - `desplegar_testnet.sh` y luego el script E2E: registrar 3 → anular 1 → el sincronizador refleja `validas = 2` → reconciliación OK.
   - Tesoreria: proponer (A) → aprobar (B) → ejecutar → el saldo del destinatario sube y aparece la compra en Registro.
   - Guardar la tabla de costos.
4. **pytest backend** (con `FakeCadena`, que implementa la misma interfaz que `cliente_rpc`):
   - **Outbox:** éxito, error definitivo, ambiguo y reintento, `RegistroYaExiste` como éxito, reinicio del worker a mitad del proceso.
   - **Sincronizador:** eventos duplicados, eventos desordenados, evento huérfano, avance atómico del cursor.
   - **Resúmenes** con tiempo congelado: cambio de día en Bogotá a las 23:59/00:01, semana ISO, mes de 28/31 días, sin datos.
   - **Progreso:** 99 → 100 sube; anular 1 en 100 no baja (histéresis); bajar de 94 sí baja; `subio_de_nivel` una sola vez.
   - **Endpoints:** 401 sin token, IDOR 404, idempotencia, límite diario, `mostrar_tutorial` una sola vez, telemetría rechaza `'Evolucionar Colmena'`.
   - **Privacidad:** el seudónimo es determinista y distinto con otro pepper; el ciphertext no contiene el texto; el *commit* coincide.
   - **Migraciones:**
     - Fixture `colmena_legacy.db` con el esquema actual y datos → `stamp 0001` → `upgrade head`.
     - Comprobar atribución (única, ambigua y sin logs), montos `round` y conteos.
     - `downgrade` hasta 0003 sin pérdida.
   - **Logging:** ninguna llave `S...` en los logs capturados.

---

## 13. Fases de implementación (cada una verificable por separado)

**F0. Base (½-1 día). Desbloquea todo lo demás**

- Alembic + `0001_baseline` + `stamp`; quitar `create_all`.
- `config.py`, `logging_seguro.py` y `.env.example` (recrear desde `REVISAR_zne-stellar.patch`; luego borrar el parche).
- `dependencias.tester_actual` y el front envía `X-Tester-Token` en todos los fetch.
- Mover `/zine/*` a `routers/zine.py`.
- **Eliminar el botón "Evolucionar Colmena"** (HTML, JS y lista blanca de telemetría).
- Eliminar `/api/ejecutar-recordatorio`.
- ✅ **Verificación:** `alembic upgrade head` en una BD vacía equivale a `create_all`; pytest de 401; el botón ya no existe; la app funciona igual.

**F1. Contrato LibroPersonal (2-3 días)**

- Workspace Rust, contrato, pruebas L1-L14, despliegue en Testnet, tabla de costos.
- ✅ **Verificación:** `cargo test` en verde; invocar `registrar`/`anular` con `stellar contract invoke` en Testnet; eventos visibles con `stellar events`; `contratos/desplegados.testnet.json` creado.

**F2. Backend on-chain + espejo (3-4 días)**

- Migraciones 0002 y 0003; `privacidad.py`; `cadena/*`; emisor y sincronizador como servicios de docker-compose con *secrets*.
- `POST /zine/registrar` (outbox), `GET /zine/movimientos`, `DELETE` (anulación) y `/api/ops/sync`.
- Migraciones 0004 y 0005 (legacy), con backup previo.
- ✅ **Verificación:**
  - pytest con `FakeCadena` en verde.
  - E2E en Testnet: registrar → `confirmado` en < 30 s; matar el emisor a mitad → se recupera sin duplicar; anular → `anulado`.
  - La reconciliación coincide.
  - Informe de migración legacy revisado.

**F3. Producto (2-3 días)**

- `resumenes.py` + `/zine/resumen` + *toasts*.
- `/zine/estadisticas` por usuario; `progreso.py` + HUD + animación de subida de nivel.
- Modal "Mis movimientos" con eliminación; tutorial con flag y opción de repetirlo; selector de tipo (gasto/ingreso/ahorro) en el formulario.
- ✅ **Verificación:** pytest de resúmenes y progreso; prueba manual con dos testers distintos (el nivel y los resúmenes no se mezclan); tutorial solo en el primer login.

**F4. Registro + Tesoreria (3-4 días)**

- Crate `interfaces`, contratos, pruebas R* y T*, despliegue.
- Routers de preparar/enviar, espejo de propuestas.
- **Eliminar los archivos `REVISAR_*aprobaciones*` y `REVISAR_stellar_*`** + migración 0007.
- ✅ **Verificación:** flujo de 3 socios en Testnet con billeteras reales (Freighter o CLI); T5 y T6 en verde.

**F5. Ahorro (2 días)**

- Contrato, pruebas A*, endpoints y espejo.
- ✅ **Verificación:** pruebas con viaje en el tiempo; en Testnet, depósito y retiro reales (el reclamo de 90 días solo se prueba en unitarias).

**F6. Endurecimiento**

- Revisión de seguridad (o auditoría externa si se va a mainnet).
- Alertas: atraso del sincronizador, saldo de la cuenta de servicio, pendientes de más de 10 min.
- Prueba de restauración desde backup + reconstrucción del espejo desde `eventos_cadena`.
- Decisión sobre `upgrade` (congelar o no).

---

## 14. Riesgos y decisiones que debes confirmar antes de construir

| # | Decisión | Recomendación |
|---|---|---|
| D1 | Los tokens hoy están **en claro**, no en SHA-256. ¿Migrar a `token_hash` (el login se ve igual)? Además, los tokens `tester01..20` son adivinables | Sí migrar. Y regenerar los tokens con entropía (≥ 128 bits) antes de cualquier usuario real |
| D2 | ¿Eliminar la multifirma clásica (`/api/aprobaciones`, tablas `aprobaciones` y `firmas_aprobacion`) cuando exista Tesoreria? | Sí, en F4 |
| D3 | Monto y categoría **en claro** on-chain para un seudónimo: permite perfilar hábitos. ¿Aceptable para Testnet o con consentimiento? Alternativa: guardar solo el *commit* del monto (se pierde la auditabilidad pública) | En claro en Testnet, con consentimiento explícito en el tutorial o los términos. Reevaluar antes de mainnet |
| D4 | Transacciones legacy: ¿atribución heurística + subirlas on-chain, o dejarlas fuera? ¿Cuentan para el nivel? | Heurística única + on-chain + sí cuentan. Las ambiguas quedan fuera |
| D5 | ¿El nivel baja al anular? ¿Ventana de anulación de 30 días? | Baja con histéresis de 5; ventana de 30 días |
| D6 | ¿Lotes desde el inicio? | No; activarlos cuando la medición lo justifique |
| D7 | "2 votos" = proponente + 1 aprobador, o 2 aprobadores distintos del proponente | Proponente + 1 (encaja con el campo único `aprobador`) |
| D8 | Admin de los contratos: llave fría única o multisig 2-de-3 de los socios | Multisig 2-de-3 (cuenta clásica con *signers*) como admin |
| D9 | ¿Contratos actualizables (`upgrade`)? | Sí en Testnet; decidir congelarlos antes de mainnet |
| D10 | TTL de Registro y Tesoreria (contabilidad del negocio, retención legal) | TTL largo con extensión periódica por un *cron*; espejo + eventos como respaldo |
| D11 | Regla de puntos de Ahorro (lotes, 1 punto por unidad por período de 90 días, sin retiro parcial) | Como está propuesto |
| D12 | `Base Datos/` (audios y transcripciones reales) no está en `.gitignore` | Sacarlo del repo y añadirlo a `.gitignore` ya |
| D13 | El campo "Comprobante" (archivo) hoy se recibe y **se ignora**. ¿Se guarda (cifrado, fuera de la cadena) o se quita del formulario? | Quitarlo hasta que haya un requisito |
| D14 | Token de Tesoreria y Ahorro: XLM nativo (SAC) o un activo propio o USDC de prueba | XLM SAC en Testnet por simplicidad |
| D15 | Zona horaria y moneda (COP, escala 100) para los resúmenes | `America/Bogota`, COP con 2 decimales internos |
| D16 | Pipeline de facturas, n8n y reglas (`webhooks`, `facturas`, `reglas`): ¿sigue en el nuevo rumbo? | No tocar hasta que decidas |

**Riesgos técnicos principales**

- **Retención de eventos del RPC.** Si el sincronizador se cae más allá de la ventana, se pierden eventos. Mitigación: alerta, `eventos_cadena` y reconstrucción por `getLedgerEntries`.
- **Cambios de API del SDK de Soroban entre versiones** (eventos, constructor, target wasm). Mitigación: fijar versiones exactas en F1 y no actualizar a mitad de proyecto.
- **SQLite con 3 procesos.** WAL ya está activo. Escrituras cortas y `busy_timeout`. Si crece, pasar a Postgres (Alembic lo hace viable).
- **Perfilado por montos en claro** (D3).
- **Llave de servicio única** como punto de falla. Mitigación: `pausar`, `set_escritor` y saldo mínimo.
