from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import geopandas as gpd
import pandas as pd

# Formato de los datos en las fuentes
AREA_CRS = "EPSG:6933"

def read_vector(path: Path) -> gpd.GeoDataFrame:
    """Read GeoParquet or another vector format."""
    suffixes = "".join(path.suffixes).lower()

    if suffixes.endswith((".parquet", ".geoparquet")):
        return gpd.read_parquet(path)

    try:
        return gpd.read_file(path, engine="pyogrio", use_arrow=True)
    except (ImportError, TypeError):
        return gpd.read_file(path)

def safe_number(value: Any) -> float | int | None:
    if pd.isna(value):
        return None
    return float(value)

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

def make_markdown(summaries: list[dict[str, Any]]) -> str:
    lines = [
        "# Exploratory Data Analysis (EDA) – Building Footprints",
        "",
        "## Objetivo",
        "",
        "Describir y comparar las características geométricas y alfanuméricas "
        "de los conjuntos de huellas de edificaciones seleccionados: Google "
        "Open Buildings y GlobalBuildingAtlas (TUM).",
        "",
        "## Metodología",
        "",
        "- Se contabilizan las entidades (huellas) de cada archivo.",
        "- Se identifica el sistema de referencia de coordenadas (CRS) y los "
        "tipos de geometría.",
        "- Se revisan geometrías nulas, vacías e inválidas.",
        "- Se calcula la extensión espacial (bounding box).",
        "- Se calcula el área geométrica en m² usando la proyección de área "
        "equivalente EPSG:6933; no se calcula directamente sobre coordenadas "
        "geográficas en grados.",
        "- Se revisa la completitud y cardinalidad de los atributos.",
        "",
        "## Resultados",
        "",
        "| Métrica | Google Open Buildings | GlobalBuildingAtlas (TUM) |",
        "|---|---:|---:|",
    ]

    by_label = {s["dataset"]: s for s in summaries}
    google = by_label.get("Google Open Buildings", {})
    tum = by_label.get("GlobalBuildingAtlas (TUM)", {})

    metrics = [
        ("Número de huellas", "feature_count"),
        ("CRS", "crs"),
        ("Tipos de geometría", "geometry_types"),
        ("Geometrías nulas", "null_geometries"),
        ("Geometrías vacías", "empty_geometries"),
        ("Geometrías inválidas", "invalid_geometries"),
        ("Geometrías válidas (%)", "valid_geometry_pct"),
        ("Número de atributos", "attribute_count"),
        ("Área mínima (m²)", "area_min_m2"),
        ("Área mediana (m²)", "area_median_m2"),
        ("Área promedio (m²)", "area_mean_m2"),
        ("Área máxima (m²)", "area_max_m2"),
    ]

    def display(value: Any) -> str:
        if value is None or value == {}:
            return "No disponible"
        if isinstance(value, dict):
            return ", ".join(f"{k}: {v}" for k, v in value.items())
        if isinstance(value, float):
            return f"{value:,.2f}"
        return str(value)

    for title, key in metrics:
        lines.append(
            f"| {title} | {display(google.get(key))} | {display(tum.get(key))} |"
        )

    lines += [
        "",
        "## Interpretación",
        "",
        "Completar esta sección después de revisar los resultados: comparar el "
        "volumen de huellas, la calidad geométrica, la distribución de áreas "
        "y la disponibilidad de atributos de cada fuente. No asumir que los "
        "dos conjuntos representan exactamente las mismas edificaciones ni "
        "que sus áreas fueron calculadas con el mismo método.",
        "",
        "## Archivos de resultados",
        "",
        "- `eda_summary.json`: resumen completo por conjunto de datos.",
        "- `eda_fields.csv`: valores faltantes y cantidad de valores distintos "
        "por atributo.",
        "",
        "> Nota: los resultados describen los archivos de entrada utilizados. "
        "Registrar en el informe la versión, fecha de descarga, cobertura "
        "geográfica y cualquier filtro aplicado antes del análisis.",
        "",
    ]
    return "\n".join(lines)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="EDA comparativo de Google Open Buildings y GlobalBuildingAtlas."
    )
    parser.add_argument("--google", required=True, help="Ruta del archivo Google.")
    parser.add_argument("--tum", required=True, help="Ruta del archivo TUM/GBA.")
    parser.add_argument("--output-dir", default="results/eda", help="Carpeta de salida.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = [
        ("Google Open Buildings", Path(args.google)),
        ("GlobalBuildingAtlas (TUM)", Path(args.tum)),
    ]

    summaries = []
    all_field_rows = []

    for label, path in inputs:
        print(f"Analizando {label}: {path}")
        summary, field_rows = analyze_dataset(label, path)
        summaries.append(summary)
        all_field_rows.extend(field_rows)
        print(
            f"  Huellas: {summary['feature_count']:,} | "
            f"CRS: {summary['crs']} | "
            f"Geometrías inválidas: {summary['invalid_geometries']:,}"
        )

    (output_dir / "eda_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pd.DataFrame(all_field_rows).to_csv(
        output_dir / "eda_fields.csv",
        index=False,
        encoding="utf-8-sig",
    )

    (output_dir / "eda_report.md").write_text(
        make_markdown(summaries),
        encoding="utf-8",
    )

    print("\nEDA finalizado.")
    print(f"Resultados guardados en: {output_dir.resolve()}")

if __name__ == "__main__":
    main()
