#!/usr/bin/env python3
"""
Requisitos:
    python -m pip install huggingface_hub shapely

Uso:
    python descargar_gba.py --municipios municipios_pdet.geojson --destino gba --solo-listar
    python descargar_gba.py --municipios municipios_pdet.geojson --destino gba

"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from shapely.geometry import box, shape

REPOS = {"ODbLPolygon": "zhu-xlab/GBA.ODbLPolygon", "Polygon": "zhu-xlab/GBA.LoD1"}


def sufijo_lon(v: int) -> str:
    return f"{'w' if v < 0 else 'e'}{abs(v):03d}"


def sufijo_lat(v: int) -> str:
    return f"{'n' if v >= 0 else 's'}{abs(v):02d}"


def nombre_tesela(lon_min: int, lat_min: int) -> str:
    return f"{sufijo_lon(lon_min)}_{sufijo_lat(lat_min + 5)}_{sufijo_lon(lon_min + 5)}_{sufijo_lat(lat_min)}"


def teselas_necesarias(ruta_municipios: Path) -> list[str]:
    data = json.loads(ruta_municipios.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in data["features"] if f.get("geometry")]
    if not geoms:
        raise SystemExit("El GeoJSON de municipios no tiene geometrías.")
    minx = min(g.bounds[0] for g in geoms)
    miny = min(g.bounds[1] for g in geoms)
    maxx = max(g.bounds[2] for g in geoms)
    maxy = max(g.bounds[3] for g in geoms)
    if max(abs(minx), abs(maxx)) > 180 or max(abs(miny), abs(maxy)) > 90:
        raise SystemExit("Las coordenadas de los municipios no están en grados (EPSG:4326).")
    teselas = []
    for lon in range(math.floor(minx / 5) * 5, math.ceil(maxx / 5) * 5, 5):
        for lat in range(math.floor(miny / 5) * 5, math.ceil(maxy / 5) * 5, 5):
            celda = box(lon, lat, lon + 5, lat + 5)
            if any(celda.intersects(g) for g in geoms):
                teselas.append(nombre_tesela(lon, lat))
    return teselas


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga las teselas de GBA que tocan los municipios PDET.")
    ap.add_argument("--municipios", required=True, type=Path, help="GeoJSON de municipios PDET (EPSG:4326).")
    ap.add_argument("--destino", default="gba", type=Path)
    ap.add_argument("--solo-listar", action="store_true", help="Solo muestra las teselas necesarias.")
    a = ap.parse_args()

    teselas = teselas_necesarias(a.municipios)
    print(f"Teselas de 5x5 grados que tocan los municipios PDET: {len(teselas)}")
    for t in teselas:
        print(f"  {t}")
    if a.solo_listar:
        return 0

    from huggingface_hub import snapshot_download  # import tardío: --solo-listar no lo necesita

    patrones = [f"*{t}*.geojson" for t in teselas]
    for parte, repo in REPOS.items():
        destino = a.destino / parte
        print(f"\nDescargando {parte} desde {repo} -> {destino}")
        raiz = Path(snapshot_download(repo_id=repo, repo_type="dataset", allow_patterns=patrones, local_dir=destino))
        bajados = [p.name for p in raiz.rglob("*.geojson")]
        faltan = [t for t in teselas if not any(t in nombre for nombre in bajados)]
        print(f"  Descargadas: {len(bajados)} de {len(teselas)} teselas")
        if faltan:
            print(f"  Sin archivo en {parte}: {', '.join(faltan)}")
            print("  (puede ser normal si esa parte no tiene edificaciones ahí; confirme en la pestaña Files del repositorio)")
    print(f"\nListo. Cargue con: python cargar_gba.py --input-dir {a.destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())