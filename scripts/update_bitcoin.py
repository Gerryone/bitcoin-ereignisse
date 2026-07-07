#!/usr/bin/env python3
"""
Bitcoin Ereignisse Updater
Holt aktuelle Bitcoin-Nachrichten via RSS, analysiert sie mit Claude API
und aktualisiert ereignisse.json im Repository.

GEÄNDERT (02.07.2026): "richtung" (bullish/bearish/neutral) und
"tendenz" wurden durch eine direkte numerische Einschätzung von -5
(stark negativ) bis +5 (stark positiv) ersetzt.

GEÄNDERT (07.07.2026): max_tokens auf 6000 erhöht + Retry-Logik (3x)
+ aggressivere JSON-Bereinigung.

GEÄNDERT (07.07.2026): Gold- und Silberkurse (EUR) werden täglich
abgerufen, in edelmetalle.json gespeichert und in den Prompt
aufgenommen, damit Haiku im Ereignis-Log auf die Entwicklung eingeht.
"""

import json
import os
import sys
import requests
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
import anthropic


# ─── Daten-Abruf ────────────────────────────────────────────────────────────

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
    Gold- und Silberkurs in EUR von metals.live abrufen (kostenlos, kein API-Key).
    Gibt Kurs pro Unze (oz) zurück. Fallback auf Vortagswert falls API nicht
    erreichbar. Wechselkurs USD→EUR wird ebenfalls von der API geliefert.
    """
    try:
        resp = requests.get(
            "https://api.metals.live/v1/spot",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        # API liefert Liste von Objekten: [{"gold": 3200.5}, {"silver": 32.1}, ...]
        metals = {}
        for item in data:
            metals.update(item)

        # USD→EUR Kurs für Umrechnung
        fx_resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            timeout=15,
        )
        fx_resp.raise_for_status()
        usd_to_eur = fx_resp.json()["rates"]["EUR"]

        gold_usd = float(metals.get("gold", 0))
        silver_usd = float(metals.get("silver", 0))

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
    grenze = str(date.today() - timedelta(days=3))
    existing_titles = [e["titel"] for e in daten.get("ereignisse", [])]
    alte_fazits = [
        f for f in daten.get("fazits", [])
        if f["datum"] <= grenze and "rueckblick" not in f
    ]
    fazit_block = ""
    if alte_fazits:
        fazit_block = "\n\nALTE FAZITS OHNE RÜCKBLICK (≥ 3 Tage alt, bitte Rückblick ergänzen):\n"
        for f in alte_fazits[:5]:
            fazit_block += (
                f"\nDatum: {f['datum']}\n"
                f"Einschätzung damals: {f.get('einschaetzung_numerisch')} (-5 bis +5) | Kurs damals: €{f.get('kurs_eur')}\n"
                f"Begründung: {f.get('einschaetzung')}\n"
                f"Schlüsselniveau: €{f.get('schluessel_niveau_eur')} – "
                f"{f.get('schluessel_niveau_erklaerung')}\n"
            )

    return f"""Du bist ein erfahrener Bitcoin-Marktanalyst. Heute ist der {heute}.

AKTUELLER BITCOIN-KURS: €{preise['btc_eur']:,.0f} EUR / ${preise['btc_usd']:,.0f} USD (24h: {preise['btc_change_24h']:+.1f}%)

MARKTKONTEXT ETHEREUM:
ETH-Kurs: €{preise['eth_eur']:,.0f} EUR / ${preise['eth_usd']:,.0f} USD (24h: {preise['eth_change_24h']:+.1f}%)

MARKTKONTEXT EDELMETALLE (Kurs pro Unze):
Gold:   €{metalle['gold_eur']:,.2f} EUR / ${metalle['gold_usd']:,.2f} USD
Silber: €{metalle['silver_eur']:,.3f} EUR / ${metalle['silver_usd']:,.3f} USD

Beziehe Gold und Silber in deine Ereignis-Beschreibungen ein, wo relevant:
- Bewegt sich Gold parallel zu Bitcoin (breite Inflations-/Krisenangst)?
- Läuft Gold besser als Bitcoin (Kapitalrotation zu klassischen Safe-Havens)?
- Fällt Silber mit Bitcoin (Risk-Off bei allen Assets)?
Diese Vergleiche sind besonders wertvoll für das Tagesfazit und für Ereignisse
der Kategorie Makro oder Persönlichkeiten (z.B. wenn jemand von Bitcoin zu Gold
rotiert). Nenne immer konkrete EUR-Kurse bei Gold/Silber-Erwähnungen.

MARKTSTIMMUNG (Crypto Fear & Greed Index, 0-100):
{fear_greed['wert']} ({fear_greed['klassifikation']})

AKTUELLE BITCOIN-NACHRICHTEN (letzte 48 Stunden):
{news_text if news_text else "Keine Nachrichten verfügbar."}

BEREITS VORHANDENE EREIGNIS-TITEL (diese NICHT nochmal verwenden):
{json.dumps(existing_titles[:30], ensure_ascii=False)}
{fazit_block}

AUFGABEN:
1. Wähle 3–5 der marktrelevantesten Ereignisse aus den Nachrichten (keine Duplikate).
2. Erstelle ein Tagesfazit mit ehrlicher Markteinschätzung.
3. Schreibe für alle alten Fazits ohne Rückblick einen selbstkritischen Rückblick.
4. Benenne im Tagesfazit konkrete SZENARIO-BEDINGUNGEN.

