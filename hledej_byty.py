"""
Hledač bytů: pronájmy bytů v Praze ze Sreality, Bezrealitek a Bazoše -> web/byty.json

Spuštění:  python hledej_byty.py
Každé spuštění stáhne aktuální nabídky, porovná je s databází (byty.db) a zapíše
data pro web (web/index.html čte web/byty.json). Filtrování dělá až web.

Popisy inzerátů se neukládají ani nezveřejňují – z popisu se při stažení jen
vyčtou fakta (poplatky, kouření, zvířata, provize…).
BYTY_LIMIT=N omezí počet inzerátů na zdroj (pro rychlý test).
"""
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------- NASTAVENÍ --
PAUZA_S = 0.7                # pauza mezi požadavky na detail (šetrnost k webům)
DETAIL_V = 2                 # verze rozboru detailu; zvýšení = znovu stáhnout detaily
LIMIT = int(os.environ.get("BYTY_LIMIT") or 0) or None

SLOZKA = Path(__file__).resolve().parent
DB_SOUBOR = SLOZKA / "byty.db"
LOG_SOUBOR = SLOZKA / "log.txt"
WEB_JSON = SLOZKA / "web" / "byty.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG_SOUBOR, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "cs"})
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=None)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def bez_diakritiky(text):
    t = unicodedata.normalize("NFKD", text or "").lower()
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))


def dispozice(text):
    """'Pronájem bytu 2+kk 54 m²' -> '2+kk'. Garsoniéra se počítá jako 1+kk."""
    t = bez_diakritiky(text)
    m = re.search(r"\b([1-9])\s*(?:\+\s*(kk|1)|(kk))\b", t)          # "2+kk", "2 + 1", "3kk"
    if m:
        return f"{m.group(1)}+{m.group(2) or m.group(3)}"
    if re.search(r"garson", t):
        return "1+kk"
    if re.search(r"\bpokoj\b|spolubydl", t):
        return "pokoj"
    return None


# ------------------------------------------------------ ROZBOR TEXTU POPISU --
# Hledá se v textu bez diakritiky. Výsledek je True / False / None (= nezmíněno).
def _je(vzor, t):
    return re.search(vzor, t) is not None


def rozbor(text):
    t = bez_diakritiky(text)
    r = {}
    if _je(r"nekur|nekour|zakaz\w* koureni|koureni\w* (je )?(zakaz|neni povol|neni mozn)"
           r"|nelze kourit|bez koureni|non-?smok", t):
        r["koureni"] = False
    elif _je(r"kuraci? (vitan|povolen|ok)|koureni (je )?(povoleno|mozne)", t):
        r["koureni"] = True
    if _je(r"(max\.?|maximalne|nejvyse|idealne|pouze|jen) ?(pro )?(1\s*[-–]\s*2|2|dve|dva|jednu|1) "
           r"?(osob|lid|najemnik)|pro (jednu|1|1\s*[-–]\s*2) osob|\bpro (par|pary)\b"
           r"|(vhodn\w*|ideal\w*) (pro|i pro) (par|jednotliv)", t):
        r["max2"] = True
    if _je(r"bez (domacich )?(zvirat|mazlick)|zvirata (ne|nejsou)|zakaz zvirat|nechovat zvir", t):
        r["zvirata"] = False
    elif _je(r"(zvirat\w*|mazlic\w*|pejsk\w*|psi) (jsou |je )?(vitan|povolen|mozn|ok|po dohode)"
             r"|pet.?friendly|vhodn\w* (i )?pro (majitel\w* )?(psa|zvir|mazlick)", t):
        r["zvirata"] = True
    if _je(r"bez provize|provize (se )?neplati|zadna provize|0 ?% provize|neplatite provizi", t):
        r["provize"] = False
    elif _je(r"provize", t):
        r["provize"] = True
    if _je(r"kratkodob|short.?term|airbnb", t):
        r["kratkodobe"] = True
    if _je(r"bez vytahu|vytah ne\b|neni vytah", t):
        r["vytah"] = False
    elif _je(r"\bvytah", t):
        r["vytah"] = True
    if _je(r"balkon|balkon|terasa|terasou|lodzi|lodzie", t) and not _je(r"bez (balkonu|terasy|lodzie)", t):
        r["venku"] = True
    return r


