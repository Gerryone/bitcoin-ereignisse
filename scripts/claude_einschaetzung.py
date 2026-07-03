#!/usr/bin/env python3
"""
Claude (Sonnet) Bitcoin-Einschätzung
Liest die bestehende ereignisse.json (inkl. Haikus neuestem Tagesfazit),
erstellt eine UNABHÄNGIGE Einschätzung sowie einen ERGÄNZENDEN KOMMENTAR
zu Haikus Fazit, und speichert beides strukturiert in claude_fazit.json.

WICHTIG ZUR EINORDNUNG:
Beide Texte sind von einem Sprachmodell erzeugte Einschätzungen, keine
verlässlichen Prognosen. Der Sinn dieses Skripts ist NICHT, eine bessere
Vorhersage als Haiku zu liefern, sondern eine zweite, unabhängige
Perspektive bereitzustellen, die sich im Rückblick mit der tatsächlichen
Kursentwicklung vergleichen lässt (siehe rueckblick-Felder in der
Home-Assistant-Datenbank, die diese Datei später einliest).
"""

import json
import os
import sys
import requests
from datetime import date, datetime
import anthropic


def fetch_crypto_prices():
    """Aktuellen BTC- und ETH-Kurs in EUR sowie deren 24h-Änderung von
    CoinGecko holen, als Kontext für den Prompt (siehe build_prompt)."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "eur",
                "include_24hr_change": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        btc = data["bitcoin"]
        eth = data["ethereum"]
        return {
            "btc_eur": btc["eur"], "btc_change_24h": btc.get("eur_24h_change", 0),
            "eth_eur": eth["eur"], "eth_change_24h": eth.get("eur_24h_change", 0),
        }
    except Exception as e:
        print(f"  Warnung: Preisabruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return {"btc_eur": 53000, "btc_change_24h": 0, "eth_eur": 1450, "eth_change_24h": 0}


def fetch_fear_greed():
    """Aktuellen Crypto Fear & Greed Index von alternative.me holen
    (kostenlos, kein API-Key nötig), als Kontext für den Prompt."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {"wert": int(data["value"]), "klassifikation": data.get("value_classification", "unbekannt")}
    except Exception as e:
        print(f"  Warnung: Fear&Greed-Abruf fehlgeschlagen ({e}), nutze Fallback", file=sys.stderr)
        return {"wert": 50, "klassifikation": "Neutral (Fallback)"}


