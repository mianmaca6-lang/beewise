from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
import os
import re
from datetime import datetime

from ..database import SessionLocal
from ..database import get_db
from ..models import RegistroArchivo, ReglaClasificacion, Cliente, Tester, LogTelemetria, Transaccion

router = APIRouter(tags=["Vistas HTML"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUTA_TEMPLATES = os.path.join(BASE_DIR, "templates")

templates = Jinja2Templates(directory=RUTA_TEMPLATES)


@router.get("/")
def redireccion_raiz():
    """
    Si alguien entra a la raíz, lo redirige automáticamente a la experiencia Zine.
    Protege el dashboard de accesos curiosos.
    """
    return RedirectResponse(url="/zine")


@router.get("/admin-secreto-tmc")
def panel_principal(request: Request, db: Session = Depends(get_db)):
    """
    Dashboard administrativo oculto. Solo tú conoces esta ruta.
    """
    total_archivos = db.query(RegistroArchivo).count()
    archivos_pendientes = db.query(RegistroArchivo).filter(RegistroArchivo.estado == "input").count()
    archivos_clasificados = db.query(RegistroArchivo).filter(RegistroArchivo.estado == "check").count()
    total_reglas = db.query(ReglaClasificacion).count()
    ultimos_archivos = db.query(RegistroArchivo).order_by(RegistroArchivo.id.desc()).limit(5).all()

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "total_archivos": total_archivos,
        "pendientes": archivos_pendientes,
        "clasificados": archivos_clasificados,
        "total_reglas": total_reglas,
        "ultimos_archivos": ultimos_archivos
    })


@router.get("/zine")
def vista_zine(request: Request, db: Session = Depends(get_db)):
    """
    Ruta para la experiencia inmersiva del proyecto Zine.
    """
    # Para el MVP conectamos la evolución a las transacciones registradas
    total_transacciones = db.query(Transaccion).count()

    if total_transacciones >= 15:
        nivel_inicial = 3
    elif total_transacciones >= 5:
        nivel_inicial = 2
    else:
        nivel_inicial = 1

    return templates.TemplateResponse(request, "colmena_juego.html", {
        "request": request,
        "nivel_inicial": nivel_inicial
    })

# --- PARSER NLP (FAST INPUT) & DOBLE CONTABILIDAD ---

def clasificar_transaccion_nlp(texto: str, monto: float):
    """
    Analiza el texto ingresado, detecta si es ingreso o gasto, 
    y asigna categorías simples y cuentas contables.
    """
    texto = texto.lower().strip()
    es_ingreso = False

    # Detectar el signo + o palabras clave de ingreso
    if texto.startswith("+"):
        es_ingreso = True
        texto = texto.replace("+", "", 1).strip()
    elif any(palabra in texto for palabra in ["quincena", "nomina", "salario", "pago"]):
        es_ingreso = True

    if es_ingreso:
        tipo = "ingreso"
        cuenta_origen = "Ingresos (Empleador/Cliente)"
        cuenta_destino = "Banco/Bolsillo"
        concepto_final = texto.title() if texto else "Ingreso General"
    else:
        tipo = "gasto"
        cuenta_origen = "Banco/Bolsillo"
        
        # Categorización sencilla
        if any(w in texto for w in ["chocorramo", "comida", "almuerzo", "cena", "mercado", "pan"]):
            cuenta_destino = "Gastos: Comida"
        elif any(w in texto for w in ["gasolina", "transporte", "uber", "bus", "taxi"]):
            cuenta_destino = "Gastos: Transporte"
        elif any(w in texto for w in ["luz", "agua", "internet", "arriendo"]):
            cuenta_destino = "Gastos Fijos: Servicios"
        else:
            cuenta_destino = "Gastos: Varios"
            
        concepto_final = texto.title()

    return tipo, concepto_final, cuenta_origen, cuenta_destino


@router.post("/zine/registrar")
async def registrar_gasto_zine(
    concepto: str = Form(...),
    monto: float = Form(...),
    archivo: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    """
    Recibe el registro manual (Fast Input), procesa el NLP 
    y lo almacena con doble contabilidad.
    """
    try:
        # 1. Pasar por el Parser NLP
        tipo, concepto_limpio, origen, destino = clasificar_transaccion_nlp(concepto, monto)

        # 2. Registrar en la tabla Transaccion (Doble Contabilidad)
        nueva_transaccion = Transaccion(
            concepto=concepto_limpio,
            monto=monto,
            tipo=tipo,
            cuenta_origen=origen,
            cuenta_destino=destino,
            fecha_hora=datetime.now().isoformat()
        )
        db.add(nueva_transaccion)
        db.commit()
        db.refresh(nueva_transaccion)

        print(f"[ZINE MVP] {tipo.upper()} registrado -> {concepto_limpio} (${monto}) | De: {origen} A: {destino}")

        return {
            "status": "success", 
            "mensaje": f"{tipo.title()} de ${monto} guardado en '{destino}'."
        }
    except Exception as e:
        db.rollback()
        print(f"[ZINE ERROR] Error al guardar transacción: {e}")
        return {"status": "error", "mensaje": "No se pudo guardar la transacción."}

# --- VISUALIZACIÓN CHART.JS ---

@router.get("/zine/estadisticas")
def obtener_estadisticas_zine(db: Session = Depends(get_db)):
    """
    Agrupa las transacciones reales de SQLite por cuenta de destino y tipo para Chart.js.
    """
    transacciones = db.query(Transaccion.cuenta_destino, func.sum(Transaccion.monto).label('total'))\
                      .filter(Transaccion.tipo == "gasto")\
                      .group_by(Transaccion.cuenta_destino).all()

    ingresos = db.query(func.sum(Transaccion.monto).label('total'))\
                 .filter(Transaccion.tipo == "ingreso").scalar() or 0

    etiquetas = ["Ingresos"] + [t.cuenta_destino for t in transacciones]
    valores = [ingresos] + [t.total for t in transacciones]
    
    # Asignar colores (Ingresos = Esmeralda, Gastos = Naranjas/Rojos)
    colores = ["rgba(16, 185, 129, 0.8)"] + ["rgba(245, 158, 11, 0.8)"] * len(transacciones)
    bordes = ["rgba(16, 185, 129, 1)"] + ["rgba(245, 158, 11, 1)"] * len(transacciones)

    return {
        "etiquetas": etiquetas,
        "valores": valores,
        "colores": colores,
        "bordes": bordes
    }

# --- SISTEMA DE ACCESO Y TELEMETRÍA ---

class LoginRequest(BaseModel):
    token: str

class TelemetriaRequest(BaseModel):
    token: str
    accion: str
    detalle: str = ""

@router.post("/zine/login")
def login_tester(req: LoginRequest, db: Session = Depends(get_db)):
    tester = db.query(Tester).filter(Tester.token == req.token).first()
    if not tester or not tester.activo:
        return {"status": "error", "mensaje": "Token inválido."}
    
    nuevo_log = LogTelemetria(tester_id=tester.id, accion="login", detalle="Inicio de sesión")
    db.add(nuevo_log)
    db.commit()
    return {"status": "success", "mensaje": "Acceso concedido."}

@router.post("/zine/telemetria")
def registrar_evento(req: TelemetriaRequest, db: Session = Depends(get_db)):
    tester = db.query(Tester).filter(Tester.token == req.token).first()
    if tester:
        nuevo_log = LogTelemetria(tester_id=tester.id, accion=req.accion, detalle=req.detalle)
        db.add(nuevo_log)
        db.commit()
        return {"status": "ok"}
    return {"status": "error"}