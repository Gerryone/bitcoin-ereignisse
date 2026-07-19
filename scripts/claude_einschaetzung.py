#!/usr/bin/env python3
"""
Claude (Sonnet) Bitcoin-Einschätzung
Liest die bestehende ereignisse.json (inkl. Haikus neuestem Tagesfazit),
führt eine KRITISCHE PRÜFUNG (Red-Team-Check) von Haikus Fazit durch und
bildet daraus eine eigene, unabhängige Einschätzung. Speichert alles
strukturiert in claude_fazit.json.

WICHTIG ZUR EINORDNUNG:
Beide Texte sind von einem Sprachmodell erzeugte Einschätzungen, keine
verlässlichen Prognosen.

GEÄNDERT (19.07.2026): Der bisherige Ansatz ("ergänzender Kommentar zu
Haikus Fazit") führte in der Praxis überwiegend zu Zustimmung statt
echter Gegenprüfung - zwei Modelle, die dieselben Daten sehen, kamen
fast immer zum selben (und ähnlich falschen) Ergebnis, ohne echten
Erkenntnisgewinn gegenüber einer einzelnen Analyse. Umgebaut zu einer
expliziten Red-Team-Rolle: Claude sucht aktiv nach Schwachstellen,
Verzerrungen und Gegenargumenten in Haikus Analyse, bevor es zur
eigenen Einschätzung kommt - Zustimmung ist weiterhin möglich, muss
aber die Kritik explizit entkräften statt sie zu ignorieren.

Außerdem: eigener Trefferquote-Feedback-Mechanismus (analog zu
update_bitcoin.py) - Claude bekommt bei jedem Lauf die eigene bisherige
Treffergenauigkeit objektiv vorgerechnet, um sich zu kalibrieren.
"""