CASTKA = re.compile(r"(?<![\d.,/])(\d{1,2}[ .]\d{3}|\d{3,5})(?![\d]|[.,]\d)")
KLIC_SLUZBY = re.compile(r"poplat|sluzb|zaloh|naklady na bydleni|energi|vyuctov")
KLIC_ELEKTRINA = re.compile(r"elektr|\bel\.")
NE_POPLATKY = re.compile(r"kauc|proviz|jistot|dph|garaz|parkov|stani|najemn[eiy]|najmu|najem\b|cena")
JEDNOTKA_NE = re.compile(r"\s*(m\b|m2|m²|km|min|metr|let\b|rok|%|\.? ?patr|mb|gb|kw|ks)")
ZA_OSOBU = re.compile(r"^\W*(kc|czk)?\W*(mes\w*\W*)?(poplat\w* |sluzb\w* )?(/|na |za |kazd\w* )\s*os")
PRO_N_OSOB = re.compile(r"(\b[12] ?os|jedn\w* osob|dve osob|dva lid)")


def _najdi_castku(t, klic, cena, vynech=None):
    """První částka v rozumném rozsahu, která stojí u klíčového slova (za ním, nebo těsně před ním)."""
    for m in CASTKA.finditer(t):
        if vynech is not None and m.start() == vynech:
            continue
        pred, za = t[max(0, m.start() - 40):m.start()], t[m.end():m.end() + 40]
        k = None
        for k in klic.finditer(pred):
            pass                                      # poslední klíčové slovo před částkou
        if k and not NE_POPLATKY.search(pred[k.end():]):
            pass
        elif not klic.search(za[:18]) or NE_POPLATKY.search(pred[-25:]):  # "4 500 Kč služby"
            continue
        kc = int(re.sub(r"\D", "", m.group(1)))
        ma_kc = re.match(r"\s*(,-+)?\s*(kc|czk|,-)", za)
        if (not 300 <= kc <= 15_000 or kc == cena or JEDNOTKA_NE.match(za)
                or (1900 <= kc <= 2100 and not ma_kc)):
            continue
        return m, kc, pred, za
    return None


def odhad_poplatku(cena, *texty):
    """Vyčte poplatky z textu. Vrátí dict popl / popl_os (částka je za osobu) / popl_info."""
    for text in texty:
        t = bez_diakritiky(text)
        nalez = _najdi_castku(t, KLIC_SLUZBY, cena)
        if not nalez:
            continue
        m, kc, pred, za = nalez
        r = dict(popl=kc, popl_os=False, popl_info="vyčteno z popisu")
        if ZA_OSOBU.match(za):
            r.update(popl_os=True, popl_info="vyčteno z popisu, uvedeno za osobu")
        elif PRO_N_OSOB.search(pred[-30:] + za[:30]):
            r["popl_info"] = "vyčteno z popisu, uvedeno pro 1–2 osoby"
        # Elektřina uvedená zvlášť (ne "včetně elektřiny") se přičte.
        el = _najdi_castku(t, KLIC_ELEKTRINA, cena, vynech=m.start())
        if el and not re.search(r"vcet\w* (el|energ)", el[2][-30:]) and el[1] <= 5_000 and not r["popl_os"]:
            r["popl"] += el[1]
            r["popl_info"] += f" + elektřina {el[1]} Kč"
        return r
    return dict(popl=None, popl_info="poplatky neuvedeny")


def ts_datum(unix):
    return datetime.fromtimestamp(unix).strftime("%Y-%m-%d") if unix else None