def load_ereignisse(path="ereignisse.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_claude_fazit(path="claude_fazit.json"):
    """Lädt die bisherige Historie unserer eigenen Fazits, falls vorhanden.
    Wird gebraucht, damit Sonnet die eigene Vorgeschichte sieht und nicht
    bei jedem Tag komplett neu anfängt."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"fazits": []}


def save_claude_fazit(daten, path="claude_fazit.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


def build_prompt(ereignisse_daten, eigene_historie, preise, fear_greed, heute):
    """Baut den Prompt für Sonnet: aktuelle Ereignisse + Haikus Fazit
    + Ethereum-Kontext + unsere eigene bisherige Einschätzungs-Historie
    (für Kontinuität)."""

    heutige_ereignisse = [
        e for e in ereignisse_daten.get("ereignisse", [])
        if e.get("datum") == heute
    ]
    haiku_fazit_heute = next(
        (f for f in ereignisse_daten.get("fazits", []) if f.get("datum") == heute),
        None
    )

    ereignis_text = "\n".join(
        f"- [Einschätzung: {e.get('einschaetzung_numerisch', e.get('richtung', '?'))}] {e.get('kategorie', '')}: {e.get('titel', '')}\n"
        f"  {e.get('beschreibung', '')}"
        for e in heutige_ereignisse
    ) or "Keine neuen Ereignisse für heute vorhanden."

    haiku_text = "Kein Haiku-Fazit für heute vorhanden."
    if haiku_fazit_heute:
        gew = haiku_fazit_heute.get("gewichtung", {})
        einschaetzung_zahl = haiku_fazit_heute.get("einschaetzung_numerisch", haiku_fazit_heute.get("tendenz"))
        haiku_text = (
            f"Einschätzung (-5 bis +5): {einschaetzung_zahl}\n"
            f"Kurs: €{haiku_fazit_heute.get('kurs_eur')}\n"
            f"Begründung: {haiku_fazit_heute.get('einschaetzung')}\n"
            f"Gewichtung: Bullish {gew.get('bullish')}% / "
            f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%\n"
            f"Schlüsselniveau: €{haiku_fazit_heute.get('schluessel_niveau_eur')} - "
            f"{haiku_fazit_heute.get('schluessel_niveau_erklaerung')}\n"
            f"Nächster Katalysator: {haiku_fazit_heute.get('naechster_katalysator')}"
        )

    # Eigene Historie der letzten Tage einbeziehen, für Kontinuität
    letzte_eigene = eigene_historie.get("fazits", [])[:5]
    eigene_historie_text = ""
    if letzte_eigene:
        eigene_historie_text = "\n\nDEINE EIGENEN LETZTEN EINSCHÄTZUNGEN (für Kontinuität, prüfe ob sich etwas bestätigt oder nicht):\n"
        for f in letzte_eigene:
            eigene_historie_text += (
                f"\nDatum: {f.get('datum')}\n"
                f"Deine Einschätzung damals (-5 bis +5): {f.get('eigene_einschaetzung_numerisch', f.get('eigene_tendenz'))}\n"
                f"Deine Begründung: {f.get('eigene_einschaetzung', '')[:200]}...\n"
            )

    return f"""Du bist ein unabhängiger Bitcoin-Marktanalyst. Heute ist der {heute}.

AKTUELLER BITCOIN-KURS: €{preise['btc_eur']:,.0f} EUR (24h: {preise['btc_change_24h']:+.1f}%)

MARKTKONTEXT ETHEREUM (zweitgrößte Kryptowährung, oft aber nicht immer
mit Bitcoin korreliert - Ethereum hat auch eigene Treiber wie
Foundation-Entscheidungen, Layer-2-Wachstum, Staking-Anteil):
ETH-Kurs: €{preise['eth_eur']:,.0f} EUR (24h: {preise['eth_change_24h']:+.1f}%)
Beziehe diesen Kontext ein, wo relevant - z.B. ob BTC und ETH sich
aktuell im Gleichschritt bewegen (deutet auf breite Markt-/
Risikostimmung hin) oder auseinanderlaufen (deutet auf Bitcoin-
spezifische statt allgemeine Krypto-Faktoren hin).

MARKTSTIMMUNG (Crypto Fear & Greed Index, 0-100):
{fear_greed['wert']} ({fear_greed['klassifikation']})
Extreme Werte (unter 20 oder über 80) werden von manchen als
Kontraindikator gedeutet (extreme Angst als möglicher Boden, extreme
Gier als möglicher Warnhinweis) - kein verlässliches Signal für sich
allein, nur als ein Faktor unter mehreren einbeziehen.

HEUTIGE BITCOIN-EREIGNISSE (von einem anderen KI-System recherchiert):
{ereignis_text}

TAGESFAZIT EINES ANDEREN KI-SYSTEMS (Claude Haiku) FÜR HEUTE:
{haiku_text}
{eigene_historie_text}

AUFGABEN:
1. Bilde deine EIGENE, UNABHÄNGIGE Einschätzung der Marktlage basierend
   auf den obigen Ereignissen - komm zu deinem eigenen Schluss, auch wenn
   er von Haikus Fazit abweicht. Sei ehrlich, auch wenn deine Einschätzung
   unsicher oder gemischt ist.
2. Schreibe zusätzlich einen ERGÄNZENDEN KOMMENTAR zu Haikus Fazit: Wo
   stimmst du zu? Wo bist du anderer Meinung? Was würdest du ergänzen oder
   anders gewichten? Sei konkret und konstruktiv-kritisch, nicht nur
   zustimmend.

WICHTIG ZUR EINSCHÄTZUNG: Nutze eine numerische Skala von -5 (stark
negativ/bearish) bis +5 (stark positiv/bullish), 0 = neutral. Sei
differenziert - nutze auch Zwischenwerte wie -2, +1, +3.

WICHTIG: Das ist KEINE verlässliche Kursprognose, sondern eine
Markteinordnung basierend auf öffentlich verfügbaren Nachrichten. Bitcoin
ist hochvolatil und nachrichtengetrieben - sei entsprechend vorsichtig in
der Formulierung, vermeide übertriebene Sicherheit.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown,
kein Text davor/danach):