import json
import os
import sys
import time
import requests
from datetime import date, datetime, timedelta
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
    """Lädt die bisherige Historie unserer eigenen Fazits, falls vorhanden."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"fazits": []}


def save_claude_fazit(daten, path="claude_fazit.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


# ─── Trefferquote-Feedback: objektive Nachberechnung (NEU 19.07.2026) ──────
# Analog zu update_bitcoin.py: Claude bekommt bei jedem Lauf die eigene
# bisherige Treffergenauigkeit objektiv vorgerechnet (via CoinGecko-
# Historiendaten, nicht durch Selbsteinschätzung), um sich zu kalibrieren
# statt jeden Tag isoliert neu zu urteilen.

def fetch_historical_btc_price_eur(datum_str):
    """Holt den historischen BTC-Kurs in EUR für ein Datum über CoinGecko."""
    try:
        dt = datetime.strptime(datum_str, "%Y-%m-%d")
        coingecko_datum = dt.strftime("%d-%m-%Y")

        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/history",
            params={"date": coingecko_datum, "localization": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        preis_eur = data["market_data"]["current_price"]["eur"]
        return float(preis_eur)
    except Exception as e:
        print(f"    Warnung: historischer Kurs für {datum_str} nicht abrufbar ({e})", file=sys.stderr)
        return None


def berechne_eigene_trefferquote(eigene_historie, heute_str):
    """
    Geht die eigene Fazit-Historie durch, berechnet für Einträge ≥7 Tage
    ohne Rückblick den tatsächlichen Kursverlauf via CoinGecko und
    speichert das Ergebnis dauerhaft. Gibt die letzten 10 ausgewerteten
    Einträge + Gesamt-Trefferquote für den Prompt zurück.
    """
    heute = datetime.strptime(heute_str, "%Y-%m-%d").date()
    MAX_NACHBERECHNUNGEN_PRO_LAUF = 5
    nachberechnet = 0

    for eintrag in eigene_historie.get("fazits", []):
        if nachberechnet >= MAX_NACHBERECHNUNGEN_PRO_LAUF:
            break

        if "rueckblick_prozent_7_tage" in eintrag:
            continue

        eintrag_datum_str = eintrag.get("datum")
        if not eintrag_datum_str:
            continue
        try:
            eintrag_datum = datetime.strptime(eintrag_datum_str, "%Y-%m-%d").date()
        except Exception:
            continue

        if (heute - eintrag_datum).days < 7:
            continue

        start_kurs = eintrag.get("kurs_eur")
        if not start_kurs:
            continue

        ziel_datum_str = (eintrag_datum + timedelta(days=7)).strftime("%Y-%m-%d")
        ziel_kurs = fetch_historical_btc_price_eur(ziel_datum_str)
        nachberechnet += 1
        time.sleep(2)

        if ziel_kurs is None:
            continue

        prozent = round(((ziel_kurs - start_kurs) / start_kurs) * 100, 2)
        eintrag["rueckblick_prozent_7_tage"] = prozent
        eintrag["rueckblick_kurs_7_tage"] = ziel_kurs
        print(f"    ✓ Trefferquote-Nachberechnung {eintrag_datum_str}: {prozent:+.2f}%")

    ausgewertete = [
        f for f in eigene_historie.get("fazits", [])
        if "rueckblick_prozent_7_tage" in f and f.get("eigene_einschaetzung_numerisch") is not None
    ]
    ausgewertete.sort(key=lambda f: f["datum"], reverse=True)
    letzte_10 = ausgewertete[:10]

    treffer = 0
    for f in letzte_10:
        einschaetzung = f["eigene_einschaetzung_numerisch"]
        prozent = f["rueckblick_prozent_7_tage"]
        richtung_erwartet = 1 if einschaetzung > 0.3 else (-1 if einschaetzung < -0.3 else 0)
        richtung_tatsaechlich = 1 if prozent > 1.0 else (-1 if prozent < -1.0 else 0)
        if richtung_erwartet == richtung_tatsaechlich:
            treffer += 1

    trefferquote_prozent = round((treffer / len(letzte_10)) * 100, 0) if letzte_10 else None

    return eigene_historie, letzte_10, trefferquote_prozent


def formatiere_trefferquote_block(letzte_eintraege, trefferquote_prozent):
    """Baut den Prompt-Abschnitt mit der eigenen bisherigen Trefferquote."""
    if not letzte_eintraege:
        return (
            "\n\nDEINE BISHERIGE TREFFERQUOTE: Noch keine ausgewerteten "
            "eigenen Einschätzungen vorhanden (erste ~7 Tage nach Start)."
        )

    zeilen = []
    for f in letzte_eintraege:
        einschaetzung = f["eigene_einschaetzung_numerisch"]
        prozent = f["rueckblick_prozent_7_tage"]
        richtung_erwartet = "bullish" if einschaetzung > 0.3 else ("bearish" if einschaetzung < -0.3 else "neutral")
        richtung_tatsaechlich = "gestiegen" if prozent > 1.0 else ("gefallen" if prozent < -1.0 else "seitwärts")
        treffer_symbol = "✓" if (
            (einschaetzung > 0.3 and prozent > 1.0) or
            (einschaetzung < -0.3 and prozent < -1.0) or
            (-0.3 <= einschaetzung <= 0.3 and -1.0 <= prozent <= 1.0)
        ) else "✗"
        zeilen.append(
            f"  {f['datum']}: Deine Einschätzung {einschaetzung:+.1f} ({richtung_erwartet}) "
            f"→ tatsächlich {prozent:+.2f}% ({richtung_tatsaechlich}) {treffer_symbol}"
        )

    quote_text = f"{trefferquote_prozent:.0f}%" if trefferquote_prozent is not None else "n/a"

    return f"""

DEINE BISHERIGE TREFFERQUOTE (letzte {len(letzte_eintraege)} ausgewertete eigene Einschätzungen, 7-Tage-Richtung):
{chr(10).join(zeilen)}

Deine aktuelle Trefferquote: {quote_text} (Richtung richtig vs. falsch gelegen)

