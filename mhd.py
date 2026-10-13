"""
Jízdní řády PID (otevřená data GTFS) -> web/mhd.bin: malý binární soubor, ze kterého web spočítá
dojezd MHD od každého bytu do zvoleného místa (algoritmus CSA přímo v prohlížeči).

Bere se jeden běžný pracovní den (nejbližší středa) a ranní špička: spoje s příjezdem 6:30–8:30,
cíl = být na místě v 8:30. Zastávky jen v okolí Prahy.

Formát (little-endian), všechna čísla bez znaménka, pokud není řečeno jinak:
  Uint32 x 5   : verze, počet zastávek, počet spojů, počet přestupních chůzí, den (RRRRMMDD)
  Int32  x 2n  : zastávky [lat*1e5, lon*1e5, ...]
  Uint32 x n+1 : začátky seznamů chůze ze zastávky (CSR)
  Uint16 x 6c  : spoje seřazené od nejpozdějšího příjezdu: [z, do, spoj, odjezd, příjezd, 0]
                 (časy v sekundách od 6:30; spoj = pořadové číslo jízdy)
  Uint16 x 2f  : chůze [cílová zastávka, sekundy]
Spuštění: python mhd.py   (stáhne GTFS, ~50 MB; vynechá, dokud vybraná středa neuplyne)
"""
import csv
import io
import math
import struct
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

GTFS = "https://data.pid.cz/PID_GTFS.zip"
VYSTUP = Path(__file__).resolve().parent / "web" / "mhd.bin"
VERZE = 1
OD, DO = 6 * 3600 + 30 * 60, 8 * 3600 + 30 * 60      # okno ranní špičky
LAT, LON = (49.93, 50.20), (14.18, 14.74)              # Praha s rezervou
CHUZE_M = 250                                          # přestup pěšky mezi zastávkami do 250 m
M_ZA_S = 1.39 / 1.25                                   # 5 km/h, cesta o čtvrtinu delší než vzdušnou čarou


def sekundy(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def metry(a, b):
    k = 111_320
    return math.hypot((a[0] - b[0]) * k / 1.0, (a[1] - b[1]) * k * math.cos(math.radians(a[0])))


def den(z):
    """Nejbližší středa, kterou jízdní řád pokrývá, a služby, které ten den jezdí."""
    cal = list(csv.DictReader(io.TextIOWrapper(z.open("calendar.txt"), "utf-8-sig")))
    vyj = list(csv.DictReader(io.TextIOWrapper(z.open("calendar_dates.txt"), "utf-8-sig")))
    d = date.today() + timedelta(days=(2 - date.today().weekday()) % 7)
    ds = d.strftime("%Y%m%d")
    sluzby = {c["service_id"] for c in cal if c["wednesday"] == "1" and c["start_date"] <= ds <= c["end_date"]}
    for v in vyj:
        if v["date"] == ds:
            (sluzby.add if v["exception_type"] == "1" else sluzby.discard)(v["service_id"])
    return ds, sluzby


def postav(z):
    ds, sluzby = den(z)
    jizdy = {t["trip_id"] for t in csv.DictReader(io.TextIOWrapper(z.open("trips.txt"), "utf-8-sig"))
             if t["service_id"] in sluzby}
    zast = {}
    for s in csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), "utf-8-sig")):
        if s["location_type"] in ("", "0") and s["stop_lat"]:
            la, lo = float(s["stop_lat"]), float(s["stop_lon"])
            if LAT[0] <= la <= LAT[1] and LON[0] <= lo <= LON[1]:
                zast[s["stop_id"]] = (la, lo)

    # zastavení po jízdách -> spoje mezi sousedními zastávkami
    po_jizdach = {}
    for r in csv.DictReader(io.TextIOWrapper(z.open("stop_times.txt"), "utf-8-sig")):
        if r["trip_id"] in jizdy:
            po_jizdach.setdefault(r["trip_id"], []).append(
                (int(r["stop_sequence"]), r["stop_id"], sekundy(r["arrival_time"]), sekundy(r["departure_time"]),
                 r["pickup_type"] != "1", r["drop_off_type"] != "1"))
    idx, spoje, cislo_jizdy = {}, [], {}
    for jid, st in po_jizdach.items():
        st.sort()
        for a, b in zip(st, st[1:]):
            _, z1, _, odj, nastup, _ = a
            _, z2, prij, _, _, vystup = b
            if z1 not in zast or z2 not in zast or not nastup or not vystup or odj < OD or prij > DO:
                continue
            j = cislo_jizdy.setdefault(jid, len(cislo_jizdy))
            for zz in (z1, z2):
                idx.setdefault(zz, len(idx))
            spoje.append((idx[z1], idx[z2], j, odj - OD, prij - OD))
    spoje.sort(key=lambda c: -c[4])
    souradnice = [None] * len(idx)
    for zid, i in idx.items():
        souradnice[i] = zast[zid]

    # přestupy pěšky: zastávky do CHUZE_M metrů (mřížka ~250 m)
    mriz = {}
    for i, (la, lo) in enumerate(souradnice):
        mriz.setdefault((int(la / 0.00225), int(lo / 0.0035)), []).append(i)
    chuze = [[] for _ in souradnice]
    for i, (la, lo) in enumerate(souradnice):
        bx, by = int(la / 0.00225), int(lo / 0.0035)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in mriz.get((bx + dx, by + dy), ()):
                    if j != i:
                        m = metry(souradnice[i], souradnice[j])
                        if m <= CHUZE_M:
                            chuze[i].append((j, round(m / M_ZA_S) + 30))   # +30 s na přestup
    if len(idx) > 65535 or len(cislo_jizdy) > 65535:
        raise SystemExit("Moc zastávek nebo jízd pro Uint16")
    return ds, souradnice, spoje, chuze


def zapis(ds, souradnice, spoje, chuze):
    n, c = len(souradnice), len(spoje)
    f = sum(len(x) for x in chuze)
    out = bytearray(struct.pack("<5I", VERZE, n, c, f, int(ds)))
    out += struct.pack(f"<{2 * n}i", *[round(v * 1e5) for la_lo in souradnice for v in la_lo])
    zac = [0]
    for x in chuze:
        zac.append(zac[-1] + len(x))
    out += struct.pack(f"<{n + 1}I", *zac)
    out += struct.pack(f"<{6 * c}H", *[v for s in spoje for v in (*s, 0)])
    out += struct.pack(f"<{2 * f}H", *[v for x in chuze for j_s in x for v in j_s])
    VYSTUP.parent.mkdir(exist_ok=True)
    VYSTUP.write_bytes(out)
    return len(out)


def main():
    if VYSTUP.exists():   # přepočítat, až vybraná středa uplyne (soubor se obnovuje z větve data, mtime nic neříká)
        hlava = struct.unpack("<5I", VYSTUP.read_bytes()[:20])
        if hlava[0] == VERZE and str(hlava[4]) >= date.today().strftime("%Y%m%d") and "--znovu" not in sys.argv:
            print(f"MHD: {VYSTUP.name} je aktuální ({hlava[4]})")
            return 0
    r = requests.get(GTFS, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        ds, souradnice, spoje, chuze = postav(z)
    vel = zapis(ds, souradnice, spoje, chuze)
    print(f"MHD: den {ds}, {len(souradnice)} zastávek, {len(spoje)} spojů, {vel / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
