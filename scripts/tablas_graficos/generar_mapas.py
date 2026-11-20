"""
Genera mapas y graficos (PNG) a partir de resultados/resumen_municipal.csv y de las geometrias
de municipios_pdet en MongoDB.

"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PatchCollection
from matplotlib.colors import PowerNorm, TwoSlopeNorm
from matplotlib.patches import PathPatch, Patch
from matplotlib.path import Path
from shapely.geometry import shape

from comun import FUENTES, NOMBRE_FUENTE, SALIDA, conectar, leer_resumen, tabla_ancha

DPI = 200


def cargar_geometrias(db):
    """Devuelve {codigo_dane: geometria shapely simplificada (~500 m) para dibujar}."""
    geoms = {}
    for d in db.municipios_pdet.find({}, {"_id": 0, "codigo_dane": 1, "geometria": 1}):
        geoms[d["codigo_dane"]] = shape(d["geometria"]).simplify(0.005, preserve_topology=True)
    return geoms


def partes(g):
    return [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon"]


def parche(poly):
    verts, codes = [], []
    for anillo in [poly.exterior, *poly.interiors]:
        xy = list(anillo.coords)
        verts += xy
        codes += [Path.MOVETO] + [Path.LINETO] * (len(xy) - 2) + [Path.CLOSEPOLY]
    return PathPatch(Path(verts, codes))


def pintar(ax, geoms, valores, cmap, norm, titulo):
    """Colorea los municipios con valor y deja en gris los que no tienen dato (NaN)."""
    con = valores.notna().to_numpy()
    for seleccion, kw in ((~con, dict(facecolor="#d9d9d9")), (con, dict(cmap=cmap, norm=norm))):
        gs = [g for g, s in zip(geoms, seleccion) if s]
        if not gs:
            continue
        col = PatchCollection([parche(p) for g in gs for p in partes(g)], edgecolor="white", linewidth=0.2, **kw)
        if "cmap" in kw:
            col.set_array(np.repeat(valores.to_numpy()[seleccion], [len(partes(g)) for g in gs]))
        ax.add_collection(col)
    x0, y0, x1, y1 = zip(*[g.bounds for g in geoms])
    ax.set_xlim(min(x0), max(x1)); ax.set_ylim(min(y0), max(y1))
    ax.set_aspect("equal"); ax.set_axis_off(); ax.set_title(titulo, fontsize=11)


def mapa_doble(geoms, columnas, titulos, cmap, norm, etiqueta, archivo, suptitulo, nota):
    fig, axes = plt.subplots(1, 2, figsize=(14, 7.5))
    for ax, col, tit in zip(axes, columnas, titulos):
        pintar(ax, geoms, col, cmap, norm, tit)
    cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, shrink=0.65, pad=0.02)
    cb.set_label(etiqueta)
    axes[1].legend(handles=[Patch(facecolor="#d9d9d9", label="Sin datos")], loc="upper center",
                   bbox_to_anchor=(0.5, 0.0), frameon=False)
    fig.suptitle(suptitulo, fontsize=13)
    fig.text(0.01, 0.01, nota, fontsize=8, color="#555555")
    fig.savefig(SALIDA / archivo, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  {archivo}")


def criterio():
    ruta = SALIDA / "parametros_corrida.json"
    if not ruta.exists():
        return ""
    ejecs = json.loads(ruta.read_text(encoding="utf-8"))["ejecuciones"]
    area = ejecs[0].get("area_min")
    conf = next((e.get("confianza_min") for e in ejecs if e["fuente"] == "google"), None)
    return f"Techo apto: area > {area:g} m2 (ambas fuentes); confianza > {conf:g} (solo Google)."


def main():
    ancho = tabla_ancha(leer_resumen())
    geoms_por_cod = cargar_geometrias(conectar())
    faltan = set(ancho["codigo_dane"]) - set(geoms_por_cod)
    if faltan:
        print(f"Aviso: {len(faltan)} municipios sin geometria en municipios_pdet")
    ancho = ancho[ancho["codigo_dane"].isin(geoms_por_cod)].reset_index(drop=True)
    geoms = [geoms_por_cod[c] for c in ancho["codigo_dane"]]
    nota = criterio()
    print(f"Dibujando {len(ancho)} municipios en {SALIDA}")

    # NaN = sin edificaciones en esa fuente (se dibuja en gris)
    area = {f: ancho[f"area_apta_ha_{f}"].where(ancho[f"n_edificaciones_{f}"] > 0) for f in FUENTES}
    pct = {f: ancho[f"pct_aptas_{f}"] for f in FUENTES}
    vmax = max(area[f].max() for f in FUENTES)
    titulos = [NOMBRE_FUENTE[f] for f in FUENTES]

    mapa_doble(geoms, [area[f] for f in FUENTES], titulos, "YlOrRd", PowerNorm(0.5, vmin=0, vmax=vmax),
               "Area apta (ha)", "mapa_area_apta.png", "Area de techos aptos por municipio PDET", nota)
    mapa_doble(geoms, [pct[f] for f in FUENTES], titulos, "YlGnBu",
               PowerNorm(0.7, vmin=0, vmax=max(pct[f].max() for f in FUENTES)),
               "Edificaciones aptas (%)", "mapa_pct_aptas.png",
               "Porcentaje de edificaciones aptas por municipio PDET", nota)

    # Razon GBA / Google del area apta: un solo mapa, centrado en 1 (valores recortados a 0.5 - 2)
    razon = (ancho["area_apta_m2_gba"] / ancho["area_apta_m2_google"].where(ancho["area_apta_m2_google"] > 0)).clip(0.5, 2)
    fig, ax = plt.subplots(figsize=(8, 7.5))
    norm = TwoSlopeNorm(vcenter=1, vmin=0.5, vmax=2)
    pintar(ax, geoms, razon, "PuOr", norm, "Area apta GBA / Google (1 = coinciden)")
    fig.colorbar(ScalarMappable(norm=norm, cmap="PuOr"), ax=ax, shrink=0.65).set_label("Razon (recortada a 0,5 - 2)")
    ax.legend(handles=[Patch(facecolor="#d9d9d9", label="Sin datos en Google")], loc="upper center",
              bbox_to_anchor=(0.5, 0.0), frameon=False)
    fig.text(0.01, 0.01, nota, fontsize=8, color="#555555")
    fig.savefig(SALIDA / "mapa_razon_gba_google.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print("  mapa_razon_gba_google.png")

    # Barras: 15 municipios con mayor area apta (promedio de ambas fuentes)
    top = ancho.assign(m=ancho[["area_apta_ha_google", "area_apta_ha_gba"]].mean(axis=1)).nlargest(15, "m")[::-1]
    y = np.arange(len(top))
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(y + 0.2, top["area_apta_ha_google"], 0.4, label=NOMBRE_FUENTE["google"], color="#e08a3c")
    ax.barh(y - 0.2, top["area_apta_ha_gba"], 0.4, label=NOMBRE_FUENTE["gba"], color="#3c6fa5")
    ax.set_yticks(y); ax.set_yticklabels(top["nombre"].str.title())
    ax.set_xlabel("Area apta (ha)"); ax.set_title("15 municipios con mayor area de techos aptos")
    ax.legend(frameon=False); ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(SALIDA / "barras_top15.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print("  barras_top15.png")

    # Dispersion Google vs GBA (log-log; solo municipios con area apta en ambas)
    ok = ancho[(ancho["area_apta_ha_google"] > 0) & (ancho["area_apta_ha_gba"] > 0)]
    r = ancho["area_apta_m2_google"].corr(ancho["area_apta_m2_gba"])
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    ax.scatter(ok["area_apta_ha_google"], ok["area_apta_ha_gba"], s=14, alpha=0.7, color="#3c6fa5")
    lim = [min(ok["area_apta_ha_google"].min(), ok["area_apta_ha_gba"].min()),
           max(ok["area_apta_ha_google"].max(), ok["area_apta_ha_gba"].max())]
    ax.plot(lim, lim, "--", color="#888888", label="Igualdad (y = x)")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Area apta Google (ha)"); ax.set_ylabel("Area apta GBA (ha)")
    ax.set_title(f"Area apta por municipio (Pearson = {r:.3f}, {len(ok)} municipios)")
    ax.legend(frameon=False); ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(SALIDA / "dispersion_fuentes.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print("  dispersion_fuentes.png")


if __name__ == "__main__":
    main()