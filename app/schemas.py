from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Facturas / n8n ---

class FacturaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre_archivo: str
    estado: str
    texto_extraido: Optional[str] = None


class FacturaEnviarN8n(BaseModel):
    registro_id: int
    workflow: str = "clasificar-factura"


class N8nRespuesta(BaseModel):
    status: str
    detalle: Optional[dict] = None


# --- Aprobaciones Stellar ---

class AprobacionCrearIn(BaseModel):
    registro_id: int = Field(ge=1)
    monto: float = Field(default=0, ge=0)


class FirmaIn(BaseModel):
    xdr_firmado: str = Field(min_length=20)


class FirmaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_key: str
    creado_en: str


class AprobacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    registro_id: int
    estado: str
    umbral: int
    firmas_validas: int
    firmantes: list[str]
    tx_hash: str
    xdr_base: str
    expira_en: str
    horizon_hash: Optional[str] = None
    error_detalle: Optional[str] = None
    red: str
