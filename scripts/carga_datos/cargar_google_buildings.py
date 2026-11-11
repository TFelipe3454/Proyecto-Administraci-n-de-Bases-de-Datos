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


def insertar(coleccion, docs: list[dict]) -> tuple[int, int]:
    """Inserción masiva no ordenada. Devuelve (insertados, fallidos)."""
    if not docs:
        return 0, 0
    try:
        return len(coleccion.insert_many(docs, ordered=False).inserted_ids), 0
    except BulkWriteError as exc:
        insertados = int(exc.details.get("nInserted", 0))
        return insertados, len(docs) - insertados


def cargar_archivo(ruta: Path, db, tree, codigos, tam_bloque: int, max_bloques: int | None, simular: bool) -> bool:
    lotes, edif = db["lotes_carga"], db["edificaciones_google"]
    lote_id = None
    if not simular:
        lote_id = lotes.insert_one({
            "fuente": "google", "archivo": ruta.name,
            "n_leidos": 0, "n_insertados": 0, "n_rechazados": 0, "n_fuera_pdet": 0,
            "fecha_carga": ahora(), "estado": "en_proceso",
        }).inserted_id

    leidos = insertados = rechazados = fuera = 0
    t0 = time.time()
    try:
        lector = pd.read_csv(ruta, compression="gzip", chunksize=tam_bloque,
                             usecols=lambda c: c.strip() in COLUMNAS)
        for i, df in enumerate(lector, 1):
            df.columns = df.columns.str.strip()
            if i == 1 and REQUERIDAS - set(df.columns):
                raise ValueError("Faltan columnas: " + ", ".join(sorted(REQUERIDAS - set(df.columns))))

            docs, b_fuera, b_rech = procesar_bloque(df, tree, codigos, lote_id)
            b_ins, b_fall = (len(docs), 0) if simular else insertar(edif, docs)

            leidos += len(df)
            insertados += b_ins
            fuera += b_fuera
            rechazados += b_rech + b_fall
            if not simular:
                lotes.update_one({"_id": lote_id}, {"$set": {
                    "n_leidos": leidos, "n_insertados": insertados,
                    "n_rechazados": rechazados, "n_fuera_pdet": fuera}})
            print(f"  bloque {i}: leídos {leidos:,} | PDET {insertados:,} | fuera de PDET {fuera:,} "
                  f"| rechazados {rechazados:,} | {time.time() - t0:,.0f} s")
            if max_bloques and i >= max_bloques:
                break
        if leidos != insertados + fuera + rechazados:
            print("  AVISO: los contadores no cuadran (leídos != insertados + fuera + rechazados).")
        if not simular:
            lotes.update_one({"_id": lote_id}, {"$set": {"estado": "completado", "fecha_fin": ahora()}})
        return True
    except BaseException as exc:
        if not simular:
            lotes.update_one({"_id": lote_id}, {"$set": {
                "estado": "interrumpido" if isinstance(exc, KeyboardInterrupt) else "error",
                "mensaje_error": str(exc), "fecha_fin": ahora()}})
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"ERROR en {ruta.name}: {exc}", file=sys.stderr)
        return False

def main() -> int:
    ap = argparse.ArgumentParser(description="Carga Google Open Buildings (.csv.gz) a MongoDB.")
    ap.add_argument("--input-dir", required=True, help="Carpeta con los archivos .csv.gz.")
    ap.add_argument("--mongo-uri", default= "mongodb://is109502:Urz1RTbguW0EsJP@orion.javeriana.edu.co:27017/is109502_db?authSource=is109502_db")
    ap.add_argument("--database", default="is109502_db")
    ap.add_argument("--batch-size", type=int, default=50000, help="Filas por bloque (50000 por defecto).")
    a = ap.parse_args()

    carpeta = Path(a.input_dir)
    archivos = sorted(carpeta.glob("*.csv.gz"))
    if not carpeta.is_dir() or not archivos:
        print(f"ERROR: no hay archivos *.csv.gz en {carpeta}", file=sys.stderr)
        return 1

    cliente = MongoClient(a.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        cliente.admin.command("ping")
        db = cliente[a.database]
        tree, codigos = cargar_municipios(db["municipios_pdet"])
        lotes, edif = db["lotes_carga"], db["edificaciones_google"]

        print(f"Archivos encontrados: {len(archivos)}" + ("  [SIMULACIÓN: no se escribe nada]" if a.dry_run else ""))
        for n, ruta in enumerate(archivos, 1):
            print(f"\n[{n}/{len(archivos)}] {ruta.name}")
            if not a.dry_run:
                previo = lotes.find_one({"fuente": "google", "archivo": ruta.name})
                if previo and previo.get("estado") == "completado":
                    print("  Ya cargado (lote completado): se omite.")
                    continue
                if previo:
                    print(f"  Lote previo en estado '{previo.get('estado')}': se limpia y se reprocesa.")
                    edif.delete_many({"lote_id": previo["_id"]})
                    lotes.delete_one({"_id": previo["_id"]})
            if not cargar_archivo(ruta, db, tree, codigos, a.batch_size, a.max_chunks, a.dry_run):
                print("Carga detenida. Vuelva a ejecutar el script: retoma desde este archivo.", file=sys.stderr)
                return 1

        if not a.dry_run:
            print("\n===== RESUMEN (lotes_carga) =====")
            for l in lotes.find({"fuente": "google"}).sort("archivo", 1):
                print(f"{l['archivo']}: leídos {l['n_leidos']:,} | insertados {l['n_insertados']:,} | "
                      f"fuera PDET {l.get('n_fuera_pdet', 0):,} | rechazados {l['n_rechazados']:,} | {l.get('estado')}")
            print(f"edificaciones_google: {edif.estimated_document_count():,} documentos")
            if a.crear_indices:
                crear_indices(db)
            else:
                print("Siguiente paso: cree los índices con --crear-indices (o desde Compass).")
        return 0
    finally:
        cliente.close()


if __name__ == "__main__":
    raise SystemExit(main())