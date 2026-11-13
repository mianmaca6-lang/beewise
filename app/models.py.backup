from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Float, Text, UniqueConstraint, LargeBinary, CheckConstraint, Index
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime, timezone

class Cliente(Base):
    __tablename__ = "clientes"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre_empresa = Column(String, unique=True, index=True)
    nit = Column(String, unique=True)
    
    # Relación con sus reglas personalizadas (El Foso)
    reglas = relationship("ReglaClasificacion", back_populates="cliente")

class ReglaClasificacion(Base):
    """
    Esta es la memoria adaptable. Si el sistema detecta 'palabra_clave' 
    en una transcripción o factura, aplica esta regla automáticamente.
    """
    __tablename__ = "reglas_clasificacion"
    
    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    palabra_clave = Column(String, index=True) # ej: "Autogrúas JCC" o "Gasolina"
    cuenta_contable = Column(String)           # ej: "519595"
    aplica_retencion = Column(Boolean, default=False)
    porcentaje_retencion = Column(Float, default=0.0)
    
    cliente = relationship("Cliente", back_populates="reglas")

class RegistroArchivo(Base):
    """
    Para llevar trazabilidad de lo que hay en 'Base Datos/input' y 'check'
    """
    __tablename__ = "registros_archivos"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre_archivo = Column(String, unique=True) # ej: "WhatsApp Audio 2026-11-07..."
    estado = Column(String, default="input")     # "input", "procesado", "check", "error"
    texto_extraido = Column(String, nullable=True)




class Tester(Base):
    __tablename__ = "testers"
    
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True) # Ej. "tester01"
    activo = Column(Boolean, default=True)
    
    logs = relationship("LogTelemetria", back_populates="tester")

class LogTelemetria(Base):
    __tablename__ = "logs_telemetria"
    
    id = Column(Integer, primary_key=True, index=True)
    tester_id = Column(Integer, ForeignKey("testers.id"))
    accion = Column(String)  # Ej. "login", "abrir_registro", "evolucionar"
    detalle = Column(String, nullable=True)
    fecha_hora = Column(String, default=lambda: datetime.now().isoformat())
    
    tester = relationship("Tester", back_populates="logs")

class Transaccion(Base):
    """
    Motor de doble contabilidad para Zine MVP.
    Registra el movimiento exacto del dinero entre cuentas.
    """
    __tablename__ = "transacciones"
    
    id = Column(Integer, primary_key=True, index=True)
    concepto = Column(String, index=True)       # Ej. "Chocorramo" o "Quincena"
    monto = Column(Float)                       # Ej. 3000 o 1500000
    tipo = Column(String)                       # "ingreso" o "gasto"
    cuenta_origen = Column(String)              # De dónde sale el dinero (Ej. "Bolsillo", "Banco")
    cuenta_destino = Column(String)             # A dónde va (Ej. "Gasto Comida", "Ahorros")
    fecha_hora = Column(String, default=lambda: datetime.now().isoformat())


def _ahora_iso():
    return datetime.now(timezone.utc).isoformat()


# REVISAR: Aprobacion y FirmaAprobacion pertenecen a la multifirma clásica (manage_data).
# Se reemplazan por el contrato Tesoreria + espejo `propuestas_compra` (docs/PLAN_SOROBAN.md, fase 4).
class Aprobacion(Base):
    """
    Aprobación multifirma (2 de 3) de una factura en Stellar.
    Solo se guardan XDR y firmas públicas; jamás llaves secretas.
    """
    __tablename__ = "aprobaciones"

    id = Column(Integer, primary_key=True, index=True)
    registro_id = Column(Integer, ForeignKey("registros_archivos.id"), unique=True, nullable=False)
    factura_hash = Column(String(64), nullable=False)
    monto = Column(Float, default=0.0)
    tx_hash = Column(String, unique=True, nullable=False)
    xdr_base = Column(Text, nullable=False)
    cuenta_origen = Column(String, nullable=False)
    red = Column(String, nullable=False)
    estado = Column(String, default="pendiente")  # pendiente, parcial, aprobada, enviada, error
    umbral = Column(Integer, default=2)
    error_detalle = Column(String, nullable=True)
    expira_en = Column(String, nullable=False)
    horizon_hash = Column(String, nullable=True)
    creado_en = Column(String, default=_ahora_iso)
    actualizado_en = Column(String, default=_ahora_iso, onupdate=_ahora_iso)

    firmas = relationship("FirmaAprobacion", back_populates="aprobacion", cascade="all, delete-orphan")


