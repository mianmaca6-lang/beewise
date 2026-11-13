from sqlalchemy.orm import Session
from sqlalchemy import or_
from ..models import RegistroArchivo

class AuditoriaExcepcion(Exception):
    """Excepción personalizada para rechazar documentos dudosos"""
    pass

def auditar_documento_entrante(db: Session, nombre_archivo: str, texto_extraido: str, monto: float):
    """
    Filtros de seguridad para evitar datos corruptos o cobros duplicados
    antes de que entren al flujo de clasificación.
    """
    
    # 1. Filtro Anti-Duplicados Exactos (Por nombre de archivo)
    archivo_existente = db.query(RegistroArchivo).filter(
        RegistroArchivo.nombre_archivo == nombre_archivo
    ).first()
    
    if archivo_existente:
        raise AuditoriaExcepcion(f"El archivo '{nombre_archivo}' ya fue procesado anteriormente (ID: {archivo_existente.id}).")

    # 2. Filtro de Montos Incoherentes
    if monto < 0:
        raise AuditoriaExcepcion("No se admiten facturas con montos totales negativos.")
    
    if monto > 50000000: # Límite de seguridad de 50 millones (ejemplo)
        # Aquí podrías detonar una alerta especial por Telegram usando HTTPX
        raise AuditoriaExcepcion("El monto excede el límite de auditoría automática. Requiere aprobación gerencial.")

    # 3. Filtro de Contenido Vacío (Falla en el OCR/Whisper)
    if not texto_extraido or len(texto_extraido.strip()) < 10:
        raise AuditoriaExcepcion("El texto extraído es demasiado corto o nulo. Posible falla de lectura (OCR/Audio).")

    # Si pasa todas las pruebas, retorna True
    return True