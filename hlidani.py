"""
Hlídání hledání: po každém stažení pošle push notifikaci (ntfy.sh) o nových bytech,
které odpovídají uloženému hledání.

Hledání = otevřené issue v repu s nadpisem "Hlídání: …" a v textu:
    Odkaz: https://<web>/?d=2%2Bkk&c=25000…     (odkaz z webu s nastavenými filtry)
    Téma: byty-xxxxxxxx                          (téma v aplikaci ntfy)
Bere se jen issue od vlastníka repa, nebo issue se štítkem "hlidani" (štítek přidá vlastník).
Co už bylo oznámeno, se pamatuje v hlidani.json (větev "data") – nic nepřijde dvakrát.

Filtry tu musí počítat stejně jako web (web/index.html, funkce duvod a spocitej).
"""
import json
import math
import os
import re
import struct
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STAV = Path(__file__).resolve().parent / "hlidani.json"
CENA_MAX = 80_000            # jako na webu: posuvník úplně vpravo = bez limitu
MAX_HLIDANI = 30
TEMA = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


# ------------------------------------------------------------ filtry webu --
def filtry(odkaz):
    q = {k: v[0] for k, v in parse_qs(urlsplit(odkaz).query, keep_blank_values=True).items()}
    ano = lambda k: q.get(k) == "1"  # noqa: E731
    bod = None
    if q.get("b"):
        la, lo, *_ = q["b"].split(",") + [""]
        try:
            bod = (float(la), float(lo))
        except ValueError:
            pass
    cislo = lambda k, d: float(q[k]) if q.get(k, "").replace(".", "", 1).isdigit() and float(q[k]) else d  # noqa: E731
    return dict(
        disp=[d for d in q.get("d", "").split(",") if d], cena=cislo("c", CENA_MAX), osob=int(cislo("o", 2)),
        plocha=cislo("p", 0), bod=bod, km=cislo("k", 5), q=q.get("q", "").lower(),
        kourim=ano("ko"), zvire=ano("zv"), venku=ano("ve"), vytah=ano("vy"), vybaveny=ano("vb"),
        nevybaveny=ano("nv"), bezrk=ano("rk"), bezprov=ano("pr"), od=q.get("od", ""), znamePopl=ano("zp"),
        kratke=ano("kr"), rezerv=ano("re"), park=ano("pk"),
        doj=q.get("dj") if q.get("dj") in ("auto", "mhd") else "km", min=cislo("mi", 30),
    )


