# Byty Praha – pronájmy

👉 **https://tuzik727.github.io/byty/**

Nebaví mě Sreality a spol. Jsou nepřehledný a stejnej byt tam vidíš pětkrát. Tak jsem si udělal vlastní web. Rychlej, jednoduchej, přehlednej a s filtrama, který fakt pomůžou najít byt.

Autor: [Tuzik727](https://github.com/Tuzik727)

## Co to umí

- **Všechno na jednom místě.** Sreality, Bezrealitky i Bazoš. Když je byt na víc portálech, ukáže se jen jednou.
- **Chytrý filtry.** Kromě ceny, dispozice a plochy:
  - jestli se smí kouřit nebo mít zvíře,
  - jestli je tam balkon nebo terasa a výtah,
  - jestli je to bez realitky a bez provize,
  - kdy se dá nastěhovat,
  - jak daleko je to od místa, který si vybereš.

  A web ti vždycky řekne, co a proč skryl, nic nezmizí potichu.
- **Je to levný, nebo drahý?** U každýho bytu vidíš, jak je na tom cenou proti bytům okolo. Podezřele levný věci jsou označený.
- **Moje hledání.** Oblíbený, skrytý a kde jsem v jaký fázi: zajímá → napsáno → odpověděli → prohlídka → rozhodnuto.
- **Napsat.** Jedním klikem zkopíruješ připravenou zprávu pro pronajímatele a byt se označí jako kontaktovanej.
- **Hlídání.** Když se objeví novej byt podle tvých filtrů, pípne ti to na mobilu.
- Mapa, klávesový zkratky (⌨), dark mode.

Nikde se nepřihlašuješ. Všechno, co si nastavíš, zůstává jen u tebe v prohlížeči. Filtry jsou v odkazu, takže hledání můžeš poslat kámošovi.

## Hlídání

1. Stáhni si appku [ntfy](https://ntfy.sh).
2. Na webu si nastav filtry a klikni na **🔔 Hlídat**.
3. Otevře se předvyplněný formulář na GitHubu. Stačí ho odeslat (potřebuješ účet na GitHubu).

Hotovo. Každej novej byt ti přijde jen jednou.

## Jak to běží

Celý to jede zdarma na GitHubu, žádnej server.

- GitHub Actions zhruba každou půlhodinu stáhne nový nabídky. GitHub to někdy zpozdí nebo vynechá, to je daň za zadarmo.
- Data leží ve větvi `data`.
- Web je jeden HTML soubor na GitHub Pages.
- Popisy inzerátů neukládám. Vytáhnu z nich jen to důležitý: poplatky, kouření, zvířata, provizi…

## Spuštění u sebe

```
pip install -r requirements.txt
python -X utf8 hledej_byty.py      # nebo spust.bat
python -m http.server -d web       # pak otevři http://localhost:8000
```

Na rychlej test nastav `BYTY_LIMIT=50`. Stáhne se jen pár inzerátů z každýho webu.

## Co je co

| Soubor | Co dělá |
|---|---|
| `hledej_byty.py` | stahuje byty, čte z popisů, slučuje duplicity, počítá cenu proti okolí |
| `hlidani.py` | posílá notifikace o nových bytech |
| `web/index.html` | celej web |
| `.github/workflows/byty.yml` | pravidelný stahování a nasazení webu |
