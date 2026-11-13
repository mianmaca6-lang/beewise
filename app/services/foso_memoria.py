import unicodedata
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import ReglaClasificacion


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sin_tildes.lower().split())


def aplicar_memoria_adaptable(db: Session, texto_extraido: str, cliente_id: Optional[int] = None):
    """
    Busca en el foso de reglas si el texto contiene la palabra_clave aprendida.
    Retorna la cuenta contable y reglas de retención asociadas.

    - cliente_id=None: se consideran todas las reglas (comportamiento anterior).
    - cliente_id=N: reglas de ese cliente + reglas globales (cliente_id NULL).
    - Si coinciden varias, gana la palabra clave más larga; en empate, la del cliente.
    La comparación se hace en Python y normaliza también las claves ya guardadas.
    """
    texto = normalizar(texto_extraido or "")
    if not texto:
        return {"encontrado": False}

    consulta = db.query(ReglaClasificacion)
    if cliente_id is not None:
        consulta = consulta.filter(
            or_(
                ReglaClasificacion.cliente_id == cliente_id,
                ReglaClasificacion.cliente_id.is_(None),
            )
        )

    mejor = None
    mejor_largo = 0
    mejor_es_propia = False
    for regla in consulta.order_by(ReglaClasificacion.id).all():
        clave = normalizar(regla.palabra_clave or "")
        if not clave or clave not in texto:
            continue
        es_propia = cliente_id is not None and regla.cliente_id == cliente_id
        gana = (
            mejor is None
            or len(clave) > mejor_largo
            or (len(clave) == mejor_largo and es_propia and not mejor_es_propia)
        )
        if gana:
            mejor, mejor_largo, mejor_es_propia = regla, len(clave), es_propia

    if mejor is None:
        return {"encontrado": False}

    return {
        "encontrado": True,
        "palabra_clave": mejor.palabra_clave,
        "cuenta_contable": mejor.cuenta_contable,
        "aplica_retencion": mejor.aplica_retencion,
        "porcentaje_retencion": mejor.porcentaje_retencion,
        "regla_id": mejor.id,
    }


def registrar_nueva_regla(
    db: Session,
    palabra_clave: str,
    cuenta_contable: str,
    aplica_retencion: bool = False,
    porcentaje_retencion: float = 0.0,
    cliente_id: Optional[int] = None,
):
    """
    Enseña al foso una nueva regla contable automatizada (upsert por cliente + clave).
    """
    clave = normalizar(palabra_clave)
    porcentaje = porcentaje_retencion if aplica_retencion else 0.0

    # Reglas del mismo cliente (o globales si cliente_id es None); se compara normalizado
    # en Python porque lower() de SQLite no maneja tildes. Si hay duplicados, gana el id menor.
    candidatas = db.query(ReglaClasificacion)
    if cliente_id is None:
        candidatas = candidatas.filter(ReglaClasificacion.cliente_id.is_(None))
    else:
        candidatas = candidatas.filter(ReglaClasificacion.cliente_id == cliente_id)

    existente = next(
        (r for r in candidatas.order_by(ReglaClasificacion.id).all()
         if normalizar(r.palabra_clave or "") == clave),
        None,
    )

    if existente:
        existente.palabra_clave = clave
        existente.cuenta_contable = cuenta_contable
        existente.aplica_retencion = aplica_retencion
        existente.porcentaje_retencion = porcentaje
        db.commit()
        return {"status": "actualizado", "id": existente.id}

    nueva_regla = ReglaClasificacion(
        cliente_id=cliente_id,
        palabra_clave=clave,
        cuenta_contable=cuenta_contable,
        aplica_retencion=aplica_retencion,
        porcentaje_retencion=porcentaje,
    )
    db.add(nueva_regla)
    db.commit()
    db.refresh(nueva_regla)

    return {"status": "aprendido", "id": nueva_regla.id}