def ano_ne(nazev):
    return {"ano": True, "ne": False}.get(bez_diakritiky(nazev or "").strip())


def nazev_z(x):
    return (x or {}).get("name") if isinstance(x, dict) else x


# ---------------------------------------------------------------- SREALITY --
def sreality(s, zname):
    api = "https://www.sreality.cz/api/v1/estates"
    vysledky, offset, celkem = [], 0, None
    while celkem is None or offset < celkem:
        r = s.get(f"{api}/search", timeout=30, params=dict(
            category_main_cb=1, category_type_cb=2, locality_region_id=10,
            limit=100, offset=offset, sort="-date"))
        r.raise_for_status()
        d = r.json()
        celkem = d["pagination"]["total"]
        if not d["results"]:
            break
        vysledky += d["results"]
        offset += len(d["results"])
        if LIMIT and len(vysledky) >= LIMIT:
            vysledky = vysledky[:LIMIT]
            break

    out = []
    for e in vysledky:
        loc = e.get("locality") or {}
        hid = str(e["hash_id"])
        disp = nazev_z(e.get("category_sub_cb")) or ""
        metro = e.get("poi_metro_distance")
        b = dict(
            id=f"sreality:{hid}", zdroj="Sreality",
            url=f"https://www.sreality.cz/detail/pronajem/byt/"
                f"{disp.lower() if re.fullmatch(r'\d\+(kk|1)|pokoj', disp.lower()) else 'atypicky'}/praha/{hid}",
            dispozice=dispozice(disp) or dispozice(e.get("advert_name")),
            cena=e.get("price_czk"),
            adresa=", ".join(x for x in (loc.get("street"), loc.get("citypart"), loc.get("district")) if x),
            lat=loc.get("gps_lat"), lon=loc.get("gps_lon"),
            metro_m=metro if metro and metro < 100_000 else None,   # 100000 = neznámo
            rk=bool(e.get("premise_id")),
        )
        if hid not in zname:          # detail jen u nových inzerátů
            time.sleep(PAUZA_S)
            try:
                dt = s.get(f"{api}/{hid}", timeout=30).json()["result"]
                popis = dt.get("advert_description") or ""
                pozn = " ".join(str(x) for x in (dt.get("price_note"), dt.get("cost_of_living")) if x)
                rb = rozbor(pozn + " " + popis)
                patro = dt.get("floor_number")
                b.update(
                    plocha=dt.get("usable_area") or dt.get("floor_area"),
                    patro=patro - 1 if isinstance(patro, int) else None,  # Sreality: přízemí = 1
                    vybaveni={"Ano": True, "Ne": False, "Částečně": "částečně"}.get(nazev_z(dt.get("furnished"))),
                    kauce=dt.get("refundable_deposit"),
                    od=dt.get("ready_date"),
                    vlozeno=dt.get("since"),
                    koureni=rb.get("koureni"), max2=rb.get("max2"), zvirata=rb.get("zvirata"),
                    kratkodobe=rb.get("kratkodobe"),
                    provize=False if dt.get("tenant_not_pay_commission") else rb.get("provize"),
                    vytah=ano_ne(nazev_z(dt.get("elevator"))) if ano_ne(nazev_z(dt.get("elevator"))) is not None
                    else rb.get("vytah"),
                    venku=bool(dt.get("balcony") or dt.get("loggia") or dt.get("terrace")) or rb.get("venku"),
                    detail_v=DETAIL_V,
                    **odhad_poplatku(b["cena"], pozn, popis),
                )
            except Exception as ex:  # noqa: BLE001
                log(f"  Sreality detail {hid} selhal: {ex}")
        out.append(b)
    return out


# ------------------------------------------------------------- BEZREALITKY --
BZ_QUERY = """query($offset:Int){ listAdverts(offerType:[PRONAJEM], estateType:[BYT],
  regionOsmIds:["R435514"], currency:CZK, limit:60, offset:$offset, order:TIMEORDER_DESC){
  totalCount list{ id uri disposition surface price charges deposit etage equipped availableFrom
  timeActivated gps{lat lng} address(locale:CS) roommate reserved description lift petFriendly
  balconySurface terraceSurface loggiaSurface shortTerm originalPrice } } }"""