WICHTIG ZUR KALIBRIERUNG: Das ist deine eigene, objektiv nachgerechnete
Bilanz - nicht Haikus. Prüfe kritisch, ob du selbst ein wiederkehrendes
Fehlermuster hast (z.B. systematisch zu pessimistisch, Überreaktion auf
bestimmte Nachrichtentypen). Nutze das aktiv zur Kalibrierung deiner
heutigen Einschätzung."""


def build_prompt(ereignisse_daten, eigene_historie, preise, fear_greed, heute,
                  trefferquote_block):
    """Baut den Prompt für Sonnet: aktuelle Ereignisse + Haikus Fazit als
    KRITISCH ZU PRÜFENDE Analyse + eigene Trefferquote-Bilanz."""

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

    letzte_eigene = eigene_historie.get("fazits", [])[:5]
    eigene_historie_text = ""
    if letzte_eigene:
        eigene_historie_text = "\n\nDEINE EIGENEN LETZTEN EINSCHÄTZUNGEN (für Kontinuität):\n"
        for f in letzte_eigene:
            eigene_historie_text += (
                f"\nDatum: {f.get('datum')}\n"
                f"Deine Einschätzung damals (-5 bis +5): {f.get('eigene_einschaetzung_numerisch', f.get('eigene_tendenz'))}\n"
                f"Deine Begründung: {f.get('eigene_einschaetzung', '')[:200]}...\n"
            )

    return f"""Du bist ein unabhängiger Bitcoin-Marktanalyst mit einer speziellen Aufgabe:
DU BIST DIE GEGENPRÜFUNG. Heute ist der {heute}.

DEINE ROLLE IST NICHT, Haikus Fazit zu bestätigen oder freundlich zu
kommentieren. Zwei KI-Systeme, die dieselben Daten sehen und ähnlich zu
ähnlichen Schlüssen kommen, bringen keinen Erkenntnisgewinn - das ist in
der Vergangenheit genau so passiert und hat sich als wenig wertvoll
erwiesen. Dein Job ist es, aktiv als Red Team zu arbeiten: die
Schwachstellen, blinden Flecken und Verzerrungen in Haikus Analyse zu
finden, BEVOR du zu deiner eigenen Einschätzung kommst.

AKTUELLER BITCOIN-KURS: €{preise['btc_eur']:,.0f} EUR (24h: {preise['btc_change_24h']:+.1f}%)

MARKTKONTEXT ETHEREUM:
ETH-Kurs: €{preise['eth_eur']:,.0f} EUR (24h: {preise['eth_change_24h']:+.1f}%)
Beziehe diesen Kontext ein, wo relevant.

MARKTSTIMMUNG (Crypto Fear & Greed Index, 0-100):
{fear_greed['wert']} ({fear_greed['klassifikation']})

HEUTIGE BITCOIN-EREIGNISSE (von einem anderen KI-System recherchiert):
{ereignis_text}

TAGESFAZIT EINES ANDEREN KI-SYSTEMS (Claude Haiku) FÜR HEUTE - DIES IST
DAS OBJEKT DEINER KRITISCHEN PRÜFUNG:
{haiku_text}
{eigene_historie_text}{trefferquote_block}

AUFGABEN (in dieser Reihenfolge):

1. KRITISCHE PRÜFUNG (Pflicht, unabhängig vom Ergebnis): Suche aktiv nach
   mindestens 2-3 konkreten Schwachstellen in Haikus Fazit. Mögliche
   Ansatzpunkte: Überreaktion auf ein einzelnes Ereignis statt
   Gesamtbild? Ignorierte Gegenindikatoren (z.B. positive Signale bei
   insgesamt bearishem Fazit oder umgekehrt)? Bestätigungsfehler
   (werden nur Ereignisse genannt, die zur vorgefassten Richtung
   passen)? Fehlende Berücksichtigung von Basiswahrscheinlichkeiten
   (wie oft bewegt sich Bitcoin überhaupt in die behauptete Richtung)?
   Zu starke Extrapolation aus kurzfristigen Nachrichten auf
   mittelfristige Kursbewegung? Benenne die Schwachstellen konkret,
   nicht pauschal.

