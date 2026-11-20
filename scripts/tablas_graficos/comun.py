"""Utilidades compartidas por los scripts de tablas y graficos."""
import os
import sys
from pathlib import Path

import pandas as pd
from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Carpeta de salida: <raiz del repositorio>/resultados (se puede cambiar con RESULTADOS_DIR)
SALIDA = Path(os.environ.get("RESULTADOS_DIR") or Path(__file__).resolve().parents[2] / "resultados")
SALIDA.mkdir(parents=True, exist_ok=True)

FUENTES = ["google", "gba"]
NOMBRE_FUENTE = {"google": "Google Open Buildings", "gba": "GlobalBuildingAtlas"}


def conectar():
    uri = os.environ.get("MONGO_URI")
    if not uri:
        sys.exit("Falta MONGO_URI (en el archivo .env o como variable de entorno).")
    return MongoClient(uri, serverSelectionTimeoutMS=15000)[os.environ.get("MONGO_DB", "is109502_db")]


def elegir_corrida(db, corrida=None):
    """Usa la corrida indicada o, si no, la ultima ejecucion completada."""
    if corrida:
        return corrida
    ultima = db.ejecuciones_analisis.find_one({"estado": "completado"}, sort=[("fecha_ejecucion", -1)])
    if ultima is None:
        sys.exit("No hay ejecuciones completadas en ejecuciones_analisis.")
    return ultima["corrida_id"]


def leer_resumen():
    ruta = SALIDA / "/Users/Administrador/PUJ2630/DBA/Proyecto-Administraci-n-de-Bases-de-Datos/resultados/resumen/resumen_municipal.csv"
    if not ruta.exists():
        sys.exit(f"No existe {ruta}. Ejecute primero exportar_resumen.py")
    return pd.read_csv(ruta, dtype={"codigo_dane": str})


def tabla_ancha(df):
    """Pasa de formato largo (municipio x fuente) a una fila por municipio con columnas <metrica>_<fuente>."""
    if set(df["fuente"]) != set(FUENTES):
        sys.exit(f"La corrida debe tener las dos fuentes {FUENTES}; tiene {sorted(set(df['fuente']))}.")
    valores = ["n_edificaciones", "n_aptas", "area_total_m2", "area_apta_m2"]
    claves = ["codigo_dane", "nombre", "departamento_nombre", "subregion_pdet"]
    ancho = df.groupby(claves + ["fuente"], dropna=False)[valores].sum().unstack("fuente")
    ancho.columns = [f"{m}_{f}" for m, f in ancho.columns]
    ancho = ancho.reset_index()
    for f in FUENTES:
        ancho[f"area_apta_ha_{f}"] = ancho[f"area_apta_m2_{f}"] / 10000
        ancho[f"pct_aptas_{f}"] = (100 * ancho[f"n_aptas_{f}"] / ancho[f"n_edificaciones_{f}"]
                                   .where(ancho[f"n_edificaciones_{f}"] > 0))
    return ancho