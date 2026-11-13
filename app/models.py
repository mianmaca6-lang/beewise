
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    ForeignKey,
    Float,
    Text,
    UniqueConstraint,
    LargeBinary,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime, timezone


def _ahora_iso():
    return datetime.now(timezone.utc).isoformat()


# =====================================================================================
# CLIENTES Y REGLAS DE CLASIFICACIÓN
# =====================================================================================

class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nombre_empresa = Column(String, unique=True, index=True)
    nit = Column(String, unique=True)

    reglas = relationship("ReglaClasificacion", back_populates="cliente")


class ReglaClasificacion(Base):
    """
    Memoria adaptable de clasificación para cada cliente.
    """

    __tablename__ = "reglas_clasificacion"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    palabra_clave = Column(String, index=True)
    cuenta_contable = Column(String)
    aplica_retencion = Column(Boolean, default=False)
    porcentaje_retencion = Column(Float, default=0.0)

    cliente = relationship("Cliente", back_populates="reglas")


# =====================================================================================
# ARCHIVOS Y FACTURAS
# =====================================================================================

class RegistroArchivo(Base):
    """
    Trazabilidad de archivos recibidos y procesados.
    """

    __tablename__ = "registros_archivos"

    id = Column(Integer, primary_key=True, index=True)
    nombre_archivo = Column(String, unique=True)
    estado = Column(String, default="input")
    texto_extraido = Column(String, nullable=True)


# =====================================================================================
# TESTERS Y TELEMETRÍA DE ZINE
# =====================================================================================

class Tester(Base):
    __tablename__ = "testers"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    activo = Column(Boolean, default=True)

    logs = relationship("LogTelemetria", back_populates="tester")


class LogTelemetria(Base):
    __tablename__ = "logs_telemetria"

    id = Column(Integer, primary_key=True, index=True)
    tester_id = Column(Integer, ForeignKey("testers.id"))
    accion = Column(String)
    detalle = Column(String, nullable=True)
    fecha_hora = Column(String, default=_ahora_iso)

    tester = relationship("Tester", back_populates="logs")


# =====================================================================================
# TRANSACCIONES DE ZINE
# =====================================================================================

class Transaccion(Base):
    """
    Motor de doble contabilidad para Zine MVP.
    """

    __tablename__ = "transacciones"

    id = Column(Integer, primary_key=True, index=True)
    concepto = Column(String, index=True)
    monto = Column(Float)
    tipo = Column(String)
    cuenta_origen = Column(String)
    cuenta_destino = Column(String)
    fecha_hora = Column(String, default=_ahora_iso)


# =====================================================================================
# MULTIFIRMA CLÁSICA DE FACTURAS
# =====================================================================================

class Aprobacion(Base):
    """
    Estructura de aprobación multifirma clásica.
    Solo guarda XDR y firmas públicas; nunca llaves secretas.
    """

    __tablename__ = "aprobaciones"

    id = Column(Integer, primary_key=True, index=True)
    registro_id = Column(
        Integer,
        ForeignKey("registros_archivos.id"),
        unique=True,
        nullable=False,
    )
    factura_hash = Column(String(64), nullable=False)
    monto = Column(Float, default=0.0)
    tx_hash = Column(String, unique=True, nullable=False)
    xdr_base = Column(Text, nullable=False)
    cuenta_origen = Column(String, nullable=False)
    red = Column(String, nullable=False)
    estado = Column(String, default="pendiente")
    umbral = Column(Integer, default=2)
    error_detalle = Column(String, nullable=True)
    expira_en = Column(String, nullable=False)
    horizon_hash = Column(String, nullable=True)
    creado_en = Column(String, default=_ahora_iso)
    actualizado_en = Column(
        String,
        default=_ahora_iso,
        onupdate=_ahora_iso,
    )

    firmas = relationship(
        "FirmaAprobacion",
        back_populates="aprobacion",
        cascade="all, delete-orphan",
    )