2. STÄRKSTES GEGENARGUMENT: Formuliere das stärkste Argument GEGEN
   Haikus Einschätzung, auch wenn du am Ende zu einem ähnlichen Schluss
   kommst - das Argument muss ernsthaft und nicht als Strohmann
   formuliert sein.

3. EIGENE, UNABHÄNGIGE EINSCHÄTZUNG: Bilde deine eigene Einschätzung
   basierend auf den Rohereignissen UND deiner Kritik aus Schritt 1-2.
   Falls du am Ende zu einer ähnlichen Richtung wie Haiku kommst, MUSST
   du explizit begründen, warum die von dir gefundene Kritik die
   Gesamteinschätzung nicht kippt - reine Zustimmung ohne diese
   Begründung ist nicht zulässig. Berücksichtige aktiv deine eigene
   bisherige Trefferquote (siehe oben) zur Kalibrierung.

4. SZENARIO-BEDINGUNGEN: Welche 2-4 konkreten, überprüfbaren
   Ereignisse/Entwicklungen müssten eintreten, damit sich deine
   Einschätzung bestätigt? Keine Zeitprognose, sondern überprüfbare
   Auslöser für den späteren Rückblick.

WICHTIG ZUR EINSCHÄTZUNG: Numerische Skala -5 (stark negativ/bearish)
bis +5 (stark positiv/bullish), 0 = neutral. Differenziert wählen.

WICHTIG: Das ist KEINE verlässliche Kursprognose, sondern eine
Markteinordnung. Bitcoin ist hochvolatil und nachrichtengetrieben - sei
entsprechend vorsichtig in der Formulierung.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown,
kein Text davor/danach):

{{
  "identifizierte_schwachstellen": [
    "Konkrete Schwachstelle 1 in Haikus Analyse",
    "Konkrete Schwachstelle 2 in Haikus Analyse"
  ],
  "staerkstes_gegenargument": "Das stärkste ernsthafte Argument gegen Haikus Fazit, 2-3 Sätze",
  "eigene_einschaetzung_numerisch": -3,
  "eigene_einschaetzung": "3-5 Sätze deine unabhängige Markteinschätzung",
  "eigene_gewichtung": {{
    "bullish": 30,
    "bearish": 60,
    "neutral": 10
  }},
  "begruendung_bei_uebereinstimmung": "Falls deine Einschätzung in eine ähnliche Richtung wie Haiku geht: warum kippt die gefundene Kritik das Gesamtbild nicht? Falls deine Einschätzung deutlich abweicht: leer lassen oder kurz bestätigen, dass keine Übereinstimmung vorliegt.",
  "szenario_bedingungen": [
    "Konkrete, überprüfbare Bedingung 1",
    "Konkrete, überprüfbare Bedingung 2",
    "Konkrete, überprüfbare Bedingung 3 (optional)"
  ]
}}

