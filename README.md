# Byty Praha – pronájmy

Rychlý a přehledný přehled bytů k pronájmu v Praze z více portálů na jednom místě, s chytrými filtry.

**Web:** https://tuzik727.github.io/byty/
**Autor:** [Tuzik727](https://github.com/Tuzik727)

## Co umí

- **Jeden seznam ze tří zdrojů:** Sreality, Bezrealitky a Bazoš. Stejný byt, který je na více portálech, se zobrazí jen jednou, s odkazy na všechny.
- **Chytré filtry:**
  - **Základní:** dispozice, cena, počet osob, plocha, vzdálenost od zvoleného místa.
  - **Z popisu inzerátu:** kouření, zvířata, venkovní prostor, výtah, vybavení, bez realitky / bez provize, termín nastěhování.
  - Web vždy napíše, kolik bytů a proč navíc skryl.
- **Cena vůči okolí:** porovná cenu za m² s podobnými byty do 1,5 km a upozorní na podezřele levné nabídky.
- **Moje hledání:** oblíbené, skryté a stav (zajímá → napsáno → odpověděli → prohlídka → rozhodnuto).
- **Napsat:** zkopíruje připravenou zprávu pronajímateli a byt si označí jako kontaktovaný.
- **Mapa, klávesové zkratky (⌨), tmavý i světlý režim.**
- **Hlídání:** push notifikace do mobilu (ntfy) o nových bytech podle uložených filtrů.

Nastavení i seznamy se ukládají jen v prohlížeči. Filtry jsou v adrese stránky, takže hledání jde poslat odkazem. Zálohu udělá export a import.

## Jak to funguje

Celé to běží zdarma na GitHubu, bez vlastního serveru.

- **Stahování:** GitHub Actions ([`.github/workflows/byty.yml`](.github/workflows/byty.yml)) spouští `hledej_byty.py` plánovaně, každých ~30 minut. GitHub plánované běhy u zdarma účtů zpožďuje nebo vynechává.
- **Co skript dělá:** stáhne nabídky, porovná je s databází `byty.db` a zapíše `web/byty.json`.
- **Data:** databáze a JSON se ukládají do větve `data`, kde je jen poslední verze bez historie.
- **Web:** `web/index.html` je jediný statický soubor (čisté JS, mapa Leaflet + OpenStreetMap) a nasazuje se na GitHub Pages.
- **Popisy inzerátů** se neukládají ani nezveřejňují. Při stažení se z nich jen vyčtou fakta (poplatky, kouření, zvířata, provize…).

## Hlídání

1. Nainstalujte si aplikaci [ntfy](https://ntfy.sh).
2. Na webu nastavte filtry a klikněte na **🔔 Hlídat**.
3. Web vytvoří předvyplněné issue „Hlídání: …“ s odkazem na hledání a tématem pro ntfy. Stačí ho odeslat (je potřeba účet na GitHubu).

Po každém stažení `hlidani.py` pošle notifikaci o nových bytech, které hledání odpovídají. Nic nepřijde dvakrát. Zpracují se jen issue od vlastníka repa, nebo issue se štítkem `hlidani`.

## Spuštění lokálně

```
pip install -r requirements.txt
python -X utf8 hledej_byty.py      # nebo spust.bat
```

`BYTY_LIMIT=N` omezí počet inzerátů na zdroj, hodí se na rychlý test. Web pak otevřete přes lokální server, třeba `python -m http.server -d web`.

## Soubory

| Soubor | Co dělá |
|---|---|
| `hledej_byty.py` | stažení, rozbor inzerátů, sloučení duplicit, cena vůči okolí, `web/byty.json` |
| `hlidani.py` | hlídání: čte issue, filtruje stejně jako web, posílá ntfy notifikace |
| `web/index.html` | celý web |
| `.github/workflows/byty.yml` | plánované stahování a nasazení webu |