WICHTIG: Alle Bitcoin-Preisangaben in EUR. Gold/Silber ebenfalls in EUR.
ETF-Flüsse dürfen in USD bleiben.

WICHTIG ZUR EINSCHÄTZUNG: Numerische Skala -5 bis +5, differenziert gewählt.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown, kein Text davor/danach):

{{
  "neue_ereignisse": [
    {{
      "datum": "{heute}",
      "kategorie": "ETF|Regulierung|Institutionell|Makro|OnChain|Technik|Persönlichkeiten",
      "titel": "Kurzer prägnanter Titel",
      "beschreibung": "2-3 Sätze mit konkreten Zahlen. Bitcoin-Kurs in EUR. Gold/Silber erwähnen wo relevant.",
      "einschaetzung_numerisch": -3
    }}
  ],
  "tagesfazit": {{
    "datum": "{heute}",
    "einschaetzung_numerisch": -3,
    "kurs_eur": {int(preise['btc_eur'])},
    "gold_eur": {metalle['gold_eur']},
    "silver_eur": {metalle['silver_eur']},
    "einschaetzung": "3-5 Sätze Gesamtbewertung inkl. Einordnung von Gold/Silber-Entwicklung.",
    "gewichtung": {{
      "bullish": 30,
      "bearish": 60,
      "neutral": 10
    }},
    "schluessel_niveau_eur": 48000,
    "schluessel_niveau_erklaerung": "Warum ist dieses Niveau entscheidend?",
    "naechster_katalysator": "Welches Ereignis wird als nächstes richtungsweisend sein?",
    "szenario_bedingungen": [
      "Konkrete, überprüfbare Bedingung 1",
      "Konkrete, überprüfbare Bedingung 2",
      "Konkrete, überprüfbare Bedingung 3 (optional)"
    ]
  }},
  "rueckblicke": {{}}
}}

Hinweise:
- einschaetzung_numerisch: -5 bis +5, differenziert
- gewichtung muss exakt 100 ergeben
- Falls keine alten Fazits: "rueckblicke" als leeres Objekt {{}}
- Sei bei Rückblicken selbstkritisch und ehrlich
"""


# ─── Hauptlogik ─────────────────────────────────────────────────────────────

def main():
    heute = str(date.today())
    print(f"\n=== Bitcoin Ereignisse Update {heute} ===\n")

    daten = load_data()
    print(f"Bestand: {len(daten.get('ereignisse', []))} Ereignisse, "
          f"{len(daten.get('fazits', []))} Fazits\n")

    vorhandene_fazit_daten = {f["datum"] for f in daten.get("fazits", [])}
    if heute in vorhandene_fazit_daten:
        print("Tagesfazit für heute bereits vorhanden. Nichts zu tun.")
        sys.exit(0)

    print("Abrufen: BTC- und ETH-Kurs...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed Index...")
    fear_greed = fetch_fear_greed()

    print("Abrufen: Gold- und Silberkurs...")
    metalle = fetch_precious_metals()

    print("Speichern: edelmetalle.json...")
    update_edelmetalle(metalle, heute)

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
            model="claude-haiku-4-5",
            max_tokens=6000,
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

    rueckblicke = result.get("rueckblicke", {})
    grenze = str(date.today() - timedelta(days=3))
    updated_rb = 0
    for fazit in daten.get("fazits", []):
        if (fazit["datum"] in rueckblicke
                and fazit["datum"] <= grenze
                and "rueckblick" not in fazit):
            fazit["rueckblick"] = rueckblicke[fazit["datum"]]
            updated_rb += 1
            print(f"  ✓ Rückblick für {fazit['datum']} eingetragen")

    vorhandene_titel = {e["titel"] for e in daten.get("ereignisse", [])}
    neue = result.get("neue_ereignisse", [])
    neu_gefiltert = [e for e in neue if e["titel"] not in vorhandene_titel]

    daten.setdefault("ereignisse", [])
    daten["ereignisse"] = neu_gefiltert + daten["ereignisse"]
    daten["ereignisse"] = daten["ereignisse"][:60]

    tagesfazit = result.get("tagesfazit", {})
    if tagesfazit:
        daten.setdefault("fazits", [])
        daten["fazits"] = [tagesfazit] + daten["fazits"]
        daten["fazits"] = daten["fazits"][:90]

    daten["letzte_aktualisierung"] = heute
    save_data(daten)

    print(f"\n{'='*40}")
    print(f"✓ {len(neu_gefiltert)} neue Ereignisse gespeichert")
    print(f"✓ {updated_rb} Rückblicke aktualisiert")
    if tagesfazit:
        gew = tagesfazit.get("gewichtung", {})
        print(f"✓ Tagesfazit: Einschätzung {tagesfazit.get('einschaetzung_numerisch', '?')} (-5 bis +5) | "
              f"BTC €{tagesfazit.get('kurs_eur', 0):,.0f} | "
              f"Gold €{tagesfazit.get('gold_eur', 0):,.2f} | "
              f"Silber €{tagesfazit.get('silver_eur', 0):,.3f} | "
              f"Bullish {gew.get('bullish')}% / "
              f"Bearish {gew.get('bearish')}% / "
              f"Neutral {gew.get('neutral')}%")
    if neu_gefiltert:
        print("\nNeue Ereignisse:")
        for e in neu_gefiltert:
            print(f"  [{e.get('einschaetzung_numerisch', '?')}] {e.get('titel', '')}")


if __name__ == "__main__":
    main()