Hinweis: eigene_gewichtung muss immer exakt 100 ergeben (bullish + bearish + neutral = 100).
"""


def main():
    heute = str(date.today())
    print(f"\n=== Claude (Sonnet) Bitcoin-Einschätzung {heute} ===\n")

    ereignisse_daten = load_ereignisse()
    eigene_historie = load_claude_fazit()

    vorhandene_daten = {f["datum"] for f in eigene_historie.get("fazits", [])}
    if heute in vorhandene_daten:
        print("Eigene Einschätzung für heute bereits vorhanden. Nichts zu tun.")
        sys.exit(0)

    print("Berechne eigene Trefferquote (bisherige Einschätzungen vs. Realität)...")
    eigene_historie, letzte_eintraege, trefferquote_prozent = berechne_eigene_trefferquote(eigene_historie, heute)
    save_claude_fazit(eigene_historie)  # Nachberechnete Rückblicke sofort sichern
    trefferquote_block = formatiere_trefferquote_block(letzte_eintraege, trefferquote_prozent)
    if trefferquote_prozent is not None:
        print(f"  ✓ Eigene aktuelle Trefferquote: {trefferquote_prozent:.0f}% ({len(letzte_eintraege)} Auswertungen)")
    else:
        print("  Noch keine ausgewerteten eigenen Einschätzungen vorhanden.")

    print("Abrufen: BTC- und ETH-Kurs...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed Index...")
    fear_greed = fetch_fear_greed()

    print("Baue Prompt mit heutigen Ereignissen + Haikus Fazit (als kritisch zu prüfende Analyse) + Trefferquote...")
    prompt = build_prompt(ereignisse_daten, eigene_historie, preise, fear_greed, heute, trefferquote_block)

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

    haiku_fazit_heute = next(
        (f for f in ereignisse_daten.get("fazits", []) if f.get("datum") == heute),
        None
    )
    kurs_eur = haiku_fazit_heute.get("kurs_eur") if haiku_fazit_heute else preise.get("btc_eur")

    haiku_einschaetzung = (
        haiku_fazit_heute.get("einschaetzung_numerisch", haiku_fazit_heute.get("tendenz"))
        if haiku_fazit_heute else None
    )

    # Übereinstimmung wird jetzt OBJEKTIV aus der Zahlendifferenz berechnet,
    # statt das Modell selbst einschätzen zu lassen (GEÄNDERT 19.07.2026 -
    # vorher konnte das Modell "hoch/mittel/niedrig" frei wählen, was der
    # Tendenz zur Zustimmung noch zusätzlich Raum gab).
    eigene_zahl = result.get("eigene_einschaetzung_numerisch")
    uebereinstimmung = "unbekannt"
    if eigene_zahl is not None and haiku_einschaetzung is not None:
        try:
            diff = abs(float(eigene_zahl) - float(haiku_einschaetzung))
            if diff <= 1.0:
                uebereinstimmung = "hoch"
            elif diff <= 2.5:
                uebereinstimmung = "mittel"
            else:
                uebereinstimmung = "niedrig"
        except Exception:
            pass

    neuer_eintrag = {
        "datum": heute,
        "kurs_eur": kurs_eur,
        "identifizierte_schwachstellen": result.get("identifizierte_schwachstellen", []),
        "staerkstes_gegenargument": result.get("staerkstes_gegenargument"),
        "eigene_einschaetzung_numerisch": eigene_zahl,
        "eigene_einschaetzung": result.get("eigene_einschaetzung"),
        "eigene_gewichtung": result.get("eigene_gewichtung"),
        "begruendung_bei_uebereinstimmung": result.get("begruendung_bei_uebereinstimmung"),
        "uebereinstimmung_mit_haiku": uebereinstimmung,
        "szenario_bedingungen": result.get("szenario_bedingungen", []),
        "haiku_einschaetzung_zum_vergleich": haiku_einschaetzung,
        "erstellt_am": datetime.now().isoformat(),
    }

    eigene_historie.setdefault("fazits", [])
    eigene_historie["fazits"] = [neuer_eintrag] + eigene_historie["fazits"]
    eigene_historie["fazits"] = eigene_historie["fazits"][:90]
    eigene_historie["letzte_aktualisierung"] = heute

    save_claude_fazit(eigene_historie)

    print(f"\n{'='*40}")
    print(f"✓ Eigene Einschätzung gespeichert: {result.get('eigene_einschaetzung_numerisch', '?')} (-5 bis +5)")
    print(f"✓ Übereinstimmung mit Haiku (objektiv berechnet): {uebereinstimmung}")
    print(f"✓ Schwachstellen identifiziert: {len(result.get('identifizierte_schwachstellen', []))}")
    gew = result.get("eigene_gewichtung", {})
    print(f"✓ Eigene Gewichtung: Bullish {gew.get('bullish')}% / "
          f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%")


if __name__ == "__main__":
    main()