class FirmaAprobacion(Base):
    __tablename__ = "firmas_aprobacion"

    __table_args__ = (
        UniqueConstraint(
            "aprobacion_id",
            "public_key",
            name="uq_firma_aprobacion_socio",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    aprobacion_id = Column(
        Integer,
        ForeignKey("aprobaciones.id"),
        nullable=False,
    )
    public_key = Column(String, nullable=False)
    firma_b64 = Column(String, nullable=False)
    creado_en = Column(String, default=_ahora_iso)

    aprobacion = relationship("Aprobacion", back_populates="firmas")


# =====================================================================================
# LIBRO PERSONAL PRIVADO
# =====================================================================================

ESTADOS_SINCRONIZACION = (
    "pendiente",
    "enviado",
    "confirmado",
    "error_reintentable",
    "error",
)


class Usuario(Base):
    """
    Usuario común que llega por Telegram u otro canal.

    No se almacena su identificador externo en claro.
    Se utiliza un HMAC para reconocerlo y un seudónimo aleatorio
    para las referencias públicas.
    """

    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    canal = Column(String(20), nullable=False)
    id_externo_hmac = Column(String(64), nullable=False, unique=True)
    seudonimo = Column(String(64), nullable=False, unique=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)

    registros = relationship(
        "RegistroPersonal",
        back_populates="usuario",
    )


class TransaccionSoroban(Base):
    """
    Referencia a cada intento de transacción en Soroban.
    """

    __tablename__ = "transacciones_soroban"

    __table_args__ = (
        CheckConstraint(
            "estado IN ('preparada','enviada','exito','fallida','desconocido')",
            name="ck_tx_estado",
        ),
    )

    id = Column(Integer, primary_key=True)
    contrato = Column(String(20), nullable=False)
    operacion = Column(String(30), nullable=False)
    estado = Column(String(12), nullable=False, default="preparada")
    tx_hash = Column(String(64), nullable=True, index=True)
    ledger = Column(Integer, nullable=True)
    codigo_error = Column(String(60), nullable=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)
    actualizado_en = Column(
        String,
        default=_ahora_iso,
        onupdate=_ahora_iso,
        nullable=False,
    )


class RegistroPersonal(Base):
    """
    Movimiento financiero personal cifrado.

    Los datos financieros se guardan dentro de contenido_cifrado.
    """

    __tablename__ = "registros_personales"

    __table_args__ = (
        UniqueConstraint(
            "usuario_id",
            "clave_idempotencia",
            name="uq_registro_idempotencia",
        ),
        CheckConstraint(
            "estado_sincronizacion IN "
            "('pendiente','enviado','confirmado','error_reintentable','error')",
            name="ck_registro_estado",
        ),
    )

    id = Column(Integer, primary_key=True)
    usuario_id = Column(
        Integer,
        ForeignKey("usuarios.id"),
        nullable=False,
        index=True,
    )
    clave_idempotencia = Column(String(128), nullable=False)
    huella_solicitud = Column(String(64), nullable=False)
    contenido_cifrado = Column(LargeBinary, nullable=False)
    version_clave = Column(Integer, nullable=False)
    hash_integridad = Column(String(64), nullable=False, unique=True)
    estado_sincronizacion = Column(
        String(20),
        nullable=False,
        default="pendiente",
    )
    transaccion_soroban_id = Column(
        Integer,
        ForeignKey("transacciones_soroban.id"),
        nullable=True,
    )
    creado_en = Column(String, default=_ahora_iso, nullable=False)
    actualizado_en = Column(
        String,
        default=_ahora_iso,
        onupdate=_ahora_iso,
        nullable=False,
    )

    usuario = relationship(
        "Usuario",
        back_populates="registros",
    )
    transaccion_soroban = relationship("TransaccionSoroban")


class HistorialOperacion(Base):
    """
    Bitácora técnica. Nunca debe contener datos financieros ni secretos.
    """

    __tablename__ = "historial_operaciones"

    __table_args__ = (
        Index("ix_historial_entidad", "entidad", "entidad_id"),
    )

    id = Column(Integer, primary_key=True)
    entidad = Column(String(30), nullable=False)
    entidad_id = Column(Integer, nullable=True)
    accion = Column(String(40), nullable=False)
    resultado = Column(String(20), nullable=False)
    detalle = Column(String(200), nullable=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)


# =====================================================================================
# GOBERNANZA Y LIBRO DE SOCIOS
# =====================================================================================

class Socio(Base):
    """
    Identidad de un socio de Colmena.

    public_key es la clave pública Stellar (G...).
    Nunca se guarda la clave secreta de la wallet aquí.
    """

    __tablename__ = "socios"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    public_key = Column(String(56), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(String, default=_ahora_iso, nullable=False)

    credenciales = relationship(
        "CredencialSocio",
        back_populates="socio",
        cascade="all, delete-orphan",
    )

    propuestas_presentadas = relationship(
        "Propuesta",
        back_populates="proponente",
        foreign_keys="Propuesta.proponente_id",
    )

    participaciones = relationship(
        "PropuestaSocioElegible",
        back_populates="socio",
    )

    votos = relationship(
        "Voto",
        back_populates="socio",
    )


class CredencialSocio(Base):
    """
    Credencial individual para acceder a los servicios privados de Colmena.

    IMPORTANTE:
    - token_hash contiene el SHA-256 del token, nunca el token original.
    - El token original se muestra una sola vez al crearlo.
    - La clave privada Stellar NO se almacena aquí.
    - Revocar una credencial no elimina al socio ni su wallet.
    """

    __tablename__ = "credenciales_socios"

    __table_args__ = (
        Index("ix_credencial_socio_activa", "socio_id", "activo"),
    )

    id = Column(Integer, primary_key=True)
    socio_id = Column(
        Integer,
        ForeignKey("socios.id"),
        nullable=False,
        index=True,
    )

    # SHA-256 hexadecimal del token aleatorio.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)

    nombre = Column(String(80), nullable=False, default="Token principal")
    activo = Column(Boolean, nullable=False, default=True)

    creado_en = Column(String, default=_ahora_iso, nullable=False)
    expira_en = Column(String, nullable=True)
    revocado_en = Column(String, nullable=True)
    ultimo_uso_en = Column(String, nullable=True)

    socio = relationship(
        "Socio",
        back_populates="credenciales",
    )


class Propuesta(Base):
    """
    Propuesta sometida a votación.

    socios_habilitados y votos_requeridos son una fotografía del
    padrón y del quórum al crear la propuesta.

    Las incorporaciones posteriores no alteran propuestas abiertas.
    """

    __tablename__ = "propuestas"

    __table_args__ = (
        CheckConstraint(
            "tipo IN ('compra','cambiar_modalidad','agregar_socio')",
            name="ck_propuesta_tipo",
        ),
        CheckConstraint(
            "modalidad IN ('unanimidad','mayoria_simple')",
            name="ck_propuesta_modalidad",
        ),
        CheckConstraint(
            "estado IN "
            "('abierta','aprobada','rechazada','ejecutada','cancelada','expirada')",
            name="ck_propuesta_estado",
        ),
        CheckConstraint(
            "votos_requeridos >= 1 AND votos_requeridos <= socios_habilitados",
            name="ck_propuesta_umbral",
        ),
    )

    id = Column(Integer, primary_key=True)

    tipo = Column(String(20), nullable=False)

    proponente_id = Column(
        Integer,
        ForeignKey("socios.id"),
        nullable=False,
    )

    # JSON con los datos públicos de la propuesta.
    datos_publicos = Column(Text, nullable=False)

    modalidad = Column(String(20), nullable=False)

    # Fotografía del padrón y del quórum.
    socios_habilitados = Column(Integer, nullable=False)
    votos_requeridos = Column(Integer, nullable=False)

    estado = Column(String(12), nullable=False, default="abierta")

    transaccion_soroban_id = Column(
        Integer,
        ForeignKey("transacciones_soroban.id"),
        nullable=True,
    )

    creado_en = Column(String, default=_ahora_iso, nullable=False)

    proponente = relationship(
        "Socio",
        back_populates="propuestas_presentadas",
        foreign_keys=[proponente_id],
    )

    participantes = relationship(
        "PropuestaSocioElegible",
        back_populates="propuesta",
        cascade="all, delete-orphan",
    )

    votos = relationship(
        "Voto",
        back_populates="propuesta",
        cascade="all, delete-orphan",
    )

    transaccion_soroban = relationship("TransaccionSoroban")


class PropuestaSocioElegible(Base):
    """
    Fotografía de los socios autorizados para votar en una propuesta.

    Se crea una fila por cada socio habilitado al abrir la propuesta.
    Así, un socio agregado posteriormente no puede votar en propuestas
    anteriores.

    public_key_snapshot conserva la clave pública asociada a esa
    participación en el momento de crear la propuesta.
    """

    __tablename__ = "propuesta_socios_elegibles"

    __table_args__ = (
        UniqueConstraint(
            "propuesta_id",
            "socio_id",
            name="uq_propuesta_socio_elegible",
        ),
    )

    id = Column(Integer, primary_key=True)

    propuesta_id = Column(
        Integer,
        ForeignKey("propuestas.id"),
        nullable=False,
        index=True,
    )

    socio_id = Column(
        Integer,
        ForeignKey("socios.id"),
        nullable=False,
        index=True,
    )

    public_key_snapshot = Column(String(56), nullable=False)
    creado_en = Column(String, default=_ahora_iso, nullable=False)

    propuesta = relationship(
        "Propuesta",
        back_populates="participantes",
    )

    socio = relationship(
        "Socio",
        back_populates="participaciones",
    )


class Voto(Base):
    """
    Voto de un socio en una propuesta.
    Un socio solo puede votar una vez por propuesta.
    """

    __tablename__ = "votos"

    __table_args__ = (
        UniqueConstraint(
            "propuesta_id",
            "socio_id",
            name="uq_voto_unico",
        ),
    )

    id = Column(Integer, primary_key=True)

    propuesta_id = Column(
        Integer,
        ForeignKey("propuestas.id"),
        nullable=False,
        index=True,
    )

    socio_id = Column(
        Integer,
        ForeignKey("socios.id"),
        nullable=False,
    )

    a_favor = Column(Boolean, nullable=False)
    creado_en = Column(String, default=_ahora_iso, nullable=False)

    propuesta = relationship(
        "Propuesta",
        back_populates="votos",
    )

    socio = relationship(
        "Socio",
        back_populates="votos",
    )
