# Libro de socios y votaciones (D7): diseño para la fase siguiente

> **Estado:** DISEÑO. No hay contrato de socios, ni endpoints, ni lógica on-chain de votación.
>
> Lo que **sí existe** desde la Fase 1:
> - Tablas espejo `socios`, `propuestas` y `votos` (migración 0002). Están vacías y nada escribe en ellas.
> - La función pura `app/services/votaciones.py::votos_requeridos`, probada.

## 1. Separación de libros

| | LibroPersonal (implementado) | Tesoreria + Registro de socios (pendiente) |
|---|---|---|
| Contrato | `contratos/libro_personal` | `contratos/tesoreria` (nuevo crate) |
| Datos on-chain | Solo `seudonimo` y `hash` (32 bytes cada uno) | Datos acordados **en claro**: descripción, tipo, monto, destinatario, proponente y votos |
| Quién escribe | Cuenta de servicio del backend | Cada socio con **su propia billetera** (`require_auth`). El backend solo arma XDR sin firmar |
| Tablas locales | `usuarios`, `registros_personales` | `socios`, `propuestas`, `votos` |
| Endpoint | `POST /api/v1/registros` con `ambito=personal` | `ambito=socio` responde **501** hasta esta fase |

No comparten storage, eventos ni claves. Un fallo o un cambio en uno no afecta al otro.

## 2. Interfaz propuesta del contrato `Tesoreria`

```text
__constructor(socios: Vec<Address>, modalidad: Modalidad, token: Address)
proponer(proponente, tipo: TipoPropuesta, datos: DatosPropuesta) -> u64   // el proponente = voto 1
votar(socio, id: u64, a_favor: bool)
ejecutar(socio, id: u64)                                                  // si aprobada
cancelar(proponente, id: u64)
obtener(id) -> Propuesta
socios() -> Vec<Address>
modalidad() -> Modalidad

enum Modalidad      { Unanimidad, MayoriaSimple }
enum TipoPropuesta  { Compra, CambiarModalidad, AgregarSocio }
enum DatosPropuesta { Compra{descripcion, tipo, monto: i128, destinatario}, CambiarModalidad(Modalidad), AgregarSocio(Address) }
enum Estado         { Abierta, Aprobada, Rechazada, Ejecutada, Cancelada, Expirada }

struct Propuesta {
  id, tipo, datos, proponente,
  socios_habilitados: Vec<Address>,   // FOTO del padrón al crear (regla 7)
  votos_requeridos: u32,              // FOTO del umbral al crear (regla 7)
  a_favor: Vec<Address>, en_contra: Vec<Address>,
  estado, creada_ts, expira_ts
}
```

## 3. Cómo se cumple cada regla de D7

| Regla | Mecanismo |
|---|---|
| Unanimidad = n; MayoriaSimple = n/2 + 1 (división entera) | `votos_requeridos(n, modalidad)`: misma fórmula que `app/services/votaciones.py`, con las mismas pruebas (n = 1…7) |
| El proponente cuenta como primer voto | `proponer` exige `proponente.require_auth()` y lo agrega a `a_favor`. Es seguro porque la firma del proponente autoriza explícitamente la propuesta |
| Cambiar la modalidad requiere votación | No existe un setter directo: solo `ejecutar` de una propuesta `CambiarModalidad` aprobada |
| Agregar un socio requiere votación | Igual, con una propuesta `AgregarSocio`. No hay `set_socios` ni admin que pueda modificar el padrón |
| Nadie modifica el padrón unilateralmente | Sin admin sobre `socios`. Las actualizaciones de código (si las hay) exigen una propuesta aprobada |
| Estado claro | `Estado` explícito. Transiciones: `Abierta → Aprobada → Ejecutada`; `Abierta → Rechazada / Cancelada / Expirada` |
| Un voto por socio | `votar` comprueba que el socio no esté en `a_favor ∪ en_contra` (error `YaVoto`) y que esté en `socios_habilitados` **de la propuesta**. La tabla espejo `votos` tiene `UNIQUE(propuesta_id, socio_id)` |
| Sin umbral retroactivo | El umbral y el padrón se copian dentro de la propuesta al crearla. Un socio agregado después no vota en propuestas anteriores, y un cambio de modalidad no altera las abiertas |
| Rechazo anticipado | Si `en_contra > n - votos_requeridos`, la aprobación ya es imposible y el estado pasa a `Rechazada` |

Con 2 socios, ambas modalidades exigen 2 votos.

## 4. Pendientes que deben confirmarse antes de implementar

- Expiración de las propuestas (sugerido: 7 días).
- Token de pago de las compras (en Testnet, XLM vía SAC).
- Si "agregar socio" debe cumplir la modalidad vigente o exigir siempre unanimidad (sugerido: unanimidad).
- Retirar socios no está en D7. Si se necesita, sería otra propuesta votada.
