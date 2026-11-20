"""
Genera las tablas de resultados (CSV) a partir de resultados/resumen_municipal.csv.
No necesita conexion a MongoDB.

"""
import argparse

import pandas as pd

from comun import FUENTES, SALIDA, leer_resumen, tabla_ancha


def guardar(df, nombre, decimales=2):
    df.round(decimales).to_csv(SALIDA / nombre, index=False, encoding="utf-8-sig")
    print(f"  {nombre}: {len(df)} filas")


def consolidar(ancho, columna):
    agg = {"municipios": ("codigo_dane", "count")}
    for f in FUENTES:
        agg[f"edificaciones_{f}"] = (f"n_edificaciones_{f}", "sum")
        agg[f"aptas_{f}"] = (f"n_aptas_{f}", "sum")
        agg[f"area_apta_ha_{f}"] = (f"area_apta_ha_{f}", "sum")
    g = ancho.groupby(columna).agg(**agg).reset_index()
    for f in FUENTES:
        g[f"pct_aptas_{f}"] = 100 * g[f"aptas_{f}"] / g[f"edificaciones_{f}"].where(g[f"edificaciones_{f}"] > 0)
        g[f"pct_area_apta_{f}"] = 100 * g[f"area_apta_ha_{f}"] / g[f"area_apta_ha_{f}"].sum()
    return g.sort_values("area_apta_ha_gba", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-aptas", type=int, default=10, help="corte para la tabla de municipios con pocas aptas")
    args = ap.parse_args()

    ancho = tabla_ancha(leer_resumen())
    print(f"{len(ancho)} municipios. Escribiendo tablas en {SALIDA}")

    ancho["razon_area_apta_gba_google"] = (
        ancho["area_apta_m2_gba"] / ancho["area_apta_m2_google"].where(ancho["area_apta_m2_google"] > 0))
    guardar(ancho.sort_values("area_apta_m2_gba", ascending=False), "tabla_municipios.csv")
    guardar(consolidar(ancho, "departamento_nombre"), "tabla_departamentos.csv")
    guardar(consolidar(ancho, "subregion_pdet"), "tabla_subregiones.csv")

    for f in FUENTES:
        cols = ["codigo_dane", "nombre", "departamento_nombre", f"n_edificaciones_{f}", f"n_aptas_{f}",
                f"area_apta_ha_{f}", f"pct_aptas_{f}"]
        top = ancho.nlargest(20, f"area_apta_m2_{f}")[cols].reset_index(drop=True)
        top.insert(0, "posicion", top.index + 1)
        guardar(top, f"top20_{f}.csv")

    totales = pd.DataFrame([{
        "fuente": f,
        "municipios_con_edificaciones": int((ancho[f"n_edificaciones_{f}"] > 0).sum()),
        "n_edificaciones": ancho[f"n_edificaciones_{f}"].sum(),
        "n_aptas": ancho[f"n_aptas_{f}"].sum(),
        "pct_aptas": 100 * ancho[f"n_aptas_{f}"].sum() / ancho[f"n_edificaciones_{f}"].sum(),
        "area_total_ha": ancho[f"area_total_m2_{f}"].sum() / 10000,
        "area_apta_ha": ancho[f"area_apta_m2_{f}"].sum() / 10000} for f in FUENTES])
    guardar(totales, "totales_por_fuente.csv")

    comp = pd.DataFrame([{
        "razon_total_aptas_gba_google": ancho["n_aptas_gba"].sum() / ancho["n_aptas_google"].sum(),
        "razon_area_apta_gba_google": ancho["area_apta_m2_gba"].sum() / ancho["area_apta_m2_google"].sum(),
        "pearson_n_aptas": ancho["n_aptas_google"].corr(ancho["n_aptas_gba"]),
        "pearson_area_apta": ancho["area_apta_m2_google"].corr(ancho["area_apta_m2_gba"])}])
    guardar(comp, "comparacion_fuentes.csv", 3)

    pocas = ancho[(ancho["n_aptas_google"] < args.min_aptas) & (ancho["n_aptas_gba"] < args.min_aptas)]
    guardar(pocas[["codigo_dane", "nombre", "departamento_nombre", "n_edificaciones_google", "n_aptas_google",
                   "n_edificaciones_gba", "n_aptas_gba"]].sort_values(["departamento_nombre", "nombre"]),
            "municipios_pocas_aptas.csv")

    print("\n" + totales.round(2).to_string(index=False))
    print("\n" + comp.round(3).to_string(index=False))
    sin = {f: int((ancho[f"n_aptas_{f}"] == 0).sum()) for f in FUENTES}
    ambas = int(((ancho["n_aptas_google"] == 0) & (ancho["n_aptas_gba"] == 0)).sum())
    print(f"\nMunicipios con 0 aptas -> Google: {sin['google']} | GBA: {sin['gba']} | ambas: {ambas}")
    print(f"Municipios con menos de {args.min_aptas} aptas en ambas fuentes: {len(pocas)}")


if __name__ == "__main__":
    main()