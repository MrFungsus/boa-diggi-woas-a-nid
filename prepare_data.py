"""v2: erzeugt data/buildings.pmtiles, lines.geojson, stations.geojson, addresses.json

pip install osmium shapely pyproj pandas
tippecanoe (>= 2.17, schreibt direkt .pmtiles) muss im PATH sein; unter Windows via WSL.
Eingaben: austria-latest.osm.pbf, landesgrenze_salzburg.geojson (EPSG:4326),
          bev/ADRESSE.csv, bev/STRASSE.csv, bev/ORTSCHAFT.csv
Die BEV-Spaltennamen (SKZ, OKZ, HAUSNRTEXT, RW, HW, STRASSENNAME, ORTSNAME) bitte gegen deine Dateien prüfen.
"""
import json, os, shutil, subprocess
import osmium, pandas as pd
from shapely import wkb as swkb
from shapely.geometry import shape, mapping, Point
from shapely.ops import transform
from shapely.prepared import prep
from shapely.strtree import STRtree
from pyproj import Transformer

PBF, GRENZE, BEV = "austria-latest.osm.pbf", "landesgrenze_salzburg.geojson", "bev"
os.makedirs("data", exist_ok=True)
g = json.load(open(GRENZE))
inside = prep(shape(g["features"][0]["geometry"] if g.get("features") else g))
wkbf = osmium.geom.WKBFactory()
to_m = Transformer.from_crs(4326, 32633, always_xy=True).transform  # UTM 33N, Meter
GEW = ("commercial", "retail", "industrial", "office", "warehouse")

def num(v):
    try: return float(str(v).replace(",", ".").split()[0])
    except Exception: return None

class H(osmium.SimpleHandler):
    def __init__(s): super().__init__(); s.b, s.l, s.s = [], [], []
    def area(s, a):
        t = a.tags
        try: poly = swkb.loads(wkbf.create_multipolygon(a), hex=True)
        except Exception: return
        if not inside.intersects(poly.centroid): return
        if "building" in t:
            pm = transform(to_m, poly)
            xy = list(pm.minimum_rotated_rectangle.exterior.coords)
            e = sorted([Point(xy[0]).distance(Point(xy[1])), Point(xy[1]).distance(Point(xy[2]))])
            lv = num(t.get("building:levels") or t.get("levels"))
            s.b.append({"type": "Feature", "geometry": mapping(poly.simplify(1e-6)), "properties": {
                "i": len(s.b), "a": round(pm.area, 1), "l": round(e[1], 1), "w": round(e[0], 1),
                "g": lv, "h": num(t.get("height")), "t": 0 if t["building"] in GEW else 1}})
        elif t.get("power") == "substation":
            s.s.append({"type": "Feature", "properties": {"name": t.get("name", "")}, "geometry": mapping(poly.centroid)})
    def way(s, w):
        if w.tags.get("power") in ("line", "minor_line", "cable") and not w.is_closed():
            try: ls = swkb.loads(wkbf.create_linestring(w), hex=True)
            except Exception: return
            if inside.intersects(ls):
                s.l.append({"type": "Feature", "properties": {"voltage": w.tags.get("voltage", "")}, "geometry": mapping(ls)})
    def node(s, n):
        if n.tags.get("power") == "substation" and inside.contains(Point(n.location.lon, n.location.lat)):
            s.s.append({"type": "Feature", "properties": {"name": n.tags.get("name", "")},
                        "geometry": {"type": "Point", "coordinates": [n.location.lon, n.location.lat]}})

h = H(); h.apply_file(PBF, locations=True)
fc = lambda f: {"type": "FeatureCollection", "features": f}
for n, f in (("lines", h.l), ("stations", h.s)):
    json.dump(fc(f), open(f"data/{n}.geojson", "w"), separators=(",", ":"))
json.dump(fc(h.b), open("data/buildings.geojson", "w"), separators=(",", ":"))
print("Gebäude", len(h.b), "Leitungen", len(h.l), "Stationen", len(h.s))

if shutil.which("tippecanoe"):
    subprocess.run(["tippecanoe", "-o", "data/buildings.pmtiles", "-l", "buildings", "-Z13", "-z16",
                    "--drop-densest-as-needed", "--no-tile-size-limit", "-f", "data/buildings.geojson"], check=True)
else:
    print("tippecanoe nicht gefunden: buildings.geojson manuell zu PMTiles umwandeln (siehe README-Zeile oben).")

# Adressen + eindeutige Gebäudezuordnung (Punkt liegt in genau einem Grundriss)
rd = lambda f: pd.read_csv(f"{BEV}/{f}", sep=";", dtype=str)
adr, st, ort = rd("ADRESSE.csv"), rd("STRASSE.csv"), rd("ORTSCHAFT.csv")
adr = adr[adr["GKZ"].str.startswith("5")]
adr = adr.merge(st[["SKZ", "STRASSENNAME"]], on="SKZ", how="left").merge(ort[["OKZ", "ORTSNAME"]], on="OKZ", how="left")
lon, lat = Transformer.from_crs(31287, 4326, always_xy=True).transform(adr["RW"].astype(float).values, adr["HW"].astype(float).values)
geoms = [shape(f["geometry"]) for f in h.b]; tree = STRtree(geoms)
out = []
for s_, n_, p_, o_, la, lo in zip(adr["STRASSENNAME"].fillna(""), adr["HAUSNRTEXT"].fillna(""), adr["PLZ"], adr["ORTSNAME"].fillna(""), lat, lon):
    hit = tree.query(Point(lo, la), predicate="within")
    out.append([f"{s_} {n_}".strip(), p_, o_, round(la, 6), round(lo, 6), int(hit[0]) if len(hit) == 1 else -1])
json.dump(out, open("data/addresses.json", "w"), ensure_ascii=False, separators=(",", ":"))
print("Adressen", len(out))
