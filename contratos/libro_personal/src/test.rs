#![cfg(test)]
extern crate std;

use super::*;
use soroban_sdk::{
    testutils::{
        storage::Persistent as _, Address as _, AuthorizedFunction, AuthorizedInvocation, Events as _, Ledger as _,
        MockAuth, MockAuthInvoke,
    },
    xdr::{Limits, ReadXdr, ScSpecEntry, ScSpecTypeDef},
    Event as _, IntoVal, Symbol,
};

struct Escenario {
    env: Env,
    id: Address,
    admin: Address,
    escritor: Address,
}

impl Escenario {
    fn nuevo() -> Self {
        let env = Env::default();
        let admin = Address::generate(&env);
        let escritor = Address::generate(&env);
        let id = env.register(LibroPersonal, (&admin, &escritor));
        Escenario { env, id, admin, escritor }
    }
    fn cliente(&self) -> LibroPersonalClient<'_> {
        LibroPersonalClient::new(&self.env, &self.id)
    }
    fn b32(&self, byte: u8) -> BytesN<32> {
        BytesN::from_array(&self.env, &[byte; 32])
    }
}

// ---------- Registrar / consultar / verificar ----------

#[test]
fn registra_consulta_y_verifica_un_hash() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    let (seud, hash) = (e.b32(7), e.b32(9));
    e.env.ledger().set_sequence_number(1234);
    e.env.ledger().set_timestamp(1_700_000_000);

    assert!(!c.existe(&hash));
    let ledger = c.mock_all_auths().registrar(&seud, &hash);
    assert_eq!(ledger, 1234);

    assert!(c.existe(&hash));
    assert_eq!(
        c.obtener(&hash),
        Some(Prueba { seudonimo: seud.clone(), ledger: 1234, registrado_en: 1_700_000_000 })
    );
    assert!(c.verificar(&seud, &hash));
}

#[test]
fn verificar_falla_con_otro_seudonimo_o_hash_alterado() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    c.mock_all_auths().registrar(&e.b32(1), &e.b32(2));
    assert!(!c.verificar(&e.b32(3), &e.b32(2))); // otro usuario
    assert!(!c.verificar(&e.b32(1), &e.b32(4))); // contenido alterado → otro hash
    assert_eq!(c.obtener(&e.b32(4)), None);
}

// ---------- Duplicados ----------

#[test]
fn hash_duplicado_se_rechaza_y_no_sobrescribe() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    e.env.ledger().set_sequence_number(10);
    c.mock_all_auths().registrar(&e.b32(1), &e.b32(2));
    e.env.ledger().set_sequence_number(20);
    // Mismo hash, incluso desde otro seudónimo: rechazado.
    assert_eq!(c.mock_all_auths().try_registrar(&e.b32(5), &e.b32(2)), Err(Ok(Error::YaRegistrado)));
    let p = c.obtener(&e.b32(2)).unwrap();
    assert_eq!(p.seudonimo, e.b32(1));
    assert_eq!(p.ledger, 10);
}

#[test]
fn valores_en_cero_se_rechazan() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    assert_eq!(c.mock_all_auths().try_registrar(&e.b32(0), &e.b32(2)), Err(Ok(Error::ValorInvalido)));
    assert_eq!(c.mock_all_auths().try_registrar(&e.b32(1), &e.b32(0)), Err(Ok(Error::ValorInvalido)));
}

// ---------- Autorización ----------

#[test]
fn registrar_exige_firma_del_escritor() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    c.mock_all_auths().registrar(&e.b32(1), &e.b32(2));
    assert_eq!(
        e.env.auths(),
        std::vec![(
            e.escritor.clone(),
            AuthorizedInvocation {
                function: AuthorizedFunction::Contract((
                    e.id.clone(),
                    Symbol::new(&e.env, "registrar"),
                    (e.b32(1), e.b32(2)).into_val(&e.env),
                )),
                sub_invocations: std::vec![],
            }
        )]
    );
}

#[test]
fn un_tercero_no_puede_registrar() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    let intruso = Address::generate(&e.env);
    let (s, h) = (e.b32(1), e.b32(2));
    let r = c
        .mock_auths(&[MockAuth {
            address: &intruso,
            invoke: &MockAuthInvoke {
                contract: &e.id,
                fn_name: "registrar",
                args: (s.clone(), h.clone()).into_val(&e.env),
                sub_invokes: &[],
            },
        }])
        .try_registrar(&s, &h);
    assert!(r.is_err());
    assert!(!c.existe(&h));
}

