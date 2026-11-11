from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pymongo import ASCENDING, GEOSPHERE, MongoClient
from pymongo.errors import BulkWriteError
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree

COLUMNAS = {"latitude", "longitude", "area_in_meters", "confidence", "geometry", "full_plus_code"}
REQUERIDAS = {"latitude", "longitude", "geometry"}
A_9377 = Transformer.from_crs("EPSG:4326", "EPSG:9377", always_xy=True)


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def numerica(df: pd.DataFrame, nombre: str) -> np.ndarray:
    if nombre not in df.columns:
        return np.full(len(df), np.nan)
    return pd.to_numeric(df[nombre], errors="coerce").to_numpy(dtype=float)


def opcional(x: float):
    return None if np.isnan(x) else float(x)


def cargar_municipios(coleccion) -> tuple[STRtree, list[str]]:
    """Carga los límites PDET en memoria y construye el índice espacial."""
    geoms, codigos = [], []
    for d in coleccion.find({}, {"_id": 0, "codigo_dane": 1, "geometria": 1}):
        if not d.get("geometria"):
            continue
        g = shape(d["geometria"])
        if not g.is_valid:
            g = shapely.make_valid(g)
            print(f"  Aviso: geometría inválida reparada en el municipio {d['codigo_dane']}")
        geoms.append(g)
        codigos.append(str(d["codigo_dane"]))
    if not geoms:
        raise RuntimeError("municipios_pdet está vacía o no tiene geometrías.")
    shapely.prepare(geoms)  # acelera los predicados espaciales
    print(f"Municipios PDET cargados en memoria: {len(geoms)}")
    return STRtree(geoms), codigos