VYBAVENI_BZ = {"VYBAVENY": True, "CASTECNE": "částečně", "NEVYBAVENY": False}


def bezrealitky(s, zname):
    out, offset, celkem = [], 0, None
    hdr = {"Origin": "https://www.bezrealitky.cz", "Referer": "https://www.bezrealitky.cz/"}
    while celkem is None or offset < celkem:
        r = s.post("https://api.bezrealitky.cz/graphql/", headers=hdr, timeout=30,
                   json=dict(query=BZ_QUERY, variables=dict(offset=offset)))
        r.raise_for_status()
        d = r.json()["data"]["listAdverts"]
        celkem = d["totalCount"]
        if not d["list"]:
            break
        offset += len(d["list"])
        for a in d["list"]:
            if a.get("roommate"):
                continue
            gps = a.get("gps") or {}
            rb = rozbor(a.get("description"))
            disp = (a.get("disposition") or "").replace("DISP_", "").replace("_KK", "+kk").replace("_1", "+1")
            b = dict(
                id=f"bezrealitky:{a['id']}", zdroj="Bezrealitky",
                url=f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{a['uri']}",
                dispozice=dispozice(disp) or ("1+kk" if disp == "GARSONIERA" else None),
                cena=a.get("price"), plocha=a.get("surface"),
                adresa=a.get("address"), lat=gps.get("lat"), lon=gps.get("lng"),
                patro=a.get("etage"), vybaveni=VYBAVENI_BZ.get(a.get("equipped")),
                kauce=a.get("deposit"), od=ts_datum(a.get("availableFrom")),
                vlozeno=ts_datum(a.get("timeActivated")),
                rk=False, rezervovano=bool(a.get("reserved")),
                koureni=rb.get("koureni"), max2=rb.get("max2"),
                zvirata=True if a.get("petFriendly") else rb.get("zvirata"),
                kratkodobe=True if a.get("shortTerm") else rb.get("kratkodobe"),
                provize=False,
                vytah=True if a.get("lift") else rb.get("vytah"),
                venku=bool(a.get("balconySurface") or a.get("terraceSurface") or a.get("loggiaSurface"))
                or rb.get("venku"),
                cena_puvodni=a.get("originalPrice") if (a.get("originalPrice") or 0) > (a.get("price") or 0)
                else None,
                detail_v=DETAIL_V,
            )
            if a.get("charges"):          # 0 = nevyplněno, ne "zdarma"
                b.update(popl=a["charges"], popl_os=False, popl_info="z inzerátu")
            else:
                b.update(odhad_poplatku(b["cena"], a.get("description")))
            out.append(b)
        if LIMIT and len(out) >= LIMIT:
            break
    return out


