#!/usr/bin/env python3
"""
Bitcoin Ereignisse Updater
Holt aktuelle Bitcoin-Nachrichten via RSS, analysiert sie mit Claude API
und aktualisiert ereignisse.json im Repository.

GEÄNDERT (02.07.2026): "richtung" (bullish/bearish/neutral) und
"tendenz" wurden durch eine direkte numerische Einschätzung von -5
(stark negativ) bis +5 (stark positiv) ersetzt. Grund: Die Übersetzung
von bullish/bearish/neutral in eine Zahl musste vorher nachträglich
(und notwendigerweise ungenau) in Home Assistant geraten werden -
jetzt liefert Claude die Zahl direkt, feiner abgestuft und ohne
Informationsverlust durch die Zwischenübersetzung.
"""

import json
import os
import sys
import requests
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
import anthropic


# ─── Daten-Abruf ────────────────────────────────────────────────────────────

def fetch_btc_price():
    """Aktuellen BTC-Kurs in EUR und USD von CoinGecko holen."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "eur,usd"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()["bitcoin"]
        eur, usd = data["eur"], data["usd"]
        print(f"  BTC Kurs: €{eur:,.0f} / ${usd:,.0f}")
        return eur, usd
    except Exception as e:
        print(f"  Warnung: Preisabruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return 53000, 61000  # Fallback


def fetch_rss(url, source_name, cutoff_hours=48):
    """RSS-Feed abrufen und Artikel der letzten cutoff_hours Stunden zurückgeben."""
    articles = []
    cutoff = datetime.utcnow() - timedelta(hours=cutoff_hours)
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "content": "http://purl.org/rss/1.0/modules/content/",
        }

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
    """Bitcoin-Nachrichten der letzten 48h aus mehreren RSS-Quellen sammeln."""
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

def build_prompt(daten, news_text, btc_eur, btc_usd, heute):
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

AKTUELLER BITCOIN-KURS: €{btc_eur:,.0f} EUR / ${btc_usd:,.0f} USD

AKTUELLE BITCOIN-NACHRICHTEN (letzte 48 Stunden):
{news_text if news_text else "Keine Nachrichten verfügbar."}

BEREITS VORHANDENE EREIGNIS-TITEL (diese NICHT nochmal verwenden):
{json.dumps(existing_titles[:30], ensure_ascii=False)}
{fazit_block}

AUFGABEN:
1. Wähle 3–5 der marktrelevantesten Ereignisse aus den Nachrichten (keine Duplikate).
2. Erstelle ein Tagesfazit mit ehrlicher Markteinschätzung.
3. Schreibe für alle alten Fazits ohne Rückblick einen selbstkritischen Rückblick.

WICHTIG: Alle Bitcoin-Preisangaben in EUR. ETF-Flüsse dürfen in USD bleiben.

WICHTIG ZUR EINSCHÄTZUNG: Nutze für jedes Ereignis und für das Tagesfazit
eine numerische Skala von -5 (stark negativ/bearish für Bitcoin) bis +5
(stark positiv/bullish für Bitcoin), 0 = neutral. Sei bei der Wahl der
Zahl differenziert - nutze nicht nur die Extremwerte, auch Zwischenwerte
wie -2, +1, +3 etc. sind erwünscht und meistens realistischer als ein
Extremwert.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown, kein Text davor/danach):

{{
  "neue_ereignisse": [
    {{
      "datum": "{heute}",
      "kategorie": "ETF|Regulierung|Institutionell|Makro|OnChain|Technik|Persönlichkeiten",
      "titel": "Kurzer prägnanter Titel",
      "beschreibung": "2-3 Sätze mit konkreten Zahlen. Bitcoin-Kurs immer in EUR.",
      "einschaetzung_numerisch": -3
    }}
  ],
  "tagesfazit": {{
    "datum": "{heute}",
    "einschaetzung_numerisch": -3,
    "kurs_eur": {int(btc_eur)},
    "einschaetzung": "3-5 Sätze Gesamtbewertung. Warum diese Zahl? Welche Faktoren dominieren?",
    "gewichtung": {{
      "bullish": 30,
      "bearish": 60,
      "neutral": 10
    }},
    "schluessel_niveau_eur": 48000,
    "schluessel_niveau_erklaerung": "Warum ist dieses Niveau entscheidend?",
    "naechster_katalysator": "Welches Ereignis wird als nächstes richtungsweisend sein?"
  }},
  "rueckblicke": {{}}
}}

Hinweise:
- einschaetzung_numerisch: -5 bis +5, differenziert gewählt (nicht nur Extremwerte)
- gewichtung muss weiterhin exakt 100 ergeben (bullish + bearish + neutral = 100),
  dient als zusätzliche Kontext-Information neben der Zahl
- Falls keine alten Fazits vorhanden: "rueckblicke" als leeres Objekt {{}}
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

    print("Abrufen: BTC-Kurs...")
    btc_eur, btc_usd = fetch_btc_price()

    print("Abrufen: Bitcoin-Nachrichten via RSS (48h)...")
    news = fetch_recent_news()

    print("\nAnalyse mit Claude API...")
    prompt = build_prompt(daten, news, btc_eur, btc_usd, heute)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"\nFehler: Ungültiges JSON von Claude: {e}", file=sys.stderr)
        print(f"Response (erste 500 Zeichen): {response_text[:500]}", file=sys.stderr)
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
              f"€{tagesfazit.get('kurs_eur', 0):,.0f} | "
              f"Bullish {gew.get('bullish')}% / "
              f"Bearish {gew.get('bearish')}% / "
              f"Neutral {gew.get('neutral')}%")
    if neu_gefiltert:
        print("\nNeue Ereignisse:")
        for e in neu_gefiltert:
            print(f"  [{e.get('einschaetzung_numerisch', '?')}] {e.get('titel', '')}")


if __name__ == "__main__":
    main()
