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

def procesar_bloque(df: pd.DataFrame, tree: STRtree, codigos: list[str], lote_id):
    """Devuelve (documentos, n_fuera_pdet, n_rechazados) de un bloque de filas."""
    lat, lon = numerica(df, "latitude"), numerica(df, "longitude")
    coord_ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
    rechazados = int((~coord_ok).sum())
    idx = np.flatnonzero(coord_ok)

    # Municipio de cada centroide; si cae en un límite compartido, el de menor índice.
    pi, ti = tree.query(shapely.points(lon[idx], lat[idx]), predicate="intersects")
    if len(pi) == 0:
        return [], len(idx), rechazados
    orden = np.lexsort((ti, pi))
    pi, ti = pi[orden], ti[orden]
    pi, primero = np.unique(pi, return_index=True)
    ti = ti[primero]
    sel = idx[pi]
    fuera = len(idx) - len(sel)

    # Solo se interpreta y valida el WKT de las huellas que sí están en un municipio PDET.
    wkt = df["geometry"].fillna("").to_numpy(dtype=object)[sel]
    geoms = shapely.from_wkt(wkt, on_invalid="ignore")
    valida = (shapely.get_type_id(geoms) == 3) & shapely.is_valid(geoms)  # 3 = Polygon
    rechazados += int((~valida).sum())
    sel, ti, geoms = sel[valida], ti[valida], geoms[valida]
    if len(sel) == 0:
        return [], fuera, rechazados

    proyectadas = shapely.transform(geoms, lambda c: np.column_stack(A_9377.transform(c[:, 0], c[:, 1])))
    areas = shapely.area(proyectadas)
    area_fuente, confianza = numerica(df, "area_in_meters"), numerica(df, "confidence")
    if "full_plus_code" in df.columns:
        pluscode = df["full_plus_code"].to_numpy(dtype=object)[sel]
    else:
        pluscode = [None] * len(sel)

    docs = []
    for k in range(len(sel)):
        r = sel[k]
        pc = pluscode[k]
        docs.append({
            "codigo_dane": codigos[ti[k]],
            "lote_id": lote_id,
            "geometria": mapping(geoms[k]),
            "centroide": {"type": "Point", "coordinates": [float(lon[r]), float(lat[r])]},
            "area_m2": float(areas[k]),
            "area_fuente_m2": opcional(area_fuente[r]),
            "confianza": opcional(confianza[r]),
            "plus_code": None if pd.isna(pc) else str(pc),
        })
    return docs, fuera, rechazados