"""
Aprobaciones multifirma (k de 3) sobre Stellar.

El servidor solo maneja XDR y firmas públicas. Las llaves secretas viven en
manos de cada socio (scripts/REVISAR_stellar_firmar.py). No hay llamadas de red al importar.
"""
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from stellar_sdk import Account, Keypair, Network, Server, TransactionBuilder, TransactionEnvelope
from stellar_sdk.exceptions import BadRequestError, NotFoundError
from stellar_sdk.decorated_signature import DecoratedSignature
from stellar_sdk.strkey import StrKey

from ..config import obtener_config


class ConfigError(Exception):
    """Configuración Stellar ausente o inválida."""


class FirmaInvalida(Exception):
    """La firma o el XDR recibido no es válido."""


class StellarError(Exception):
    """Fallo de red o respuesta de Horizon."""

    def __init__(self, mensaje: str, definitivo: bool = False):
        super().__init__(mensaje)
        self.definitivo = definitivo  # True: Horizon rechazó la tx (no es ambiguo)


@dataclass(frozen=True)
class StellarConfig:
    red: str
    passphrase: str
    horizon_url: str
    cuenta: str
    socios: Tuple[str, ...]
    umbral: int
    ttl_horas: int


def parsear_socios(valor: str) -> Tuple[str, ...]:
    socios = tuple(s.strip() for s in (valor or "").split(",") if s.strip())
    if len(socios) != 3 or len(set(socios)) != 3:
        raise ConfigError("STELLAR_SOCIOS debe contener exactamente 3 llaves públicas distintas")
    for s in socios:
        if not StrKey.is_valid_ed25519_public_key(s):
            raise ConfigError(f"Llave pública inválida en STELLAR_SOCIOS: {s}")
    return socios


def passphrase_de(red: str) -> str:
    if red == "public":
        return Network.PUBLIC_NETWORK_PASSPHRASE
    if red == "testnet":
        return Network.TESTNET_NETWORK_PASSPHRASE
    raise ConfigError("STELLAR_NETWORK debe ser 'testnet' o 'public'")


def cargar_config() -> StellarConfig:
    cfg = obtener_config()
    red = cfg.stellar_network.strip().lower()
    cuenta = cfg.stellar_cuenta_multisig.strip()
    if not StrKey.is_valid_ed25519_public_key(cuenta):
        raise ConfigError("STELLAR_CUENTA_MULTISIG ausente o inválida")
    umbral = cfg.stellar_umbral
    ttl = cfg.aprobacion_ttl_horas
    if not 1 <= umbral <= 3 or ttl < 1:
        raise ConfigError("STELLAR_UMBRAL debe estar entre 1 y 3 y APROBACION_TTL_HORAS ser >= 1")
    return StellarConfig(
        red=red,
        passphrase=passphrase_de(red),
        horizon_url=cfg.stellar_horizon_url,
        cuenta=cuenta,
        socios=parsear_socios(cfg.stellar_socios),
        umbral=umbral,
        ttl_horas=ttl,
    )


def _normalizar_texto(texto: Optional[str]) -> str:
    return " ".join((texto or "").split())


def hash_factura(registro, monto: float) -> str:
    canonico = json.dumps(
        {
            "id": registro.id,
            "nombre_archivo": registro.nombre_archivo,
            "texto": _normalizar_texto(registro.texto_extraido),
            "monto": float(monto),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def cargar_cuenta(cfg: StellarConfig) -> Account:
    """Lee el número de secuencia actual desde Horizon (llamada de red)."""
    try:
        return Server(cfg.horizon_url).load_account(cfg.cuenta)
    except Exception as exc:
        raise StellarError(f"No se pudo cargar la cuenta en Horizon: {exc}") from exc


def construir_tx(cfg: StellarConfig, registro_id: int, factura_hash: str,
                 cuenta: Optional[Account] = None) -> TransactionEnvelope:
    """Arma la tx (manage_data con el hash de la factura). Sin `cuenta`, consulta Horizon."""
    cuenta = cuenta or cargar_cuenta(cfg)
    return (
        TransactionBuilder(
            source_account=cuenta,
            network_passphrase=cfg.passphrase,
            base_fee=100,
        )
        .append_manage_data_op(f"zne:fac:{registro_id}", bytes.fromhex(factura_hash))
        .set_timeout(cfg.ttl_horas * 3600)
        .build()
    )


def validar_firma(cfg: StellarConfig, aprobacion, xdr_firmado: str) -> Tuple[str, str]:
    """Devuelve (public_key, firma_b64) de un socio válido o lanza FirmaInvalida."""
    try:
        env = TransactionEnvelope.from_xdr(xdr_firmado, cfg.passphrase)
    except Exception as exc:
        raise FirmaInvalida("XDR inválido") from exc
    if env.hash_hex() != aprobacion.tx_hash:
        raise FirmaInvalida("El XDR no corresponde a esta aprobación")
    datos = env.hash()
    if not env.signatures:
        raise FirmaInvalida("El XDR no contiene firmas")
    for firma in env.signatures:
        for socio in cfg.socios:
            kp = Keypair.from_public_key(socio)
            if kp.signature_hint() != firma.signature_hint:
                continue
            try:
                kp.verify(datos, firma.signature)
            except Exception:
                continue
            return socio, base64.b64encode(firma.signature).decode()
    raise FirmaInvalida("Ninguna firma válida de un socio autorizado")


def calcular_estado(aprobacion, n_validas: int) -> str:
    """pendiente / parcial / aprobada. Nunca recalcula enviada ni error."""
    if aprobacion.estado in ("enviada", "error"):
        return aprobacion.estado
    if n_validas >= aprobacion.umbral:
        return "aprobada"
    return "parcial" if n_validas >= 1 else "pendiente"


def expirada(aprobacion) -> bool:
    exp = datetime.fromisoformat(aprobacion.expira_en)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= exp


def _decorada(public_key: str, firma: bytes):
    hint = Keypair.from_public_key(public_key).signature_hint()
    return DecoratedSignature(hint, firma)


def enviar(cfg: StellarConfig, aprobacion) -> str:
    """Envía la tx firmada a Horizon y devuelve su hash. Lanza StellarError."""
    env = TransactionEnvelope.from_xdr(aprobacion.xdr_base, cfg.passphrase)
    for f in aprobacion.firmas:
        env.signatures.append(_decorada(f.public_key, base64.b64decode(f.firma_b64)))
    try:
        resp = Server(cfg.horizon_url).submit_transaction(env)
    except BadRequestError as exc:
        raise StellarError(f"Horizon rechazó la transacción: {exc}", definitivo=True) from exc
    except Exception as exc:
        raise StellarError(f"Fallo enviando a Horizon: {exc}") from exc
    return resp.get("hash") or aprobacion.tx_hash


def existe_en_horizon(cfg: StellarConfig, tx_hash: str) -> bool:
    """True si Horizon ya conoce la tx. Lanza StellarError si no se puede consultar."""
    try:
        Server(cfg.horizon_url).transactions().transaction(tx_hash).call()
        return True
    except NotFoundError:
        return False
    except Exception as exc:
        raise StellarError(f"No se pudo consultar Horizon: {exc}") from exc