{{
  "eigene_einschaetzung_numerisch": -3,
  "eigene_einschaetzung": "3-5 Sätze deine unabhängige Markteinschätzung",
  "eigene_gewichtung": {{
    "bullish": 30,
    "bearish": 60,
    "neutral": 10
  }},
  "kommentar_zu_haiku": "3-5 Sätze: wo stimmst du zu, wo widersprichst du, was ergänzt du?",
  "uebereinstimmung_mit_haiku": "hoch|mittel|niedrig"
}}

Hinweis: eigene_gewichtung muss immer exakt 100 ergeben (bullish + bearish + neutral = 100),
dient als zusätzliche Kontext-Information neben der Zahl.
"""


def main():
    heute = str(date.today())
    print(f"\n=== Claude (Sonnet) Bitcoin-Einschätzung {heute} ===\n")

    ereignisse_daten = load_ereignisse()
    eigene_historie = load_claude_fazit()

    # Prüfen ob heutige Einschätzung schon existiert
    vorhandene_daten = {f["datum"] for f in eigene_historie.get("fazits", [])}
    if heute in vorhandene_daten:
        print("Eigene Einschätzung für heute bereits vorhanden. Nichts zu tun.")
        sys.exit(0)

    print("Abrufen: BTC- und ETH-Kurs...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed Index...")
    fear_greed = fetch_fear_greed()

    print("Baue Prompt mit heutigen Ereignissen + Haikus Fazit + ETH-Kontext...")
    prompt = build_prompt(ereignisse_daten, eigene_historie, preise, fear_greed, heute)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
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

    # Kurs zum Zeitpunkt für spätere Rückblicke mitspeichern
    haiku_fazit_heute = next(
        (f for f in ereignisse_daten.get("fazits", []) if f.get("datum") == heute),
        None
    )
    kurs_eur = haiku_fazit_heute.get("kurs_eur") if haiku_fazit_heute else preise.get("btc_eur")

    neuer_eintrag = {
        "datum": heute,
        "kurs_eur": kurs_eur,
        "eigene_einschaetzung_numerisch": result.get("eigene_einschaetzung_numerisch"),
        "eigene_einschaetzung": result.get("eigene_einschaetzung"),
        "eigene_gewichtung": result.get("eigene_gewichtung"),
        "kommentar_zu_haiku": result.get("kommentar_zu_haiku"),
        "uebereinstimmung_mit_haiku": result.get("uebereinstimmung_mit_haiku"),
        "haiku_einschaetzung_zum_vergleich": (
            haiku_fazit_heute.get("einschaetzung_numerisch", haiku_fazit_heute.get("tendenz"))
            if haiku_fazit_heute else None
        ),
        "erstellt_am": datetime.now().isoformat(),
    }

    eigene_historie.setdefault("fazits", [])
    eigene_historie["fazits"] = [neuer_eintrag] + eigene_historie["fazits"]
    eigene_historie["fazits"] = eigene_historie["fazits"][:90]
    eigene_historie["letzte_aktualisierung"] = heute

    save_claude_fazit(eigene_historie)

    print(f"\n{'='*40}")
    print(f"✓ Eigene Einschätzung gespeichert: {result.get('eigene_einschaetzung_numerisch', '?')} (-5 bis +5)")
    print(f"✓ Übereinstimmung mit Haiku: {result.get('uebereinstimmung_mit_haiku', '?')}")
    gew = result.get("eigene_gewichtung", {})
    print(f"✓ Eigene Gewichtung: Bullish {gew.get('bullish')}% / "
          f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%")


if __name__ == "__main__":
    main()
