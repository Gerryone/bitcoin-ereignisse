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
from datetime import date, datetime
import anthropic


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


def build_prompt(ereignisse_daten, eigene_historie, heute):
    """Baut den Prompt für Sonnet: aktuelle Ereignisse + Haikus Fazit
    + unsere eigene bisherige Einschätzungs-Historie (für Kontinuität)."""

    heutige_ereignisse = [
        e for e in ereignisse_daten.get("ereignisse", [])
        if e.get("datum") == heute
    ]
    haiku_fazit_heute = next(
        (f for f in ereignisse_daten.get("fazits", []) if f.get("datum") == heute),
        None
    )

    ereignis_text = "\n".join(
        f"- [{e.get('richtung', '?')}] {e.get('kategorie', '')}: {e.get('titel', '')}\n"
        f"  {e.get('beschreibung', '')}"
        for e in heutige_ereignisse
    ) or "Keine neuen Ereignisse für heute vorhanden."

    haiku_text = "Kein Haiku-Fazit für heute vorhanden."
    if haiku_fazit_heute:
        gew = haiku_fazit_heute.get("gewichtung", {})
        haiku_text = (
            f"Tendenz: {haiku_fazit_heute.get('tendenz')}\n"
            f"Kurs: €{haiku_fazit_heute.get('kurs_eur')}\n"
            f"Einschätzung: {haiku_fazit_heute.get('einschaetzung')}\n"
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
                f"Deine Tendenz damals: {f.get('eigene_tendenz')}\n"
                f"Deine Einschätzung: {f.get('eigene_einschaetzung', '')[:200]}...\n"
            )

    return f"""Du bist ein unabhängiger Bitcoin-Marktanalyst. Heute ist der {heute}.

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

WICHTIG: Das ist KEINE verlässliche Kursprognose, sondern eine
Markteinordnung basierend auf öffentlich verfügbaren Nachrichten. Bitcoin
ist hochvolatil und nachrichtengetrieben - sei entsprechend vorsichtig in
der Formulierung, vermeide übertriebene Sicherheit.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown,
kein Text davor/danach):

{{
  "eigene_tendenz": "bullish|bearish|neutral",
  "eigene_einschaetzung": "3-5 Sätze deine unabhängige Markteinschätzung",
  "eigene_gewichtung": {{
    "bullish": 30,
    "bearish": 60,
    "neutral": 10
  }},
  "kommentar_zu_haiku": "3-5 Sätze: wo stimmst du zu, wo widersprichst du, was ergänzt du?",
  "uebereinstimmung_mit_haiku": "hoch|mittel|niedrig"
}}

Hinweis: eigene_gewichtung muss immer exakt 100 ergeben (bullish + bearish + neutral = 100).
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

    print("Baue Prompt mit heutigen Ereignissen + Haikus Fazit...")
    prompt = build_prompt(ereignisse_daten, eigene_historie, heute)

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
    kurs_eur = haiku_fazit_heute.get("kurs_eur") if haiku_fazit_heute else None

    neuer_eintrag = {
        "datum": heute,
        "kurs_eur": kurs_eur,
        "eigene_tendenz": result.get("eigene_tendenz"),
        "eigene_einschaetzung": result.get("eigene_einschaetzung"),
        "eigene_gewichtung": result.get("eigene_gewichtung"),
        "kommentar_zu_haiku": result.get("kommentar_zu_haiku"),
        "uebereinstimmung_mit_haiku": result.get("uebereinstimmung_mit_haiku"),
        "haiku_tendenz_zum_vergleich": haiku_fazit_heute.get("tendenz") if haiku_fazit_heute else None,
        "erstellt_am": datetime.now().isoformat(),
    }

    eigene_historie.setdefault("fazits", [])
    eigene_historie["fazits"] = [neuer_eintrag] + eigene_historie["fazits"]
    eigene_historie["fazits"] = eigene_historie["fazits"][:90]
    eigene_historie["letzte_aktualisierung"] = heute

    save_claude_fazit(eigene_historie)

    print(f"\n{'='*40}")
    print(f"✓ Eigene Einschätzung gespeichert: {result.get('eigene_tendenz', '?').upper()}")
    print(f"✓ Übereinstimmung mit Haiku: {result.get('uebereinstimmung_mit_haiku', '?')}")
    gew = result.get("eigene_gewichtung", {})
    print(f"✓ Eigene Gewichtung: Bullish {gew.get('bullish')}% / "
          f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%")


if __name__ == "__main__":
    main()
