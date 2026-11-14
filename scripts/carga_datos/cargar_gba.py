"""
Requisitos:
    python -m pip install "pymongo>=4.6" numpy "shapely>=2.0" pyproj ijson

Uso (PowerShell). La URI se toma de --mongo-uri o de la variable MONGO_URI, para no
dejar contraseñas escritas en el código:
    $env:MONGO_URI = "mongodb://usuario:clave@host:27017/base?authSource=base"
    python cargar_gba.py --input-dir ".\\gba" --database is109502_db --dry-run --max-chunks 2
    python cargar_gba.py --input-dir ".\\gba" --database is109502_db
    python cargar_gba.py --input-dir ".\\gba" --database is109502_db --crear-indices
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

import ijson
import numpy as np
import shapely
from pymongo import ASCENDING, GEOSPHERE, MongoClient
from pymongo.errors import BulkWriteError
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree

FUENTE = "globalbuildingatlas"
A_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
A_9377 = {
    "EPSG:3857": Transformer.from_crs("EPSG:3857", "EPSG:9377", always_xy=True),
    "EPSG:4326": Transformer.from_crs("EPSG:4326", "EPSG:9377", always_xy=True),
}


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def crs_declarado(ruta: Path) -> str:
    """CRS que el archivo dice tener (encabezado GeoJSON), solo informativo."""
    with open(ruta, "rb") as f:
        cabecera = f.read(4096).decode("utf-8", errors="ignore")
    if "CRS84" in cabecera:
        return "EPSG:4326"
    m = re.search(r"EPSG:+(\d{4,5})", cabecera)
    return f"EPSG:{m.group(1)}" if m else "sin declarar"


def primer_xy(geom: dict) -> tuple[float, float]:
    c = geom["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    return c[0], c[1]


def crs_real(x: float, y: float) -> str:
    """Grados si las coordenadas caben en ±180/±90; en otro caso, metros Web Mercator."""
    return "EPSG:4326" if abs(x) <= 180 and abs(y) <= 90 else "EPSG:3857"


def cargar_municipios(coleccion) -> tuple[STRtree, list[str]]:
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
    shapely.prepare(geoms)
    print(f"Municipios PDET cargados en memoria: {len(geoms)}")
    return STRtree(geoms), codigos


def procesar_bloque(feats: list[dict], crs: str, tree: STRtree, codigos: list[str], lote_id):
    """Devuelve (documentos, n_fuera_pdet, n_rechazados, codigos_asignados)."""
    n = len(feats)
    geoms = np.empty(n, dtype=object)
    ok = np.zeros(n, dtype=bool)
    for i, f in enumerate(feats):
        try:
            geoms[i] = shape(f["geometry"])
            ok[i] = True
        except Exception:
            pass  # geometría nula o mal formada
    rechazados = int((~ok).sum())
    idx = np.flatnonzero(ok)

    # Centroide aproximado en grados, solo para decidir el municipio.
    cent = shapely.centroid(geoms[idx])
    cx, cy = shapely.get_x(cent), shapely.get_y(cent)
    if crs == "EPSG:3857":
        cx, cy = A_4326.transform(cx, cy)
        cx, cy = np.asarray(cx), np.asarray(cy)
    fin = np.isfinite(cx) & np.isfinite(cy) & (np.abs(cx) <= 180) & (np.abs(cy) <= 90)
    rechazados += int((~fin).sum())
    idx, cx, cy = idx[fin], cx[fin], cy[fin]

    pi, ti = tree.query(shapely.points(cx, cy), predicate="intersects")
    if len(pi) == 0:
        return [], len(idx), rechazados, []
    orden = np.lexsort((ti, pi))  # límite compartido: municipio de menor índice
    pi, ti = pi[orden], ti[orden]
    pi, primero = np.unique(pi, return_index=True)
    ti = ti[primero]
    sel = idx[pi]
    fuera = len(idx) - len(sel)

    gs = geoms[sel]
    valida = np.isin(shapely.get_type_id(gs), (3, 6)) & shapely.is_valid(gs)  # Polygon/MultiPolygon
    rechazados += int((~valida).sum())
    sel, ti, gs = sel[valida], ti[valida], gs[valida]
    if len(sel) == 0:
        return [], fuera, rechazados, []

    a_9377 = A_9377[crs]
    areas = shapely.area(shapely.transform(gs, lambda c: np.column_stack(a_9377.transform(c[:, 0], c[:, 1]))))
    if crs == "EPSG:3857":
        gs = shapely.transform(gs, lambda c: np.column_stack(A_4326.transform(c[:, 0], c[:, 1])))
    cent = shapely.centroid(gs)
    cx, cy = shapely.get_x(cent), shapely.get_y(cent)

    docs, asignados = [], []
    for k in range(len(sel)):
        h = (feats[sel[k]].get("properties") or {}).get("height")
        h = float(h) if isinstance(h, (int, float)) and np.isfinite(h) else None
        cod = codigos[ti[k]]
        asignados.append(cod)
        docs.append({
            "codigo_dane": cod,
            "lote_id": lote_id,
            "geometria": mapping(gs[k]),
            "centroide": {"type": "Point", "coordinates": [float(cx[k]), float(cy[k])]},
            "area_m2": float(areas[k]),
            "altura_m": h,
        })
    return docs, fuera, rechazados, asignados


def insertar(coleccion, docs: list[dict]) -> tuple[int, int]:
    if not docs:
        return 0, 0
    try:
        return len(coleccion.insert_many(docs, ordered=False).inserted_ids), 0
    except BulkWriteError as exc:
        ins = int(exc.details.get("nInserted", 0))
        return ins, len(docs) - ins


def cargar_tesela(ruta: Path, nombre: str, db, tree, codigos, tam_bloque, max_bloques, simular, por_municipio) -> bool:
    lotes, edif = db["lotes_carga"], db["edificaciones_gba1"]
    declarado = crs_declarado(ruta)
    leidos = insertados = rechazados = fuera = 0
    crs = None
    lote_id = None
    t0 = time.time()
    try:
        with open(ruta, "rb") as fh:
            it = ijson.items(fh, "features.item", use_float=True)
            primer = list(islice(it, 1))
            if not primer:
                print("  Tesela sin edificaciones.")
            else:
                crs = crs_real(*primer_xy(primer[0]["geometry"]))
                aviso = "" if declarado == crs else "  <-- el CRS declarado NO coincide con el real"
                print(f"  CRS declarado: {declarado} | CRS real (por coordenadas): {crs}{aviso}")
            if not simular:
                lote_id = lotes.insert_one({
                    "fuente": FUENTE, "archivo": nombre, "crs_declarado": declarado, "crs_detectado": crs,
                    "n_leidos": 0, "n_insertados": 0, "n_rechazados": 0, "n_fuera_pdet": 0,
                    "fecha_carga": ahora(), "estado": "en_proceso",
                }).inserted_id

            pendientes = primer
            i = 0
            while True:
                feats = pendientes + list(islice(it, tam_bloque - len(pendientes)))
                pendientes = []
                if not feats:
                    break
                i += 1
                docs, b_fuera, b_rech, asignados = procesar_bloque(feats, crs, tree, codigos, lote_id)
                b_ins, b_fall = (len(docs), 0) if simular else insertar(edif, docs)
                por_municipio.update(asignados if simular else asignados[:b_ins])
                leidos += len(feats)
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
        if not simular and lote_id is not None:
            lotes.update_one({"_id": lote_id}, {"$set": {
                "estado": "interrumpido" if isinstance(exc, KeyboardInterrupt) else "error",
                "mensaje_error": str(exc), "fecha_fin": ahora()}})
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"ERROR en {nombre}: {exc}", file=sys.stderr)
        return False


def crear_indices(db) -> None:
    edif = db["edificaciones_gba1"]
    print("Creando índices (puede tardar)...")
    edif.create_index([("centroide", GEOSPHERE)])
    edif.create_index([("codigo_dane", ASCENDING)])
    edif.create_index([("lote_id", ASCENDING)])
    print("Índices creados: centroide (2dsphere), codigo_dane, lote_id.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga GlobalBuildingAtlas (.geojson) a MongoDB.")
    ap.add_argument("--input-dir", required=True, help="Carpeta con las teselas .geojson (se busca en subcarpetas).")
    ap.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    ap.add_argument("--database", default="is109502_db")
    ap.add_argument("--batch-size", type=int, default=20000, help="Edificaciones por bloque (20000 por defecto).")
    ap.add_argument("--max-chunks", type=int, default=None, help="Procesa solo N bloques por tesela (pruebas).")
    ap.add_argument("--dry-run", action="store_true", help="Procesa y diagnostica sin escribir en MongoDB.")
    ap.add_argument("--crear-indices", action="store_true", help="Crea los índices al terminar la carga.")
    a = ap.parse_args()

    carpeta = Path(a.input_dir)
    archivos = sorted(carpeta.rglob("*.geojson"))
    if not carpeta.is_dir() or not archivos:
        print(f"ERROR: no hay archivos *.geojson en {carpeta}", file=sys.stderr)
        return 1

    cliente = MongoClient(a.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        cliente.admin.command("ping")
        db = cliente[a.database]
        tree, codigos = cargar_municipios(db["municipios_pdet"])
        lotes, edif = db["lotes_carga"], db["edificaciones_gba1"]
        por_municipio: Counter = Counter()

        print(f"Teselas encontradas: {len(archivos)}" + ("  [SIMULACIÓN: no se escribe nada]" if a.dry_run else ""))
        for n, ruta in enumerate(archivos, 1):
            nombre = ruta.relative_to(carpeta).as_posix()
            print(f"\n[{n}/{len(archivos)}] {nombre}")
            if not a.dry_run:
                previo = lotes.find_one({"fuente": FUENTE, "archivo": nombre})
                if previo and previo.get("estado") == "completado":
                    print("  Ya cargada (lote completado): se omite.")
                    continue
                if previo:
                    print(f"  Lote previo en estado '{previo.get('estado')}': se limpia y se reprocesa.")
                    edif.delete_many({"lote_id": previo["_id"]})
                    lotes.delete_one({"_id": previo["_id"]})
            if not cargar_tesela(ruta, nombre, db, tree, codigos, a.batch_size, a.max_chunks, a.dry_run, por_municipio):
                print("Carga detenida. Vuelva a ejecutar el script: retoma desde esta tesela.", file=sys.stderr)
                return 1

        if not a.dry_run:
            print("\n===== RESUMEN (lotes_carga) =====")
            for l in lotes.find({"fuente": FUENTE}).sort("archivo", 1):
                print(f"{l['archivo']}: leídos {l['n_leidos']:,} | insertados {l['n_insertados']:,} | "
                      f"fuera PDET {l.get('n_fuera_pdet', 0):,} | rechazados {l['n_rechazados']:,} | {l.get('estado')}")
            print(f"edificaciones_gba1: {edif.estimated_document_count():,} documentos")
        # En simulación se usa lo contado en esta ejecución; en carga real, lo que hay en la colección.
        cubiertos = set(por_municipio) if a.dry_run else set(edif.distinct("codigo_dane"))
        sin = sorted(set(codigos) - cubiertos)
        print(f"\nMunicipios PDET con edificaciones: {len(set(codigos) & cubiertos)} de {len(set(codigos))}")
        if sin:
            print(f"Municipios SIN ninguna edificación ({len(sin)}): " + ", ".join(sin))
            print("  -> Si esperaba datos allí, revise que estén descargadas todas las teselas y ambos productos de GBA.")
        if not a.dry_run:
            if a.crear_indices:
                crear_indices(db)
            else:
                print("Siguiente paso: cree los índices con --crear-indices (o desde Compass).")
        return 0
    finally:
        cliente.close()


if __name__ == "__main__":
    raise SystemExit(main())