#[test]
fn sin_ninguna_firma_no_se_registra() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    assert!(c.try_registrar(&e.b32(1), &e.b32(2)).is_err());
    assert!(!c.existe(&e.b32(2)));
}

#[test]
fn admin_rota_escritor_y_el_anterior_pierde_permiso() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    let nuevo = Address::generate(&e.env);
    c.mock_all_auths().cambiar_escritor(&nuevo);
    assert_eq!(e.env.auths()[0].0, e.admin);
    assert_eq!(c.escritor(), nuevo);

    // El escritor anterior ya no puede escribir.
    let (s, h) = (e.b32(1), e.b32(2));
    let r = c
        .mock_auths(&[MockAuth {
            address: &e.escritor,
            invoke: &MockAuthInvoke {
                contract: &e.id,
                fn_name: "registrar",
                args: (s.clone(), h.clone()).into_val(&e.env),
                sub_invokes: &[],
            },
        }])
        .try_registrar(&s, &h);
    assert!(r.is_err());
}

#[test]
fn el_escritor_no_puede_rotarse_a_si_mismo() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    let otro = Address::generate(&e.env);
    let r = c
        .mock_auths(&[MockAuth {
            address: &e.escritor,
            invoke: &MockAuthInvoke {
                contract: &e.id,
                fn_name: "cambiar_escritor",
                args: (otro.clone(),).into_val(&e.env),
                sub_invokes: &[],
            },
        }])
        .try_cambiar_escritor(&otro);
    assert!(r.is_err());
    assert_eq!(c.escritor(), e.escritor);
}

// ---------- Eventos y TTL ----------

#[test]
fn emite_evento_solo_con_seudonimo_hash_y_ledger() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    e.env.ledger().set_sequence_number(55);
    c.mock_all_auths().registrar(&e.b32(1), &e.b32(2));
    let esperado = EventoRegistro { seudonimo: e.b32(1), hash: e.b32(2), ledger: 55 };
    assert_eq!(e.env.events().all(), std::vec![esperado.to_xdr(&e.env, &e.id)]);
}

#[test]
fn extiende_ttl_de_la_prueba() {
    let e = Escenario::nuevo();
    let c = e.cliente();
    c.mock_all_auths().registrar(&e.b32(1), &e.b32(2));
    let ttl = e.env.as_contract(&e.id, || e.env.storage().persistent().get_ttl(&Clave::Prueba(e.b32(2))));
    assert!(ttl >= TTL_UMBRAL, "ttl={ttl}");
}

// ---------- Privacidad: la interfaz no admite datos financieros ----------

fn entradas(spec: &[u8]) -> std::vec::Vec<(std::string::String, ScSpecTypeDef)> {
    match ScSpecEntry::from_xdr(spec, Limits::none()).unwrap() {
        ScSpecEntry::FunctionV0(f) => f
            .inputs
            .iter()
            .map(|i| (std::string::String::from_utf8(i.name.to_vec()).unwrap(), i.type_.clone()))
            .collect(),
        otro => panic!("no es función: {otro:?}"),
    }
}

#[test]
fn interfaz_publica_solo_recibe_bytes32() {
    let bytes32 = |t: &ScSpecTypeDef| matches!(t, ScSpecTypeDef::BytesN(b) if b.n == 32);

    // registrar recibe exactamente (seudonimo: BytesN<32>, hash: BytesN<32>).
    let reg = entradas(&LibroPersonal::spec_xdr_registrar());
    assert_eq!(reg.len(), 2);
    assert_eq!(reg[0].0, "seudonimo");
    assert_eq!(reg[1].0, "hash");
    assert!(reg.iter().all(|(_, t)| bytes32(t)));

    // Ninguna función acepta montos, textos, símbolos ni números: solo BytesN<32> o Address.
    let todas = [
        entradas(&LibroPersonal::spec_xdr_registrar()),
        entradas(&LibroPersonal::spec_xdr_existe()),
        entradas(&LibroPersonal::spec_xdr_obtener()),
        entradas(&LibroPersonal::spec_xdr_verificar()),
        entradas(&LibroPersonal::spec_xdr_cambiar_escritor()),
        entradas(&LibroPersonal::spec_xdr___constructor()),
    ];
    let prohibidos = ["monto", "categoria", "concepto", "descripcion", "fecha", "tipo"];
    for f in todas.iter() {
        for (nombre, tipo) in f {
            assert!(bytes32(tipo) || matches!(tipo, ScSpecTypeDef::Address), "{nombre}: {tipo:?}");
            assert!(!prohibidos.contains(&nombre.as_str()), "{nombre}");
        }
    }
}
