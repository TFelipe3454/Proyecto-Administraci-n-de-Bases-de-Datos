from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import geopandas as gpd
import pandas as pd

# Formato de los datos en las fuentes
AREA_CRS = "EPSG:6933"


def analyze_dataset(label: str, path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    gdf = read_vector(path)

    if gdf.empty:
        raise ValueError(f"El archivo {path} no contiene registros.")

    if gdf.crs is None:
        raise ValueError(
            f"{label}: el archivo no declara CRS. No se calcularán áreas "
            "hasta identificar su sistema de coordenadas."
        )

    geom = gdf.geometry
    null_count = int(geom.isna().sum())
    empty_count = int(geom.is_empty.sum())
    valid_mask = geom.notna() & ~geom.is_empty
    valid_count = int(geom[valid_mask].is_valid.sum())
    invalid_count = int(valid_mask.sum() - valid_count)

    geometry_types = (
        geom.dropna()
        .geom_type.value_counts()
        .to_dict()
    )

    # Calculo de las  areas en poligonos iguales 
    usable = gdf.loc[valid_mask].copy()
    area_stats: dict[str, Any] = {}
    if not usable.empty:
        projected = usable.to_crs(AREA_CRS)
        areas = projected.geometry.area
        area_stats = {
            "area_crs": AREA_CRS,
            "area_unit": "m²",
            "area_min_m2": safe_number(areas.min()),
            "area_median_m2": safe_number(areas.median()),
            "area_mean_m2": safe_number(areas.mean()),
            "area_max_m2": safe_number(areas.max()),
            "area_p25_m2": safe_number(areas.quantile(0.25)),
            "area_p75_m2": safe_number(areas.quantile(0.75)),
        }

    # Completar los atributos excluyendo la columna de geometria
    field_rows: list[dict[str, Any]] = []
    attribute_columns = [c for c in gdf.columns if c != gdf.geometry.name]
    for column in attribute_columns:
        missing = int(gdf[column].isna().sum())
        field_rows.append({
            "dataset": label,
            "field": str(column),
            "dtype": str(gdf[column].dtype),
            "missing": missing,
            "missing_pct": round(100 * missing / len(gdf), 2),
            "distinct_values": int(gdf[column].nunique(dropna=True)),
        })

    bounds = [float(v) for v in gdf.total_bounds]
    summary = {
        "dataset": label,
        "source_file": str(path),
        "feature_count": int(len(gdf)),
        "crs": str(gdf.crs),
        "geometry_column": str(gdf.geometry.name),
        "geometry_types": {str(k): int(v) for k, v in geometry_types.items()},
        "null_geometries": null_count,
        "empty_geometries": empty_count,
        "invalid_geometries": invalid_count,
        "valid_geometries": valid_count,
        "valid_geometry_pct": round(100 * valid_count / len(gdf), 2),
        "bounds": {
            "min_x": bounds[0],
            "min_y": bounds[1],
            "max_x": bounds[2],
            "max_y": bounds[3],
        },
        "attribute_count": len(attribute_columns),
        "attributes": [str(c) for c in attribute_columns],
        **area_stats,
    }

    return summary, field_rows


if __name__ == "__main__":
    main()