class FirmaAprobacion(Base):
    __tablename__ = "firmas_aprobacion"
    __table_args__ = (UniqueConstraint("aprobacion_id", "public_key", name="uq_firma_aprobacion_socio"),)

    id = Column(Integer, primary_key=True, index=True)
    aprobacion_id = Column(Integer, ForeignKey("aprobaciones.id"), nullable=False)
    public_key = Column(String, nullable=False)
    firma_b64 = Column(String, nullable=False)
    creado_en = Column(String, default=_ahora_iso)

    aprobacion = relationship("Aprobacion", back_populates="firmas")


# =====================================================================================
# FASE 1 — Libro personal privado y referencias Soroban
# Reglas: ningún monto, categoría ni concepto personal en texto plano; ninguna llave
# privada en la base de datos.
# =====================================================================================

ESTADOS_SINCRONIZACION = ("pendiente", "enviado", "confirmado", "error_reintentable", "error")


class Usuario(Base):
    """
    Usuario común (llega por Telegram → n8n). No se guarda su id de Telegram en claro:
    solo un HMAC con secreto del servidor, suficiente para reconocerlo en la siguiente
    solicitud. El `seudonimo` es aleatorio (no deriva de su identidad) y es lo único
    que se publica en Soroban.
    """
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    canal = Column(String(20), nullable=False)
    id_externo_hmac = Column(String(64), nullable=False, unique=True)
    seudonimo = Column(String(64), nullable=False, unique=True)  # hex de 32 bytes
    creado_en = Column(String, default=_ahora_iso, nullable=False)

    registros = relationship("RegistroPersonal", back_populates="usuario")


class TransaccionSoroban(Base):
    """
    Referencia a cada intento de transacción en Soroban (genérica: la usarán también
    los libros de socios). Un registro puede tener varios intentos.
    """
    __tablename__ = "transacciones_soroban"
    __table_args__ = (
        CheckConstraint("estado IN ('preparada','enviada','exito','fallida','desconocido')", name="ck_tx_estado"),
    )

    id = Column(Integer, primary_key=True)
    contrato = Column(String(20), nullable=False)       # "libro_personal"
    operacion = Column(String(30), nullable=False)      # "registrar"
    estado = Column(String(12), nullable=False, default="preparada")
    tx_hash = Column(String(64), nullable=True, index=True)
    ledger = Column(Integer, nullable=True)
    codigo_error = Column(String(60), nullable=True)    # código técnico, sin datos personales
    creado_en = Column(String, default=_ahora_iso, nullable=False)
    actualizado_en = Column(String, default=_ahora_iso, onupdate=_ahora_iso, nullable=False)