# ------------------------------------------------------------------ BAZOŠ --
def bazos(s, zname):
    base = "https://reality.bazos.cz"
    params = dict(hledat="", hlokalita="12000", humkreis=20)   # 120 00 = Praha 2, kruh přes celou Prahu
    out, offset = [], 0
    while True:
        cesta = "/pronajmu/byt/" + (f"{offset}/" if offset else "")
        r = s.get(base + cesta, params=params, timeout=30)
        if r.status_code == 404 and offset:   # za poslední stránkou vrací Bazoš 404
            break
        r.raise_for_status()
        inz = BeautifulSoup(r.text, "lxml").select("div.inzeraty")
        if not inz:
            break
        for i in inz:
            lok = i.select_one("div.inzeratylok").get_text(" ", strip=True)
            if "praha" not in bez_diakritiky(lok):
                continue
            a = i.select_one("h2.nadpis a")
            nazev = a.get_text(strip=True)
            m = re.search(r"/inzerat/(\d+)/", a["href"])
            if not m:
                continue
            bid = m.group(1)
            cena_txt = i.select_one("div.inzeratycena").get_text(" ", strip=True)
            cena = int(re.sub(r"\D", "", cena_txt)) if re.search(r"\d", cena_txt) else None
            datum = re.search(r"\[(\d+)\.(\d+)\.\s*(\d{4})\]", i.get_text())
            plocha = re.search(r"(\d{2,3})\s*m(2|²)", nazev)
            b = dict(
                id=f"bazos:{bid}", zdroj="Bazoš", url=base + a["href"], dispozice=dispozice(nazev), nazev=nazev[:90],
                cena=cena, adresa=lok, plocha=int(plocha.group(1)) if plocha else None,
                vlozeno=f"{datum.group(3)}-{int(datum.group(2)):02d}-{int(datum.group(1)):02d}" if datum else None,
            )
            if bid not in zname:
                time.sleep(PAUZA_S)
                try:
                    dt = s.get(b["url"], timeout=30).text
                    g = re.search(r"maps/place/([\d.]+),([\d.]+)", dt)
                    if g:
                        b["lat"], b["lon"] = float(g.group(1)), float(g.group(2))
                    p = BeautifulSoup(dt, "lxml").select_one("div.popisdetail")
                    popis = p.get_text("\n", strip=True) if p else ""
                    rb = rozbor(nazev + " " + popis)
                    if not b["dispozice"]:
                        b["dispozice"] = dispozice(popis)
                    if not b["plocha"]:
                        pl = re.search(r"(\d{2,3})\s*m(2|²)", popis)
                        b["plocha"] = int(pl.group(1)) if pl else None
                    patro = re.search(r"\b(\d{1,2})\.\s*(patr|podlazi|np)", bez_diakritiky(popis))
                    b.update(rb, patro=int(patro.group(1)) if patro else None, detail_v=DETAIL_V,
                             **odhad_poplatku(cena, popis))
                except Exception as ex:  # noqa: BLE001
                    log(f"  Bazoš detail {bid} selhal: {ex}")
            out.append(b)
        if LIMIT and len(out) >= LIMIT:
            break
        offset += 20
        time.sleep(PAUZA_S)
    return out


# --------------------------------------------------------------- DATABÁZE --
SLOUPCE = ["zdroj", "url", "nazev", "dispozice", "cena", "popl", "popl_os", "popl_info", "plocha", "adresa",
           "lat", "lon", "metro_m", "patro", "vybaveni", "kauce", "od", "vlozeno", "rk", "provize",
           "koureni", "max2", "zvirata", "kratkodobe", "vytah", "venku", "rezervovano", "detail_v"]
STARE_SLOUPCE = ["km", "pozn_cena", "varovani", "popis", "poplatky"]   # z verze 1, už se nepoužívají


