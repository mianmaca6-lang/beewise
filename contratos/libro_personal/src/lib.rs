//! # LibroPersonal
//!
//! Registra **pruebas de integridad** de movimientos financieros personales.
//!
//! Privacidad (decisión D3): el contrato solo recibe dos valores de 32 bytes:
//! - `seudonimo`: identificador aleatorio del usuario generado por el backend
//!   (no deriva de su teléfono, chat de Telegram ni nombre).
//! - `hash`: SHA-256 de la representación canónica del registro, que incluye un
//!   nonce aleatorio de 256 bits (no se puede revertir por diccionario).
//!
//! El contrato **no tiene** parámetros para monto, categoría, concepto ni fecha
//! del movimiento: es imposible enviarlos por error a través de esta interfaz.
//! La prueba `interfaz_publica_solo_recibe_bytes32` lo verifica sobre la especificación.
//!
//! Solo la cuenta `escritor` (cuenta de servicio del backend FastAPI) puede registrar.
//! El `admin` (llave fría) solo puede rotar al escritor.
#![no_std]

use soroban_sdk::{contract, contracterror, contractevent, contractimpl, contracttype, Address, BytesN, Env};

/// ~1 día en ledgers (ledger de ~5 s).
const DIA_EN_LEDGERS: u32 = 17_280;
/// Si al TTL le quedan menos de 30 días, se extiende hasta 120 días.
const TTL_UMBRAL: u32 = 30 * DIA_EN_LEDGERS;
const TTL_EXTENDER_A: u32 = 120 * DIA_EN_LEDGERS;

#[contracttype]
#[derive(Clone)]
enum Clave {
    Admin,
    Escritor,
    Prueba(BytesN<32>),
}

/// Lo que queda guardado por cada hash. Ningún dato financiero.
#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Prueba {
    pub seudonimo: BytesN<32>,
    /// Número de ledger en que se registró (marca temporal verificable de la red).
    pub ledger: u32,
    /// Marca de tiempo del ledger (segundos UNIX). Es la hora de registro, no la del gasto.
    pub registrado_en: u64,
}

#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    /// El hash ya fue registrado. El backend lo usa como señal de idempotencia.
    YaRegistrado = 1,
    /// Hash o seudónimo con todos los bytes en cero (casi seguro un error del cliente).
    ValorInvalido = 2,
    /// El contrato no tiene escritor/admin (no debería ocurrir: se fija en el constructor).
    NoInicializado = 3,
}

#[contractevent(topics = ["libro_personal", "registro"])]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventoRegistro {
    #[topic]
    pub seudonimo: BytesN<32>,
    pub hash: BytesN<32>,
    pub ledger: u32,
}

#[contractevent(topics = ["libro_personal", "escritor"])]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventoEscritor {
    pub anterior: Address,
    pub nuevo: Address,
}

#[contract]
pub struct LibroPersonal;

fn es_cero(v: &BytesN<32>) -> bool {
    v.to_array() == [0u8; 32]
}

fn extender_instancia(env: &Env) {
    env.storage().instance().extend_ttl(TTL_UMBRAL, TTL_EXTENDER_A);
}

fn leer_direccion(env: &Env, clave: &Clave) -> Result<Address, Error> {
    env.storage().instance().get(clave).ok_or(Error::NoInicializado)
}

#[contractimpl]
impl LibroPersonal {
    /// Se ejecuta una sola vez, de forma atómica con el despliegue.
    pub fn __constructor(env: Env, admin: Address, escritor: Address) {
        env.storage().instance().set(&Clave::Admin, &admin);
        env.storage().instance().set(&Clave::Escritor, &escritor);
        extender_instancia(&env);
    }

    /// Registra la prueba de integridad `hash` asociada a `seudonimo`.
    /// Devuelve el número de ledger del registro.
    pub fn registrar(env: Env, seudonimo: BytesN<32>, hash: BytesN<32>) -> Result<u32, Error> {
        let escritor = leer_direccion(&env, &Clave::Escritor)?;
        escritor.require_auth();

        if es_cero(&seudonimo) || es_cero(&hash) {
            return Err(Error::ValorInvalido);
        }
        let clave = Clave::Prueba(hash.clone());
        if env.storage().persistent().has(&clave) {
            return Err(Error::YaRegistrado);
        }

        let ledger = env.ledger().sequence();
        let prueba = Prueba {
            seudonimo: seudonimo.clone(),
            ledger,
            registrado_en: env.ledger().timestamp(),
        };
        env.storage().persistent().set(&clave, &prueba);
        env.storage().persistent().extend_ttl(&clave, TTL_UMBRAL, TTL_EXTENDER_A);
        extender_instancia(&env);

        EventoRegistro { seudonimo, hash, ledger }.publish(&env);
        Ok(ledger)
    }

    /// ¿Existe una prueba con este hash?
    pub fn existe(env: Env, hash: BytesN<32>) -> bool {
        env.storage().persistent().has(&Clave::Prueba(hash))
    }

    /// Devuelve la prueba registrada para `hash`, si existe.
    pub fn obtener(env: Env, hash: BytesN<32>) -> Option<Prueba> {
        env.storage().persistent().get(&Clave::Prueba(hash))
    }

    /// Verifica que `hash` esté registrado **y** pertenezca a `seudonimo`.
    /// Quien tenga el registro original (descifrado localmente) recalcula el hash
    /// y lo compara aquí: si alguien alteró el registro, el hash ya no coincide.
    pub fn verificar(env: Env, seudonimo: BytesN<32>, hash: BytesN<32>) -> bool {
        match Self::obtener(env, hash) {
            Some(p) => p.seudonimo == seudonimo,
            None => false,
        }
    }

    /// Rota la cuenta de servicio (p. ej. si su llave se compromete). Solo admin.
    pub fn cambiar_escritor(env: Env, nuevo: Address) -> Result<(), Error> {
        let admin = leer_direccion(&env, &Clave::Admin)?;
        admin.require_auth();
        let anterior = leer_direccion(&env, &Clave::Escritor)?;
        env.storage().instance().set(&Clave::Escritor, &nuevo);
        extender_instancia(&env);
        EventoEscritor { anterior, nuevo }.publish(&env);
        Ok(())
    }

    pub fn escritor(env: Env) -> Result<Address, Error> {
        leer_direccion(&env, &Clave::Escritor)
    }

    pub fn admin(env: Env) -> Result<Address, Error> {
        leer_direccion(&env, &Clave::Admin)
    }
}

#[cfg(test)]
mod test;