def median(a):
    a = sorted(a)
    return a[len(a) // 2] if a else None


def vzdalenost(bod, b):
    r = math.pi / 180
    la1, la2 = bod[0] * r, b["lat"] * r
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((b["lon"] - bod[1]) * r / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def disp_skup(b):
    d = b.get("dispozice")
    return "?" if not d else "4+" if d[0] in "456789" else d


def odpovida(b, f, popl_m2, popl_med):
    popl, odhad = b.get("popl"), False
    if popl is not None and b.get("popl_os"):
        popl *= f["osob"]
    if popl is None:
        popl = round(b["plocha"] * popl_m2 / 100) * 100 if b.get("plocha") and popl_m2 else popl_med
        odhad = True
    celkem = b["cena"] + (popl or 0)
    d = disp_skup(b)
    if (d not in f["disp"]) if f["disp"] else d == "pokoj":
        return False
    if f["cena"] < CENA_MAX and celkem > f["cena"]:
        return False
    if f["plocha"] and (b.get("plocha") or 0) < f["plocha"]:
        return False
    if f["bod"] and f["doj"] == "km" and b.get("lat") is not None and vzdalenost(f["bod"], b) > f["km"]:
        return False
    if f.get("_min") is not None and b.get("lat") is not None:          # dojezd autem / MHD
        m = f["_min"].get(b["id"])
        if m is None or m > f["min"]:
            return False
    if f["park"] and not (b.get("garaz") or b.get("parkovani") or b.get("zona") == "zdarma"):
        return False
    if f["q"] and f["q"] not in (b.get("adresa") or "").lower():
        return False
    if ((f["venku"] and not b.get("venku")) or (f["vytah"] and b.get("vytah") is not True)
            or (f["vybaveny"] and b.get("vybaveni") not in (True, "částečně"))
            or (f["nevybaveny"] and b.get("vybaveni") is not False)
            or (f["bezrk"] and b.get("rk") is not False) or (f["bezprov"] and b.get("provize") is not False)
            or (f["od"] and b.get("od") and b["od"] > f["od"]) or (f["znamePopl"] and odhad)):
        return False
    if ((not f["rezerv"] and b.get("rezervovano")) or (not f["kratke"] and b.get("kratkodobe"))
            or (f["osob"] >= 3 and b.get("max2")) or (f["kourim"] and b.get("koureni") is False)
            or (f["zvire"] and b.get("zvirata") is False)):
        return False
    b["_celkem"], b["_odhad"] = celkem, odhad
    return True


# ------------------------------------------------------------------ dojezd --
# Stejný výpočet jako web (spocitejDojezd): MHD z web/mhd.bin, auto z izochrony Valhally.
MHD_BIN = Path(__file__).resolve().parent / "web" / "mhd.bin"
S_ZA_M = 0.9


def metry(la1, lo1, la2, lo2):
    return math.hypot((la1 - la2) * 111320, (lo1 - lo2) * 111320 * math.cos(la1 * math.pi / 180))


def mhd_minuty(byty, bod):
    data = MHD_BIN.read_bytes()
    _, n, c, f, _ = struct.unpack_from("<5I", data)
    o = 20
    zast = struct.unpack_from(f"<{2 * n}i", data, o); o += 8 * n
    zac = struct.unpack_from(f"<{n + 1}I", data, o); o += 4 * (n + 1)
    spoje = struct.unpack_from(f"<{6 * c}H", data, o); o += 12 * c
    chuze = struct.unpack_from(f"<{2 * f}H", data, o)
    T, L = 7200, [-math.inf] * n
    lat = [zast[2 * i] / 1e5 for i in range(n)]
    lon = [zast[2 * i + 1] / 1e5 for i in range(n)]

    def chuze_z(i):
        for k in range(zac[i], zac[i + 1]):
            t, v = chuze[2 * k], L[i] - chuze[2 * k + 1]
            if v > L[t]:
                L[t] = v
    for i in range(n):
        d = metry(lat[i], lon[i], *bod)
        if d <= 1500:
            L[i] = T - d * S_ZA_M
    for i in range(n):
        if L[i] > -math.inf:
            chuze_z(i)
    jede = set()
    for i in range(c):
        z, kam, j, odj, pri = spoje[6 * i:6 * i + 5]
        if pri > T:
            continue
        if j in jede or L[kam] >= pri:
            jede.add(j)
            if odj > L[z]:
                L[z] = odj
                chuze_z(z)
    mriz = {}
    for i in range(n):
        mriz.setdefault((zast[2 * i] // 1000, zast[2 * i + 1] // 1500), []).append(i)
    out = {}
    for b in byty:
        if b.get("lat") is None:
            continue
        nej = T - metry(b["lat"], b["lon"], *bod) * S_ZA_M
        bx, by = math.floor(b["lat"] * 100), math.floor(b["lon"] * 1e5 / 1500)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in mriz.get((bx + dx, by + dy), ()):
                    if L[i] > -math.inf:
                        d = metry(b["lat"], b["lon"], lat[i], lon[i])
                        if d <= 1200:
                            nej = max(nej, L[i] - d * S_ZA_M)
        m = round((T - nej) / 60)
        out[b["id"]] = max(m, 1) if m <= 120 else None
    return out


def v_polygonu(lon, lat, geom):
    poly = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]

    def v_kruhu(k):
        v, j = False, len(k) - 1
        for i in range(len(k)):
            if (k[i][1] > lat) != (k[j][1] > lat) and lon < (k[j][0] - k[i][0]) * (lat - k[i][1]) / (k[j][1] - k[i][1]) + k[i][0]:
                v = not v
            j = i
        return v
    return any(v_kruhu(p[0]) and not any(v_kruhu(d) for d in p[1:]) for p in poly)


def auto_minuty(s, byty, bod, mx):
    q = dict(locations=[dict(lat=bod[0], lon=bod[1])], costing="auto", contours=[dict(time=mx)], polygons=True)
    r = s.get("https://valhalla1.openstreetmap.de/isochrone", params={"json": json.dumps(q, separators=(",", ":"))}, timeout=60)
    r.raise_for_status()
    geom = r.json()["features"][0]["geometry"]
    return {b["id"]: (mx if v_polygonu(b["lon"], b["lat"], geom) else None) for b in byty if b.get("lat") is not None}


# ------------------------------------------------------------------ GitHub --
def hledani(s):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return []
    vlastnik = repo.split("/")[0].lower()
    r = s.get(f"https://api.github.com/repos/{repo}/issues", timeout=30,
              params=dict(state="open", per_page=100),
              headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    out = []
    for i in r.json():
        if "pull_request" in i or not i["title"].lower().startswith("hlídání"):
            continue
        if i["user"]["login"].lower() != vlastnik and "hlidani" not in {l["name"] for l in i["labels"]}:
            continue
        telo = i.get("body") or ""
        odkaz = re.search(r"Odkaz:\s*(\S+)", telo)
        tema = re.search(r"Téma:\s*(\S+)", telo)
        if odkaz and tema and TEMA.match(tema.group(1)):
            out.append(dict(cislo=str(i["number"]), odkaz=odkaz.group(1), tema=tema.group(1)))
    return out[:MAX_HLIDANI]


# -------------------------------------------------------------------- ntfy --
def kc(n):
    return f"{round(n):,}".replace(",", " ") + " Kč"


def radek(b):
    co = ", ".join(x for x in (b.get("dispozice") or "byt", b.get("plocha") and f"{b['plocha']} m²") if x)
    misto = (b.get("adresa") or "").split(",")[-1].strip() or b.get("adresa") or ""
    return f"{co} · {misto} · {'≈ ' if b['_odhad'] else ''}{kc(b['_celkem'])}"


def posli(s, h, nove, uvod=None):
    text = uvod or "\n".join(radek(b) for b in nove[:6]) + (f"\n… a další {len(nove) - 6}" if len(nove) > 6 else "")
    zprava = dict(topic=h["tema"], click=h["odkaz"], tags=["house"],
                  title=uvod and "Hlídání bytů běží" or
                  (f"{len(nove)} nový byt" if len(nove) == 1 else f"{len(nove)} nové byty" if len(nove) < 5
                   else f"{len(nove)} nových bytů"),
                  message=text)
    if not uvod:
        zprava["actions"] = [dict(action="view", label=(b.get("dispozice") or "byt") + " " + kc(b["_celkem"]),
                                  url=b["url"]) for b in nove[:3]]
    s.post("https://ntfy.sh/", data=json.dumps(zprava).encode(), timeout=30,
           headers={"Content-Type": "application/json"}).raise_for_status()


def spust(byty, s, log):
    hl = hledani(s)
    if not hl:
        return
    stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
    znam = [b for b in byty if b.get("popl") is not None and not b.get("popl_os")]
    popl_m2 = median([b["popl"] / b["plocha"] for b in znam if b.get("plocha")])
    popl_med = median([b["popl"] for b in znam])
    aktivni = {b["id"] for b in byty}
    novy_stav = {}
    for h in hl:
        f = filtry(h["odkaz"])
        drive = stav.get(h["cislo"])
        try:
            if f["bod"] and f["doj"] != "km":   # když výpočet selže, hledání se tentokrát přeskočí (výjimka níž)
                f["_min"] = mhd_minuty(byty, f["bod"]) if f["doj"] == "mhd" else auto_minuty(s, byty, f["bod"], int(f["min"]))
            shoda = [b for b in byty if odpovida(b, f, popl_m2, popl_med)]
            if drive is None or drive.get("odkaz") != h["odkaz"] or drive.get("tema") != h["tema"]:
                posli(s, h, shoda, uvod=f"Teď hledání odpovídá {len(shoda)} bytů. "
                                        "Nové ti pošlu, jakmile se objeví.")
                znamo = {b["id"] for b in shoda}
            else:
                znamo = set(drive["ids"]) & aktivni
                nove = sorted((b for b in shoda if b["id"] not in znamo), key=lambda b: b["_celkem"])
                if nove:
                    posli(s, h, nove)
                znamo |= {b["id"] for b in nove}
            novy_stav[h["cislo"]] = dict(odkaz=h["odkaz"], tema=h["tema"], ids=sorted(znamo))
            log(f"Hlídání #{h['cislo']}: {len(shoda)} odpovídá")
        except Exception as ex:  # noqa: BLE001
            novy_stav[h["cislo"]] = drive or {}
            log(f"Hlídání #{h['cislo']}: CHYBA {type(ex).__name__}: {ex}")
    STAV.write_text(json.dumps(novy_stav, ensure_ascii=False), encoding="utf-8")