def otevri_db():
    db = sqlite3.connect(DB_SOUBOR)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS byty (id TEXT PRIMARY KEY, cena_puvodni,
        prvni_videni TEXT, posledni_videni TEXT, aktivni INTEGER DEFAULT 1)""")
    mam = {r["name"] for r in db.execute("PRAGMA table_info(byty)")}
    for c in SLOUPCE:
        if c not in mam:
            db.execute(f"ALTER TABLE byty ADD COLUMN {c}")
    for c in STARE_SLOUPCE:      # popisy inzerátů nedržet (DB je ve veřejném repu)
        if c in mam:
            db.execute(f"ALTER TABLE byty DROP COLUMN {c}")
    return db


def uloz(db, byty, beh):
    nove = 0
    for b in byty:
        stary = db.execute("SELECT * FROM byty WHERE id=?", (b["id"],)).fetchone()
        if stary is None:
            nove += 1
            cols = ["id"] + SLOUPCE + ["cena_puvodni", "prvni_videni", "posledni_videni", "aktivni"]
            vals = [b["id"]] + [b.get(c) for c in SLOUPCE] + [b.get("cena_puvodni"), beh, beh, 1]
            db.execute(f"INSERT INTO byty ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        else:
            # Aktualizuj jen to, co přišlo (detail se u starých nestahuje znovu).
            zmeny = {c: b[c] for c in SLOUPCE if b.get(c) is not None}
            if stary["cena"] and b.get("cena") and b["cena"] != stary["cena"]:
                zmeny["cena_puvodni"] = stary["cena_puvodni"] or stary["cena"]
            elif b.get("cena_puvodni"):
                zmeny["cena_puvodni"] = b["cena_puvodni"]
            zmeny.update(posledni_videni=beh, aktivni=1)
            db.execute(f"UPDATE byty SET {', '.join(k + '=?' for k in zmeny)} WHERE id=?",
                       [*zmeny.values(), b["id"]])
    return nove


# -------------------------------------------------------------------- WEB --
WEB_POLE = ["id", "zdroj", "url", "nazev", "dispozice", "cena", "popl", "popl_os", "popl_info", "plocha", "adresa",
            "lat", "lon", "metro_m", "patro", "vybaveni", "kauce", "od", "vlozeno", "rk", "provize",
            "koureni", "max2", "zvirata", "kratkodobe", "vytah", "venku", "rezervovano",
            "cena_puvodni", "prvni_videni"]
PORADI_ZDROJU = {"Bezrealitky": 0, "Sreality": 1, "Bazoš": 2}   # u duplicit vyhrává ten s nejvíc údaji


def cislo(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def je_duplicita(a, b):
    """Stejný byt na jiném portálu: stejná dispozice, plocha ±2 m², cena ±5 %, do 400 m."""
    return (a["zdroj"] != b["zdroj"] and a.get("dispozice") == b.get("dispozice")
            and abs(a["plocha"] - b["plocha"]) <= 2
            and abs(a["cena"] - b["cena"]) <= 0.05 * max(a["cena"], b["cena"])
            and km(a, b) <= 0.4)


def sluc_duplicity(byty):
    lze = [b for b in byty if b.get("plocha") and b.get("cena") and b.get("lat")]
    skupiny = {}
    for b in lze:
        skupiny.setdefault((b.get("dispozice"), round(b["plocha"] / 5)), []).append(b)
    pryc = set()
    for b in sorted(lze, key=lambda b: PORADI_ZDROJU.get(b["zdroj"], 9)):
        if b["id"] in pryc:
            continue
        for kl in (round(b["plocha"] / 5) - 1, round(b["plocha"] / 5), round(b["plocha"] / 5) + 1):
            for o in skupiny.get((b.get("dispozice"), kl), []):
                if o["id"] != b["id"] and o["id"] not in pryc and je_duplicita(b, o):
                    pryc.add(o["id"])
                    b.setdefault("dalsi", []).append(dict(zdroj=o["zdroj"], url=o["url"]))
                    for k, v in o.items():       # doplnit, co hlavnímu inzerátu chybí
                        if b.get(k) is None and v is not None and k in WEB_POLE:
                            b[k] = v
    return [b for b in byty if b["id"] not in pryc]


def skup_disp(d):
    return "4+" if d and d[0] in "456789" else d


def cena_vuci_okoli(byty, okruh_km=1.5, min_vzorek=6):
    """Doplní "rel": o kolik je nájem za m² vyšší/nižší než medián stejné dispozice v okolí (−0.12 = o 12 % levnější).
    Když je v okolí málo bytů, srovná s celou Prahou."""
    lze = [b for b in byty if b.get("plocha") and b["plocha"] >= 10 and b.get("cena") and b.get("lat")]
    mrizka, praha = {}, {}
    for b in lze:
        b["_m2"] = b["cena"] / b["plocha"]
        b["_bunka"] = (round(b["lat"] / 0.0135), round(b["lon"] / 0.021))   # ~1,5 × 1,5 km
        mrizka.setdefault((skup_disp(b.get("dispozice")), b["_bunka"]), []).append(b)
        praha.setdefault(skup_disp(b.get("dispozice")), []).append(b["_m2"])

    def median(a):
        a = sorted(a)
        return a[len(a) // 2] if a else None

    for b in lze:
        d, (x, y) = skup_disp(b.get("dispozice")), b["_bunka"]
        okoli = [o["_m2"] for dx in (-1, 0, 1) for dy in (-1, 0, 1) for o in mrizka.get((d, (x + dx, y + dy)), [])
                 if o is not b and km(b, o) <= okruh_km]
        if len(okoli) < min_vzorek:
            okoli = praha.get(d, []) if len(praha.get(d, [])) > min_vzorek else []
        med = median(okoli)
        if med:
            b["rel"] = round(b["_m2"] / med - 1, 2)
    for b in lze:
        del b["_m2"], b["_bunka"]


def uloz_web(db, beh):
    byty = []
    for r in db.execute("SELECT * FROM byty WHERE aktivni=1 AND cena BETWEEN 3000 AND 500000"):
        b = {k: cislo(r[k]) for k in WEB_POLE}
        for k in ("rk", "provize", "koureni", "max2", "zvirata", "kratkodobe", "vytah", "venku",
                  "rezervovano", "popl_os"):
            if b[k] is not None:
                b[k] = bool(b[k])
        if b["popl"] == 0:                  # 0 = nevyplněno (starší záznamy z Bezrealitek)
            b["popl"], b["popl_info"] = None, "poplatky neuvedeny"
        if b["vybaveni"] in (0, 1):          # SQLite drží True/False jako 1/0, "částečně" jako text
            b["vybaveni"] = bool(b["vybaveni"])
        byty.append(b)
    byty = sluc_duplicity(byty)
    cena_vuci_okoli(byty)
    # Vynechat prázdné hodnoty; False nechat jen tam, kde nese informaci ("zákaz", "bez výtahu"…).
    tri_stav = {"koureni", "zvirata", "provize", "vytah", "rk", "vybaveni"}
    byty = [{k: v for k, v in b.items() if v not in (None, "") and (v is not False or k in tri_stav)}
            for b in byty]
    data = dict(aktualizovano=beh, byty=byty)
    WEB_JSON.parent.mkdir(exist_ok=True)
    WEB_JSON.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return byty


# ------------------------------------------------------------------- MAIN --
def main():
    beh = datetime.now().strftime("%Y-%m-%d %H:%M")
    log(f"=== Start ({beh}){f' LIMIT {LIMIT}' if LIMIT else ''} ===")
    s, db = session(), otevri_db()
    for nazev, fce in (("Sreality", sreality), ("Bezrealitky", bezrealitky), ("Bazoš", bazos)):
        try:
            zname = {r["id"].split(":", 1)[1] for r in db.execute(
                "SELECT id FROM byty WHERE zdroj=? AND detail_v>=?", (nazev, DETAIL_V))}
            byty = fce(s, zname)
            nove = uloz(db, byty, beh)
            # Co zdroj už nevrací, je pryč (pronajato/smazáno). Jen když zdroj prošel celý a bez chyby.
            if not LIMIT:
                db.execute("UPDATE byty SET aktivni=0 WHERE zdroj=? AND posledni_videni<>?", (nazev, beh))
            db.commit()
            log(f"{nazev}: {len(byty)} inzerátů, z toho {nove} nových")
        except Exception as ex:  # noqa: BLE001
            db.rollback()
            log(f"{nazev}: CHYBA {type(ex).__name__}: {ex}")
    db.execute("VACUUM")
    byty = uloz_web(db, beh)
    log(f"Web: {WEB_JSON}  ({len(byty)} bytů po sloučení duplicit)")
    try:
        import hlidani
        hlidani.spust(byty, s, log)
    except Exception as ex:  # noqa: BLE001
        log(f"Hlídání: CHYBA {type(ex).__name__}: {ex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
