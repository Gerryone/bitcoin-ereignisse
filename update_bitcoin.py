# 14.08.2026 13:05
#!/usr/bin/env python3
"""
Bitcoin-Ereignisse Update (Claude Haiku) – REINE RECHERCHE

GEÄNDERT 13.08.2026 (12:20): Haiku erstellt KEIN Tagesfazit mehr.

Warum: Die Auswertung vom 13.08.2026 hat gezeigt, dass beide Modelle
denselben Fehler machen (Dauer-Skepsis, 33 von 35 Prognosen negativ bei
+6,4 % Kursentwicklung), nur unterschiedlich stark. Ein Ensemble aus zwei
gleich verzerrten Prognosen hat nichts zu mitteln. Zudem war die
Unabhaengigkeit ohnehin verletzt, weil das zweite Modell Haikus fertige
Kursziele in den Prompt bekam und darauf geankert war.

Neue Rollenverteilung: Haiku recherchiert Ereignisse — ueberpruefbare
Faktenarbeit, bei der ein kleines Modell gut und guenstig ist. Die
Prognose macht ausschliesslich scripts/claude_einschaetzung.py.

Entfernt wurden deshalb:
  - der komplette "tagesfazit"-Block aus Prompt, Validierung und Speicherung
  - der Rueckblick-Mechanismus, mit dem Haiku seine eigenen alten Fazits
    nachtraeglich bewertet hat (ohne eigene Prognosen gegenstandslos)
  - formatiere_kursziel_alt_oder_neu()

Bestehende "fazits" in ereignisse.json bleiben unangetastet stehen — die
Historie wird nicht veraendert, es kommen nur keine neuen mehr dazu.

NEU: Kategorie-Validierung. Die sieben erlaubten Kategorien standen bisher
nur im Prompt und wurden nie geprueft — deshalb liegen "Technologie",
"Whale-Verkauf", "Sicherheit", "Sicherheit & Regulierung" und "Geopolitik"
in der Datenbank, obwohl es sie nicht geben duerfte. Ein Ereignis mit
unerlaubter Kategorie faellt jetzt sichtbar auf "Sonstiges" zurueck.

NEU: modell und prompt_version werden an jedem Ereignis mitgespeichert,
damit ein spaeterer Modellwechsel die Historie nicht stillschweigend
vermischt.
"""

import json
import os
import sys
import requests
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
import anthropic


# ─── Daten-Abruf ────────────────────────────────────────────────────────────

# ─── Konfiguration ──────────────────────────────────────────────────────────

MODELL = "claude-haiku-4-5"

# Anlagewert, auf den sich die recherchierten Ereignisse beziehen.
ASSET = "BTC"
MAX_TOKENS = 6000

# Bei jeder inhaltlichen Aenderung am Prompt hochzaehlen.
PROMPT_VERSION = 2

# Wird jetzt tatsaechlich geprueft, nicht nur im Prompt erwaehnt.
ERLAUBTE_KATEGORIEN = [
    "ETF", "Regulierung", "Institutionell", "Makro",
    "OnChain", "Technik", "Persönlichkeiten",
]
ERSATZ_KATEGORIE = "Sonstiges"

ANZAHL_EREIGNISSE_BEHALTEN = 60


def fetch_crypto_prices():
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "eur,usd",
                "include_24hr_change": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        btc = data["bitcoin"]
        eth = data["ethereum"]
        print(f"  BTC Kurs: €{btc['eur']:,.0f} / ${btc['usd']:,.0f} ({btc.get('eur_24h_change', 0):+.1f}% 24h)")
        print(f"  ETH Kurs: €{eth['eur']:,.0f} / ${eth['usd']:,.0f} ({eth.get('eur_24h_change', 0):+.1f}% 24h)")
        return {
            "btc_eur": btc["eur"], "btc_usd": btc["usd"], "btc_change_24h": btc.get("eur_24h_change", 0),
            "eth_eur": eth["eur"], "eth_usd": eth["usd"], "eth_change_24h": eth.get("eur_24h_change", 0),
        }
    except Exception as e:
        print(f"  Warnung: Preisabruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return {
            "btc_eur": 53000, "btc_usd": 61000, "btc_change_24h": 0,
            "eth_eur": 1450, "eth_usd": 1650, "eth_change_24h": 0,
        }