class RegistroPersonal(Base):
    """
    Movimiento financiero personal. Todo el contenido financiero (tipo, monto, moneda,
    categoría, concepto, fecha y nonce) va dentro de `contenido_cifrado` (AES-256-GCM).
    En claro solo quedan datos técnicos: dueño, hash de integridad, estado e idempotencia.
    """
    __tablename__ = "registros_personales"
    __table_args__ = (
        UniqueConstraint("usuario_id", "clave_idempotencia", name="uq_registro_idempotencia"),
        CheckConstraint(
            "estado_sincronizacion IN ('pendiente','enviado','confirmado','error_reintentable','error')",
            name="ck_registro_estado",
        ),
    )

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    clave_idempotencia = Column(String(128), nullable=False)
    # HMAC de la solicitud: detecta reutilizar la misma clave con otro contenido.
    huella_solicitud = Column(String(64), nullable=False)
    contenido_cifrado = Column(LargeBinary, nullable=False)  # nonce(12) || ciphertext+tag
    version_clave = Column(Integer, nullable=False)
    hash_integridad = Column(String(64), nullable=False, unique=True)  # hex SHA-256
    estado_sincronizacion = Column(String(20), nullable=False, default="pendiente")
    transaccion_soroban_id = Column(Integer, ForeignKey("transacciones_soroban.id"), nullable=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)
    actualizado_en = Column(String, default=_ahora_iso, onupdate=_ahora_iso, nullable=False)

    usuario = relationship("Usuario", back_populates="registros")
    transaccion_soroban = relationship("TransaccionSoroban")


class HistorialOperacion(Base):
    """Bitácora de auditoría. `detalle` nunca contiene datos financieros ni secretos."""
    __tablename__ = "historial_operaciones"
    __table_args__ = (Index("ix_historial_entidad", "entidad", "entidad_id"),)

    id = Column(Integer, primary_key=True)
    entidad = Column(String(30), nullable=False)
    entidad_id = Column(Integer, nullable=True)
    accion = Column(String(40), nullable=False)
    resultado = Column(String(20), nullable=False)
    detalle = Column(String(200), nullable=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)


# -------------------------------------------------------------------------------------
# Libro de SOCIOS (transparente). Fase 1 solo define la estructura local (espejo).
# La autoridad será un contrato Soroban separado (Tesoreria) en una fase posterior:
# NO hay endpoints ni lógica on-chain de socios todavía. Ver docs/ARQUITECTURA_SOCIOS.md.
# -------------------------------------------------------------------------------------

class Socio(Base):
    __tablename__ = "socios"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    public_key = Column(String(56), nullable=False, unique=True)  # G... (nunca la secreta)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)


class Propuesta(Base):
    """
    Propuesta sujeta a votación (D7). `socios_habilitados` y `votos_requeridos` son una
    foto tomada al crearla: cambios posteriores del padrón o de la modalidad no
    alteran el umbral de propuestas ya abiertas (sin efectos retroactivos).
    """
    __tablename__ = "propuestas"
    __table_args__ = (
        CheckConstraint("tipo IN ('compra','cambiar_modalidad','agregar_socio')", name="ck_propuesta_tipo"),
        CheckConstraint("modalidad IN ('unanimidad','mayoria_simple')", name="ck_propuesta_modalidad"),
        CheckConstraint(
            "estado IN ('abierta','aprobada','rechazada','ejecutada','cancelada','expirada')",
            name="ck_propuesta_estado",
        ),
        CheckConstraint("votos_requeridos >= 1 AND votos_requeridos <= socios_habilitados", name="ck_propuesta_umbral"),
    )

    id = Column(Integer, primary_key=True)
    tipo = Column(String(20), nullable=False)
    proponente_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    datos_publicos = Column(Text, nullable=False)  # JSON con los datos acordados en claro
    modalidad = Column(String(20), nullable=False)
    socios_habilitados = Column(Integer, nullable=False)
    votos_requeridos = Column(Integer, nullable=False)
    estado = Column(String(12), nullable=False, default="abierta")
    transaccion_soroban_id = Column(Integer, ForeignKey("transacciones_soroban.id"), nullable=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)


class Voto(Base):
    __tablename__ = "votos"
    __table_args__ = (UniqueConstraint("propuesta_id", "socio_id", name="uq_voto_unico"),)

    id = Column(Integer, primary_key=True)
    propuesta_id = Column(Integer, ForeignKey("propuestas.id"), nullable=False, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    a_favor = Column(Boolean, nullable=False)
    creado_en = Column(String, default=_ahora_iso, nullable=False)
