"""Fase 1 — Integridad: hash determinista, sensible a cambios, resistente a diccionario."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import integridad as I
from app.services.votaciones import Modalidad, votos_requeridos

SEUD = "a" * 64
NONCE = "b" * 64


def _base(**cambios):
    datos = dict(
        seudonimo=SEUD, tipo="gasto", monto="15000", moneda="COP", categoria="Comida",
        concepto="Almuerzo  corrientazo", fecha=datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc), nonce=NONCE,
    )
    datos.update(cambios)
    return datos


def _h(**cambios) -> str:
    return I.calcular_hash(I.serializar(I.construir_canonico(**_base(**cambios)))).hex()


def test_mismo_registro_normalizado_mismo_hash():
    assert _h() == _h()
    # Variantes que se normalizan igual:
    assert _h() == _h(monto="15000.00")
    assert _h() == _h(categoria="  COMIDA ")
    assert _h() == _h(concepto=" Almuerzo corrientazo ")
    assert _h() == _h(moneda="cop")
    assert _h() == _h(tipo="GASTO")
    bogota = timezone(timedelta(hours=-5))
    assert _h() == _h(fecha=datetime(2026, 11, 12, 7, 0, 0, 999, tzinfo=bogota))
    # Unicode: "é" compuesto vs "e" + acento combinante.
    assert _h(concepto="Café") == _h(concepto="Café")


@pytest.mark.parametrize(
    "cambio",
    [
        {"monto": "15000.01"}, {"tipo": "ingreso"}, {"moneda": "USD"}, {"categoria": "Transporte"},
        {"concepto": "Almuerzo corrientazO"}, {"fecha": datetime(2026, 11, 12, 12, 0, 1, tzinfo=timezone.utc)},
        {"seudonimo": "c" * 64}, {"nonce": "d" * 64},
    ],
)
def test_cualquier_modificacion_cambia_el_hash(cambio):
    assert _h() != _h(**cambio)


def test_nonce_aleatorio_impide_ataque_por_diccionario():
    # El mismo gasto con nonces distintos produce hashes distintos e impredecibles.
    hashes = {_h(nonce=I.nuevo_nonce()) for _ in range(20)}
    assert len(hashes) == 20


def test_verificar():
    s = I.serializar(I.construir_canonico(**_base()))
    h = I.calcular_hash(s).hex()
    assert I.verificar(s, h)
    assert not I.verificar(s.replace(b"15000.00", b"15000.01"), h)


def test_serializacion_es_json_canonico():
    s = I.serializar(I.construir_canonico(**_base()))
    assert s.startswith(b'{"ambito":"personal","categoria":"comida"')
    assert b" :" not in s and b", " not in s


@pytest.mark.parametrize(
    "cambio",
    [
        {"monto": "10.005"}, {"monto": "0"}, {"monto": "-5"}, {"monto": "abc"}, {"monto": "1e20"},
        {"fecha": datetime(2026, 1, 1)},  # sin zona horaria
        {"tipo": "prestamo"}, {"moneda": "PESOS"}, {"categoria": "   "}, {"seudonimo": "xyz"}, {"nonce": "0"},
    ],
)
def test_entradas_invalidas(cambio):
    with pytest.raises(I.RegistroInvalido):
        I.construir_canonico(**_base(**cambio))


@pytest.mark.parametrize(
    "n,unanimidad,mayoria", [(1, 1, 1), (2, 2, 2), (3, 3, 2), (4, 4, 3), (5, 5, 3), (6, 6, 4), (7, 7, 4)]
)
def test_d7_votos_requeridos(n, unanimidad, mayoria):
    assert votos_requeridos(n, Modalidad.UNANIMIDAD) == unanimidad
    assert votos_requeridos(n, Modalidad.MAYORIA_SIMPLE) == mayoria


def test_d7_sin_socios_es_invalido():
    with pytest.raises(ValueError):
        votos_requeridos(0, Modalidad.MAYORIA_SIMPLE)