def fetch_precious_metals():
    """
    Gold- und Silberkurs in EUR abrufen.

    GEÄNDERT (18.07.2026): api.metals.live/v1/spot ist nicht mehr
    erreichbar (SSL-Handshake schlägt fehl: TLSV1_UNRECOGNIZED_NAME -
    der Endpunkt existiert offenbar nicht mehr / wurde stillgelegt).
    Umgestellt auf gold-api.com, das keinen API-Key benötigt und
    Gold/Silber-Spotpreise in USD pro Feinunze liefert.

    Fallback auf Vortagswert falls API nicht erreichbar. Wechselkurs
    USD→EUR wird weiterhin separat von api.frankfurter.app geholt.
    """
    try:
        gold_resp = requests.get(
            "https://api.gold-api.com/price/XAU",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        gold_resp.raise_for_status()
        gold_usd = float(gold_resp.json()["price"])

        silver_resp = requests.get(
            "https://api.gold-api.com/price/XAG",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        silver_resp.raise_for_status()
        silver_usd = float(silver_resp.json()["price"])

        # USD→EUR Kurs für Umrechnung
        fx_resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            timeout=15,
        )
        fx_resp.raise_for_status()
        usd_to_eur = fx_resp.json()["rates"]["EUR"]

        gold_eur = round(gold_usd * usd_to_eur, 2)
        silver_eur = round(silver_usd * usd_to_eur, 4)

        print(f"  Gold:   ${gold_usd:,.2f} / €{gold_eur:,.2f} pro Unze")
        print(f"  Silber: ${silver_usd:,.3f} / €{silver_eur:,.3f} pro Unze")
        print(f"  USD/EUR: {usd_to_eur:.4f}")

        return {
            "gold_usd": gold_usd,
            "gold_eur": gold_eur,
            "silver_usd": silver_usd,
            "silver_eur": silver_eur,
            "usd_eur": usd_to_eur,
        }
    except Exception as e:
        print(f"  Warnung: Edelmetall-Abruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return {
            "gold_usd": 3200.0,
            "gold_eur": 2950.0,
            "silver_usd": 32.0,
            "silver_eur": 29.5,
            "usd_eur": 0.922,
        }


def load_edelmetalle(path="edelmetalle.json"):
    """Bisherige Edelmetall-Kursdaten laden (für Verlaufsdarstellung in HA)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"kurse": []}


def save_edelmetalle(daten, path="edelmetalle.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


def update_edelmetalle(metalle, heute):
    """
    Tageskurs in edelmetalle.json eintragen. Bereits vorhandener Eintrag
    für heute wird überschrieben (Idempotenz bei mehrfachem Run).
    Maximale Anzahl gespeicherter Tage: 90.
    """
    daten = load_edelmetalle()
    kurse = daten.get("kurse", [])

    # Heutigen Eintrag entfernen falls vorhanden (Überschreiben)
    kurse = [k for k in kurse if k["datum"] != heute]

    kurse.insert(0, {
        "datum": heute,
        "gold_eur": metalle["gold_eur"],
        "gold_usd": metalle["gold_usd"],
        "silver_eur": metalle["silver_eur"],
        "silver_usd": metalle["silver_usd"],
        "usd_eur": metalle["usd_eur"],
    })

    # Auf 90 Tage begrenzen
    kurse = kurse[:90]
    daten["kurse"] = kurse
    daten["letzte_aktualisierung"] = heute
    save_edelmetalle(daten)
    print(f"  ✓ edelmetalle.json aktualisiert ({len(kurse)} Einträge)")
    return daten


def fetch_fear_greed():
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        wert = int(data["value"])
        klassifikation = data.get("value_classification", "unbekannt")
        print(f"  Fear & Greed: {wert} ({klassifikation})")
        return {"wert": wert, "klassifikation": klassifikation}
    except Exception as e:
        print(f"  Warnung: Fear&Greed-Abruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return {"wert": 50, "klassifikation": "Neutral (Fallback)"}


def fetch_rss(url, source_name, cutoff_hours=48):
    articles = []
    cutoff = datetime.utcnow() - timedelta(hours=cutoff_hours)
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            desc  = (item.findtext("description") or "").strip()[:400]
            pub   = item.findtext("pubDate") or ""
            pub_dt = None
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
                try:
                    pub_dt = datetime.strptime(pub.strip(), fmt).replace(tzinfo=None)
                    break
                except ValueError:
                    pass
            if pub_dt and pub_dt < cutoff:
                continue
            if title:
                articles.append({
                    "source": source_name,
                    "title": title,
                    "desc": desc,
                    "pub": pub_dt.strftime("%Y-%m-%d %H:%M") if pub_dt else "unbekannt",
                })
    except Exception as e:
        print(f"  Warnung: RSS {source_name} fehlgeschlagen ({e})", file=sys.stderr)
    return articles


def fetch_recent_news():
    feeds = [
        ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
        ("https://cointelegraph.com/rss", "CoinTelegraph"),
        ("https://bitcoinmagazine.com/.rss/full/", "Bitcoin Magazine"),
        ("https://cryptonews.com/news/feed/", "CryptoNews"),
    ]
    all_articles = []
    for url, name in feeds:
        arts = fetch_rss(url, name)
        all_articles.extend(arts)
        print(f"  {name}: {len(arts)} Artikel")

    btc_keywords = ["bitcoin", "btc", "satoshi", "lightning", "halving",
                    "etf", "blackrock", "microstrategy", "strategy", "sec",
                    "fed", "inflation", "crypto", "blockchain"]
    relevant = [
        a for a in all_articles
        if any(kw in (a["title"] + a["desc"]).lower() for kw in btc_keywords)
    ]
    lines = []
    for a in relevant[:30]:
        lines.append(f"[{a['pub']}] {a['source']}: {a['title']}")
        if a["desc"]:
            lines.append(f"  → {a['desc']}")
        lines.append("")
    print(f"  Gesamt: {len(relevant)} relevante Bitcoin-Artikel")
    return "\n".join(lines)


# ─── JSON-Datei ─────────────────────────────────────────────────────────────

def load_data(path="ereignisse.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(daten, path="ereignisse.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


# ─── Claude-Prompt ──────────────────────────────────────────────────────────

def build_prompt(daten, news_text, preise, fear_greed, metalle, heute):
    """Reiner Recherche-Prompt. Kein Tagesfazit, keine Kursziele fuer den
    Gesamtmarkt — das macht seit 13.08.2026 ausschliesslich
    scripts/claude_einschaetzung.py."""
    existing_titles = [e["titel"] for e in daten.get("ereignisse", [])]

    return f"""Du bist ein Bitcoin-Marktbeobachter. Heute ist der {heute}.

Deine Aufgabe ist RECHERCHE, keine Prognose. Du sammelst und beschreibst,
was tatsaechlich passiert ist. Ob daraus steigende oder fallende Kurse
folgen, entscheidet ein anderer Schritt — du sollst dazu ausdruecklich
KEINE Gesamteinschaetzung abgeben.

AKTUELLER BITCOIN-KURS: €{preise['btc_eur']:,.0f} EUR / ${preise['btc_usd']:,.0f} USD (24h: {preise['btc_change_24h']:+.1f}%)

MARKTKONTEXT ETHEREUM:
ETH-Kurs: €{preise['eth_eur']:,.0f} EUR / ${preise['eth_usd']:,.0f} USD (24h: {preise['eth_change_24h']:+.1f}%)

MARKTKONTEXT EDELMETALLE (Kurs pro Unze):
Gold:   €{metalle['gold_eur']:,.2f} EUR / ${metalle['gold_usd']:,.2f} USD
Silber: €{metalle['silver_eur']:,.3f} EUR / ${metalle['silver_usd']:,.3f} USD

Beziehe Gold und Silber in deine Ereignis-Beschreibungen ein, wo relevant:
- Bewegt sich Gold parallel zu Bitcoin (breite Inflations-/Krisenangst)?
- Laeuft Gold besser als Bitcoin (Kapitalrotation zu klassischen Safe-Havens)?
- Faellt Silber mit Bitcoin (Risk-Off bei allen Assets)?
Nenne immer konkrete EUR-Kurse bei Gold/Silber-Erwaehnungen.

MARKTSTIMMUNG (Crypto Fear & Greed Index, 0-100):
{fear_greed['wert']} ({fear_greed['klassifikation']})

AKTUELLE BITCOIN-NACHRICHTEN (letzte 48 Stunden):
{news_text if news_text else "Keine Nachrichten verfuegbar."}

BEREITS VORHANDENE EREIGNIS-TITEL (diese NICHT nochmal verwenden):
{json.dumps(existing_titles[:30], ensure_ascii=False)}

AUFGABE:
Waehle 3-5 der marktrelevantesten Ereignisse aus den Nachrichten aus
(keine Duplikate zu den vorhandenen Titeln).

Achte dabei auf den Unterschied zwischen einem EREIGNIS und einem
KOMMENTAR darueber. Ein Beschluss, eine Zahl, ein Vollzug ist ein
Ereignis. "Analyst haelt Kurssturz fuer moeglich" ist keines. Nachrichten
ueber Bitcoin bestehen zu einem grossen Teil aus Meinung — nimm die
Meinung nicht als Fakt auf.

WICHTIG: Alle Bitcoin-Preisangaben in EUR. Gold/Silber ebenfalls in EUR.
ETF-Fluesse duerfen in USD bleiben.

Die Kategorie MUSS exakt einer aus dieser Liste sein:
{" | ".join(ERLAUBTE_KATEGORIEN)}
Etwas anderes ist nicht zulaessig — passt nichts, nimm die naechstliegende.

Antworte AUSSCHLIESSLICH mit einem gueltigen JSON-Objekt (kein Markdown,
kein Text davor/danach):

{{
  "neue_ereignisse": [
    {{
      "datum": "{heute}",
      "kategorie": "{ERLAUBTE_KATEGORIEN[0]}",
      "titel": "Kurzer praegnanter Titel",
      "beschreibung": "2-3 Saetze mit konkreten Zahlen. Bitcoin-Kurs in EUR. Gold/Silber erwaehnen wo relevant."
    }}
  ]
}}
"""


# ─── Hauptlogik ─────────────────────────────────────────────────────────────

def main():
    heute = str(date.today())
    print(f"\n=== Bitcoin Ereignisse Update {heute} ===\n")

    daten = load_data()
    print(f"Bestand: {len(daten.get('ereignisse', []))} Ereignisse, "
          f"{len(daten.get('fazits', []))} Fazits\n")

    print("Abrufen: Gold- und Silberkurs...")
    metalle = fetch_precious_metals()

    print("Speichern: edelmetalle.json...")
    update_edelmetalle(metalle, heute)

    # Ohne Tagesfazit haengt die Tagessperre jetzt an den Ereignissen:
    # Gibt es fuer heute schon welche, war der Lauf bereits erfolgreich und
    # ein zweiter Aufruf wuerde nur unnoetig die API kosten.
    vorhandene_ereignis_daten = {e.get("datum") for e in daten.get("ereignisse", [])}
    if heute in vorhandene_ereignis_daten:
        print("Ereignisse für heute bereits vorhanden. Nur edelmetalle.json wurde aktualisiert.")
        sys.exit(0)

    print("Abrufen: BTC- und ETH-Kurs...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed Index...")
    fear_greed = fetch_fear_greed()

    print("Abrufen: Bitcoin-Nachrichten via RSS (48h)...")
    news = fetch_recent_news()

    print("\nAnalyse mit Claude API...")
    prompt = build_prompt(daten, news, preise, fear_greed, metalle, heute)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    result = None
    for versuch in range(3):
        if versuch > 0:
            print(f"  Retry {versuch}/2...")

        message = client.messages.create(
            model=MODELL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()

        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start != -1 and end > start:
            response_text = response_text[start:end]

        try:
            result = json.loads(response_text)
            break
        except json.JSONDecodeError as e:
            print(f"  Versuch {versuch+1}: Ungültiges JSON ({e})", file=sys.stderr)
            print(f"  Response (erste 300 Zeichen): {response_text[:300]}", file=sys.stderr)
            if versuch == 2:
                print("\nFehler: JSON nach 3 Versuchen nicht parsebar.", file=sys.stderr)
                sys.exit(1)

    # ─── Kategorie-Validierung (NEU 13.08.2026) ───────────────────────────
    # Die sieben erlaubten Werte standen bisher nur im Prompt. Ohne Pruefung
    # sind fuenf unerlaubte Kategorien in der Datenbank gelandet.
    vorhandene_titel = {e["titel"] for e in daten.get("ereignisse", [])}
    neue = result.get("neue_ereignisse", [])

    neu_gefiltert = []
    korrigierte_kategorien = []
    unbrauchbar = 0

    for e in neue:
        if not isinstance(e, dict) or not e.get("titel") or not e.get("beschreibung"):
            unbrauchbar += 1
            continue
        if e["titel"] in vorhandene_titel:
            continue

        kategorie = e.get("kategorie")
        if kategorie not in ERLAUBTE_KATEGORIEN:
            korrigierte_kategorien.append((e["titel"], kategorie))
            e["kategorie"] = ERSATZ_KATEGORIE

        # Punkt L: Herkunft an jedem Datensatz, nicht als Markierung an der
        # Wechselstelle — eine Markierung kann vergessen werden.
        e["modell"] = MODELL
        e["prompt_version"] = PROMPT_VERSION
        e["asset"] = ASSET
        e.setdefault("datum", heute)

        neu_gefiltert.append(e)
        vorhandene_titel.add(e["titel"])

    daten.setdefault("ereignisse", [])
    daten["ereignisse"] = neu_gefiltert + daten["ereignisse"]
    daten["ereignisse"] = daten["ereignisse"][:ANZAHL_EREIGNISSE_BEHALTEN]

    # "fazits" bleibt unangetastet: bestehende Historie wird weder geloescht
    # noch ergaenzt. Haiku erstellt seit 13.08.2026 keine Prognosen mehr.
    daten["letzte_aktualisierung"] = heute
    daten["recherche_modell"] = MODELL
    daten["recherche_prompt_version"] = PROMPT_VERSION
    save_data(daten)

    print(f"\n{'='*50}")
    print(f"✓ {len(neu_gefiltert)} neue Ereignisse gespeichert "
          f"({MODELL}, Prompt-Version {PROMPT_VERSION})")

    if korrigierte_kategorien:
        print(f"⚠ {len(korrigierte_kategorien)} unerlaubte Kategorien auf "
              f"'{ERSATZ_KATEGORIE}' korrigiert:", file=sys.stderr)
        for titel, falsch in korrigierte_kategorien:
            print(f"    {falsch!r} bei: {titel}", file=sys.stderr)

    if unbrauchbar:
        print(f"⚠ {unbrauchbar} Einträge ohne Titel oder Beschreibung verworfen",
              file=sys.stderr)

    if not neu_gefiltert:
        # Sichtbar, aber kein Abbruch: an ruhigen Tagen gibt es schlicht
        # nichts Neues, und edelmetalle.json wurde bereits aktualisiert.
        print("Hinweis: keine neuen Ereignisse — entweder nichts Relevantes "
              "in den Nachrichten oder alles schon vorhanden.")
    else:
        print("\nNeue Ereignisse:")
        for e in neu_gefiltert:
            print(f"  [{e['kategorie']}] {e['titel']}")


if __name__ == "__main__":
    main()
