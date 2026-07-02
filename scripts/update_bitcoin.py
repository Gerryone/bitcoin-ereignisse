#!/usr/bin/env python3
"""
Bitcoin Ereignisse Updater
Holt aktuelle Bitcoin-Nachrichten, analysiert sie mit Claude API
und aktualisiert ereignisse.json im Repository.
"""

import json
import os
import sys
import requests
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


def fetch_recent_news():
    """Aktuelle Bitcoin-Nachrichten (letzte 48h) von CryptoCompare holen."""
    ts_48h = int((datetime.utcnow() - timedelta(hours=48)).timestamp())
    try:
        resp = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/",
            params={"lang": "EN", "categories": "BTC", "lTs": ts_48h},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        articles = resp.json().get("Data", [])[:30]

        lines = []
        for a in articles:
            ts = datetime.utcfromtimestamp(a.get("published_on", 0))
            pub = ts.strftime("%Y-%m-%d %H:%M UTC")
            title = a.get("title", "").strip()
            source = a.get("source_info", {}).get("name", a.get("source", ""))
            body = (a.get("body") or "")[:600].strip()
            lines.append(f"[{pub}] {source}: {title}")
            if body:
                lines.append(f"  → {body}")
            lines.append("")

        print(f"  {len(articles)} Artikel geladen")
        return "\n".join(lines)

    except Exception as e:
        print(f"  Warnung: News-Abruf fehlgeschlagen ({e})", file=sys.stderr)
        return ""


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

    # Bestehende Titel für Deduplizierung
    existing_titles = [e["titel"] for e in daten.get("ereignisse", [])]

    # Alte Fazits ohne Rückblick
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
                f"Tendenz: {f.get('tendenz')} | Kurs damals: €{f.get('kurs_eur')}\n"
                f"Einschätzung: {f.get('einschaetzung')}\n"
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

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown, kein Text davor/danach):

{{
  "neue_ereignisse": [
    {{
      "datum": "{heute}",
      "kategorie": "ETF|Regulierung|Institutionell|Makro|OnChain|Technik|Persönlichkeiten",
      "titel": "Kurzer prägnanter Titel",
      "beschreibung": "2-3 Sätze mit konkreten Zahlen. Bitcoin-Kurs immer in EUR.",
      "richtung": "bullish|bearish|neutral"
    }}
  ],
  "tagesfazit": {{
    "datum": "{heute}",
    "tendenz": "bullish|bearish|neutral",
    "kurs_eur": {int(btc_eur)},
    "einschaetzung": "3-5 Sätze Gesamtbewertung. Warum diese Tendenz? Welche Faktoren dominieren?",
    "gewichtung": {{
      "bullish": 30,
      "bearish": 60,
      "neutral": 10
    }},
    "schluessel_niveau_eur": 48000,
    "schluessel_niveau_erklaerung": "Warum ist dieses Niveau entscheidend?",
    "naechster_katalysator": "Welches Ereignis wird als nächstes richtungsweisend sein?"
  }},
  "rueckblicke": {{
    "YYYY-MM-DD": {{
      "kurs_danach_eur": 50000,
      "tendenz_korrekt": true,
      "was_richtig": "Was an der damaligen Einschätzung korrekt war.",
      "was_falsch": "Was falsch eingeschätzt wurde.",
      "lerneffekt": "Was daraus gelernt werden kann.",
      "gewichtungs_anpassung": "Wie zukünftige Gewichtungen angepasst werden sollten."
    }}
  }}
}}

Hinweise:
- gewichtung muss immer exakt 100 ergeben (bullish + bearish + neutral = 100)
- Falls keine alten Fazits vorhanden: "rueckblicke" als leeres Objekt {{}}
- Sei bei Rückblicken selbstkritisch und ehrlich – das verbessert die Methodik
"""


# ─── Hauptlogik ─────────────────────────────────────────────────────────────

def main():
    heute = str(date.today())
    print(f"\n=== Bitcoin Ereignisse Update {heute} ===\n")

    # Daten laden
    daten = load_data()
    print(f"Bestand: {len(daten.get('ereignisse', []))} Ereignisse, "
          f"{len(daten.get('fazits', []))} Fazits\n")

    # Prüfen ob Tagesfazit schon existiert
    vorhandene_fazit_daten = {f["datum"] for f in daten.get("fazits", [])}
    if heute in vorhandene_fazit_daten:
        print("Tagesfazit für heute bereits vorhanden. Nichts zu tun.")
        sys.exit(0)

    # Kurs und Nachrichten holen
    print("Abrufen: BTC-Kurs...")
    btc_eur, btc_usd = fetch_btc_price()

    print("Abrufen: Bitcoin-Nachrichten (48h)...")
    news = fetch_recent_news()

    # Claude API aufrufen
    print("\nAnalyse mit Claude API...")
    prompt = build_prompt(daten, news, btc_eur, btc_usd, heute)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # JSON aus Response extrahieren (falls in Markdown eingebettet)
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

    # Rückblicke in alte Fazits eintragen
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

    # Neue Ereignisse hinzufügen (Duplikate filtern)
    vorhandene_titel = {e["titel"] for e in daten.get("ereignisse", [])}
    neue = result.get("neue_ereignisse", [])
    neu_gefiltert = [e for e in neue if e["titel"] not in vorhandene_titel]

    daten.setdefault("ereignisse", [])
    daten["ereignisse"] = neu_gefiltert + daten["ereignisse"]
    daten["ereignisse"] = daten["ereignisse"][:60]  # Max. 60 Einträge

    # Tagesfazit einfügen
    tagesfazit = result.get("tagesfazit", {})
    if tagesfazit:
        daten.setdefault("fazits", [])
        daten["fazits"] = [tagesfazit] + daten["fazits"]
        daten["fazits"] = daten["fazits"][:90]  # Max. 90 Einträge

    daten["letzte_aktualisierung"] = heute

    # Speichern
    save_data(daten)

    # Zusammenfassung ausgeben
    print(f"\n{'='*40}")
    print(f"✓ {len(neu_gefiltert)} neue Ereignisse gespeichert")
    print(f"✓ {updated_rb} Rückblicke aktualisiert")
    if tagesfazit:
        gew = tagesfazit.get("gewichtung", {})
        print(f"✓ Tagesfazit: {tagesfazit.get('tendenz', '?').upper()} | "
              f"€{tagesfazit.get('kurs_eur', 0):,.0f} | "
              f"Bullish {gew.get('bullish')}% / "
              f"Bearish {gew.get('bearish')}% / "
              f"Neutral {gew.get('neutral')}%")
    if neu_gefiltert:
        print("\nNeue Ereignisse:")
        for e in neu_gefiltert:
            print(f"  [{e.get('richtung', '?'):7}] {e.get('titel', '')}")


if __name__ == "__main__":
    main()
