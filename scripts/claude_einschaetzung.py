# Aktualisiert: 2026-08-04 11:40
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

GEÄNDERT (25.07.2026): Die abstrakte -5/+5-Einschätzung wurde durch
konkrete KURSZIELE in EUR ersetzt (3/7/14/30-Tage-Horizonte, analog zu
update_bitcoin.py). Das macht sowohl den Vergleich mit Haikus Fazit als
auch die eigene Trefferquoten-Berechnung präziser: statt nur "Richtung
richtig/falsch" (✓/✗) sehen wir jetzt die erwartete %-Veränderung vs.
die tatsächliche %-Veränderung - also auch WIE WEIT daneben, nicht nur
OB daneben. ALTE Einträge mit der -5/+5-Skala bleiben unverändert in
claude_fazit.json stehen und werden von der neuen Trefferquoten-
Berechnung übersprungen (keine Rückwirkung, keine Vermischung der
Skalen).

GEÄNDERT (04.08.2026, 09:45): Nach wiederholten "Ungültiges JSON von
Claude: Unterminated string"-Fehlern (zuletzt bei nur ~5700 Zeichen,
weit unter dem max_tokens=4096-Budget) wird jetzt zusätzlich
stop_reason und output_tokens der API-Antwort geloggt, um die
tatsächliche Abbruchursache beim nächsten Auftreten sichtbar zu
machen. Bei einem JSON-Fehler wird außerdem die VOLLSTÄNDIGE Rohantwort
(nicht nur die ersten 500 Zeichen) in claude_fazit_error_debug.json
gespeichert.

GEÄNDERT (04.08.2026, 11:40): Bis zu 3 Versuche (Original + 2 Retries)
bei ungültigem JSON, mit 5 Sekunden Pause dazwischen - fängt einmalige
Ausreißer ab, ohne den Freitext/die Begründungen zu kürzen (Punkt 1 aus
der Diskussion wurde bewusst verworfen: Klartext-Begründungen sind der
Zweck des Projekts und dürfen nicht gekappt werden). Bei Erfolg nach
einem Retry wird die Anzahl benötigter Versuche als Feld
"api_versuche" im gespeicherten Eintrag vermerkt, damit Ausreißer in
claude_fazit.json sichtbar bleiben, ohne den gesamten Workflow neu
laufen zu lassen. Scheitern alle 3 Versuche, enthält die Debug-Datei
jetzt alle Rohantworten (nicht nur die letzte), um Abbruchmuster
vergleichen zu können.
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


def save_error_debug(versuche_protokoll, path="claude_fazit_error_debug.json"):
    """Speichert nach endgültigem Scheitern (alle Versuche fehlgeschlagen)
    ALLE Rohantworten samt Diagnosedaten je Versuch, damit man vergleichen
    kann, ob der Abbruch immer an derselben Stelle passiert oder nicht -
    hilfreich, um Content- vs. Limit- vs. API-Flakiness-Ursachen zu
    unterscheiden."""
    debug_daten = {
        "zeitpunkt": datetime.now().isoformat(),
        "anzahl_versuche": len(versuche_protokoll),
        "versuche": versuche_protokoll,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug_daten, f, ensure_ascii=False, indent=2)


def frage_claude_mit_retry(client, prompt, max_versuche=3, pause_sekunden=5):
    """Fragt Claude bis zu max_versuche mal an, bis eine Antwort ein
    gültiges JSON-Objekt ergibt. Gibt (result_dict, anzahl_versuche,
    versuche_protokoll) zurück, oder (None, anzahl_versuche,
    versuche_protokoll) wenn alle Versuche scheitern - versuche_protokoll
    enthält für JEDEN Versuch stop_reason/output_tokens/Rohantwort/Fehler,
    damit im Fehlerfall nichts verloren geht."""
    versuche_protokoll = []

    for versuch_nr in range(1, max_versuche + 1):
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        stop_reason = message.stop_reason
        output_tokens = message.usage.output_tokens
        response_text = message.content[0].text.strip()

        print(f"  Versuch {versuch_nr}/{max_versuche} - stop_reason: {stop_reason}, "
              f"output_tokens: {output_tokens}", file=sys.stderr)

        geparster_text = response_text
        if "```json" in geparster_text:
            geparster_text = geparster_text.split("```json")[1].split("```")[0].strip()
        elif "```" in geparster_text:
            geparster_text = geparster_text.split("```")[1].split("```")[0].strip()

        eintrag_protokoll = {
            "versuch": versuch_nr,
            "stop_reason": stop_reason,
            "output_tokens": output_tokens,
            "response_text_vollstaendig": response_text,
            "response_text_laenge": len(response_text),
        }

        try:
            result = json.loads(geparster_text)
            versuche_protokoll.append(eintrag_protokoll)
            return result, versuch_nr, versuche_protokoll
        except json.JSONDecodeError as e:
            eintrag_protokoll["fehler"] = str(e)
            versuche_protokoll.append(eintrag_protokoll)
            print(f"    Versuch {versuch_nr}/{max_versuche} fehlgeschlagen: {e}", file=sys.stderr)
            if versuch_nr < max_versuche:
                time.sleep(pause_sekunden)

    return None, max_versuche, versuche_protokoll


# ─── Kursziel-Hilfsfunktionen (NEU 25.07.2026) ─────────────────────────────

HORIZONTE_TAGE = [3, 7, 14, 30]


def erwartete_veraenderung_prozent(kurs_start, kursziel):
    """Rechnet ein Kursziel in eine erwartete %-Veränderung ausgehend vom
    Startkurs um. Gibt None zurück, wenn einer der Werte fehlt."""
    if kurs_start is None or kursziel is None or kurs_start == 0:
        return None
    return round(((kursziel - kurs_start) / kurs_start) * 100, 2)


def hat_neues_kursziel_format(fazit):
    """Prüft, ob ein Fazit-Eintrag bereits das neue Kursziel-Format nutzt
    (statt der alten -5/+5-Skala von vor dem 25.07.2026)."""
    return bool(fazit.get("eigenes_kursziel_eur"))


# ─── Trefferquote-Feedback: objektive Nachberechnung ───────────────────────
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
    Geht die eigene Fazit-Historie durch (NUR Einträge im neuen
    Kursziel-Format, siehe hat_neues_kursziel_format) und berechnet für
    jeden fälligen Horizont (3/7/14/30 Tage) ohne vorhandenen Rückblick
    den tatsächlichen Kursverlauf via CoinGecko. Speichert je Horizont
    sowohl die erwartete als auch die tatsächliche %-Veränderung.

    Begrenzung auf MAX_NACHBERECHNUNGEN_PRO_LAUF API-Aufrufe pro Lauf
    (über alle Einträge/Horizonte hinweg), um CoinGecko nicht zu
    überlasten und die Laufzeit des Skripts zu begrenzen.
    """
    heute = datetime.strptime(heute_str, "%Y-%m-%d").date()
    MAX_NACHBERECHNUNGEN_PRO_LAUF = 8
    nachberechnet = 0

    for eintrag in eigene_historie.get("fazits", []):
        if nachberechnet >= MAX_NACHBERECHNUNGEN_PRO_LAUF:
            break

        if not hat_neues_kursziel_format(eintrag):
            continue  # altes -5/+5-Format: überspringen, keine Rückwirkung

        eintrag_datum_str = eintrag.get("datum")
        if not eintrag_datum_str:
            continue
        try:
            eintrag_datum = datetime.strptime(eintrag_datum_str, "%Y-%m-%d").date()
        except Exception:
            continue

        start_kurs = eintrag.get("kurs_eur")
        kursziele = eintrag.get("eigenes_kursziel_eur", {})

        for horizont_tage in HORIZONTE_TAGE:
            if nachberechnet >= MAX_NACHBERECHNUNGEN_PRO_LAUF:
                break

            feld = f"rueckblick_{horizont_tage}_tage"
            if f"{feld}_tatsaechlich_prozent" in eintrag:
                continue  # dieser Horizont wurde bereits ausgewertet

            tage_vergangen = (heute - eintrag_datum).days
            if tage_vergangen < horizont_tage:
                continue  # noch nicht fällig

            kursziel = kursziele.get(f"{horizont_tage}_tage")
            if kursziel is None or start_kurs is None:
                continue

            ziel_datum_str = (eintrag_datum + timedelta(days=horizont_tage)).strftime("%Y-%m-%d")
            ziel_kurs = fetch_historical_btc_price_eur(ziel_datum_str)
            nachberechnet += 1
            time.sleep(2)

            if ziel_kurs is None:
                continue

            erwartet_prozent = erwartete_veraenderung_prozent(start_kurs, kursziel)
            tatsaechlich_prozent = erwartete_veraenderung_prozent(start_kurs, ziel_kurs)

            eintrag[f"{feld}_erwartet_prozent"] = erwartet_prozent
            eintrag[f"{feld}_tatsaechlich_prozent"] = tatsaechlich_prozent
            eintrag[f"{feld}_tatsaechlicher_kurs"] = ziel_kurs

            print(
                f"    ✓ Trefferquote-Nachberechnung {eintrag_datum_str} "
                f"({horizont_tage}T): erwartet {erwartet_prozent:+.2f}% "
                f"vs. tatsächlich {tatsaechlich_prozent:+.2f}%"
            )

    # Für den Prompt: die letzten 10 vollständig ausgewerteten 7-Tage-
    # Horizonte (7 Tage als "Leitmetrik" analog zur bisherigen Trefferquote)
    ausgewertete = [
        f for f in eigene_historie.get("fazits", [])
        if hat_neues_kursziel_format(f) and "rueckblick_7_tage_tatsaechlich_prozent" in f
    ]
    ausgewertete.sort(key=lambda f: f["datum"], reverse=True)
    letzte_10 = ausgewertete[:10]

    treffer = 0
    summe_abweichung = 0.0
    for f in letzte_10:
        erwartet = f["rueckblick_7_tage_erwartet_prozent"]
        tatsaechlich = f["rueckblick_7_tage_tatsaechlich_prozent"]
        richtung_erwartet = 1 if erwartet > 1.0 else (-1 if erwartet < -1.0 else 0)
        richtung_tatsaechlich = 1 if tatsaechlich > 1.0 else (-1 if tatsaechlich < -1.0 else 0)
        if richtung_erwartet == richtung_tatsaechlich:
            treffer += 1
        summe_abweichung += abs(erwartet - tatsaechlich)

    trefferquote_prozent = round((treffer / len(letzte_10)) * 100, 0) if letzte_10 else None
    durchschnittliche_abweichung = round(summe_abweichung / len(letzte_10), 2) if letzte_10 else None

    return eigene_historie, letzte_10, trefferquote_prozent, durchschnittliche_abweichung


def formatiere_trefferquote_block(letzte_eintraege, trefferquote_prozent, durchschnittliche_abweichung):
    """Baut den Prompt-Abschnitt mit der eigenen bisherigen Trefferquote,
    jetzt auf Basis erwarteter vs. tatsächlicher %-Kursveränderung (7-Tage-
    Horizont als Leitmetrik)."""
    if not letzte_eintraege:
        return (
            "\n\nDEINE BISHERIGE TREFFERQUOTE: Noch keine ausgewerteten "
            "eigenen Kursziele vorhanden (erste ~7 Tage nach Umstellung "
            "auf das neue Kursziel-Format am 25.07.2026)."
        )

    zeilen = []
    for f in letzte_eintraege:
        erwartet = f["rueckblick_7_tage_erwartet_prozent"]
        tatsaechlich = f["rueckblick_7_tage_tatsaechlich_prozent"]
        treffer_symbol = "✓" if (
            (erwartet > 1.0 and tatsaechlich > 1.0) or
            (erwartet < -1.0 and tatsaechlich < -1.0) or
            (-1.0 <= erwartet <= 1.0 and -1.0 <= tatsaechlich <= 1.0)
        ) else "✗"
        zeilen.append(
            f"  {f['datum']}: Du hast {erwartet:+.1f}% erwartet "
            f"→ tatsächlich {tatsaechlich:+.1f}% {treffer_symbol}"
        )

    quote_text = f"{trefferquote_prozent:.0f}%" if trefferquote_prozent is not None else "n/a"
    abweichung_text = f"{durchschnittliche_abweichung:.1f} Prozentpunkte" if durchschnittliche_abweichung is not None else "n/a"

    return f"""

DEINE BISHERIGE TREFFERQUOTE (letzte {len(letzte_eintraege)} ausgewertete eigene Kursziele, 7-Tage-Horizont):
{chr(10).join(zeilen)}

Deine Richtungs-Trefferquote: {quote_text} (Richtung richtig vs. falsch gelegen)
Deine durchschnittliche Abweichung (erwartet vs. tatsächlich): {abweichung_text}

WICHTIG ZUR KALIBRIERUNG: Das ist deine eigene, objektiv nachgerechnete
Bilanz - nicht Haikus. Prüfe kritisch, ob du selbst ein wiederkehrendes
Fehlermuster hast (z.B. systematisch zu pessimistisch, Kursziele zu
extrem oder zu vorsichtig geschätzt). Nutze das aktiv zur Kalibrierung
deiner heutigen Kursziele."""


def formatiere_haiku_kursziel(haiku_fazit_heute):
    """Zeigt Haikus heutiges Fazit an, egal ob es das neue Kursziel-Format
    oder (bei einem Übergangstag) noch die alte -5/+5-Skala nutzt."""
    if not haiku_fazit_heute:
        return "Kein Haiku-Fazit für heute vorhanden."

    gew = haiku_fazit_heute.get("gewichtung", {})
    kz = haiku_fazit_heute.get("kursziel_eur")

    if kz:
        kursziel_text = (
            f"Kursziele (3/7/14/30 Tage): €{kz.get('3_tage')} / "
            f"€{kz.get('7_tage')} / €{kz.get('14_tage')} / €{kz.get('30_tage')}"
        )
    else:
        # Übergangs-Fallback, falls Haiku an einem Tag noch die alte Skala liefert
        kursziel_text = f"Einschätzung (ALTE SKALA, -5 bis +5): {haiku_fazit_heute.get('einschaetzung_numerisch')}"

    return (
        f"{kursziel_text}\n"
        f"Kurs zum Zeitpunkt: €{haiku_fazit_heute.get('kurs_eur')}\n"
        f"Begründung: {haiku_fazit_heute.get('einschaetzung')}\n"
        f"Gewichtung: Bullish {gew.get('bullish')}% / "
        f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%\n"
        f"Schlüsselniveau: €{haiku_fazit_heute.get('schluessel_niveau_eur')} - "
        f"{haiku_fazit_heute.get('schluessel_niveau_erklaerung')}\n"
        f"Nächster Katalysator: {haiku_fazit_heute.get('naechster_katalysator')}"
    )


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
        f"- [Kursziel 7T: €{e.get('kursziel_eur_7_tage', e.get('einschaetzung_numerisch', '?'))}] {e.get('kategorie', '')}: {e.get('titel', '')}\n"
        f"  {e.get('beschreibung', '')}"
        for e in heutige_ereignisse
    ) or "Keine neuen Ereignisse für heute vorhanden."

    haiku_text = formatiere_haiku_kursziel(haiku_fazit_heute)

    letzte_eigene = eigene_historie.get("fazits", [])[:5]
    eigene_historie_text = ""
    if letzte_eigene:
        eigene_historie_text = "\n\nDEINE EIGENEN LETZTEN EINSCHÄTZUNGEN (für Kontinuität):\n"
        for f in letzte_eigene:
            if hat_neues_kursziel_format(f):
                kz = f.get("eigenes_kursziel_eur", {})
                kz_text = (
                    f"€{kz.get('3_tage')} / €{kz.get('7_tage')} / "
                    f"€{kz.get('14_tage')} / €{kz.get('30_tage')}"
                )
            else:
                kz_text = f"ALTE SKALA: {f.get('eigene_einschaetzung_numerisch', f.get('eigene_tendenz'))}"
            eigene_historie_text += (
                f"\nDatum: {f.get('datum')}\n"
                f"Deine Kursziele damals (3/7/14/30 Tage): {kz_text}\n"
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
   passen)? Sind die Kursziele realistisch kalibriert, oder wirken sie
   willkürlich (z.B. runde Zahlen ohne erkennbare Herleitung)? Zu
   starke Extrapolation aus kurzfristigen Nachrichten auf
   mittelfristige Kursziele? Benenne die Schwachstellen konkret, nicht
   pauschal.

2. STÄRKSTES GEGENARGUMENT: Formuliere das stärkste Argument GEGEN
   Haikus Einschätzung, auch wenn du am Ende zu einem ähnlichen Schluss
   kommst - das Argument muss ernsthaft und nicht als Strohmann
   formuliert sein.

3. EIGENE, UNABHÄNGIGE KURSZIELE: Bilde deine eigenen Kursziele in EUR
   für 3/7/14/30 Tage, basierend auf den Rohereignissen UND deiner
   Kritik aus Schritt 1-2. Falls deine Kursziele in eine ähnliche
   Richtung wie Haikus gehen, MUSST du explizit begründen, warum die
   von dir gefundene Kritik die Gesamteinschätzung nicht kippt - reine
   Zustimmung ohne diese Begründung ist nicht zulässig. Berücksichtige
   aktiv deine eigene bisherige Trefferquote (siehe oben) zur
   Kalibrierung - insbesondere, ob deine Kursziele in der Vergangenheit
   tendenziell zu extrem oder zu vorsichtig waren.

4. SZENARIO-BEDINGUNGEN: Welche 2-4 konkreten, überprüfbaren
   Ereignisse/Entwicklungen müssten eintreten, damit sich deine
   Kursziele bestätigen? Keine Zeitprognose, sondern überprüfbare
   Auslöser für den späteren Rückblick.

WICHTIG: Das ist KEINE verlässliche Kursprognose, sondern eine
Markteinordnung. Bitcoin ist hochvolatil und nachrichtengetrieben - sei
entsprechend vorsichtig in der Formulierung. Ein Kursziel nahe am
aktuellen Kurs ist eine valide Aussage ("keine klare Richtung
erkennbar"), kein Fehler.

Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (kein Markdown,
kein Text davor/danach):

{{
  "identifizierte_schwachstellen": [
    "Konkrete Schwachstelle 1 in Haikus Analyse",
    "Konkrete Schwachstelle 2 in Haikus Analyse"
  ],
  "staerkstes_gegenargument": "Das stärkste ernsthafte Argument gegen Haikus Fazit, 2-3 Sätze",
  "eigenes_kursziel_eur": {{
    "3_tage": {int(preise['btc_eur'])},
    "7_tage": {int(preise['btc_eur'])},
    "14_tage": {int(preise['btc_eur'])},
    "30_tage": {int(preise['btc_eur'])}
  }},
  "eigene_einschaetzung": "3-5 Sätze deine unabhängige Markteinschätzung",
  "eigene_gewichtung": {{
    "bullish": 30,
    "bearish": 60,
    "neutral": 10
  }},
  "begruendung_bei_uebereinstimmung": "Falls deine Kursziele in eine ähnliche Richtung wie Haikus gehen: warum kippt die gefundene Kritik das Gesamtbild nicht? Falls deine Kursziele deutlich abweichen: leer lassen oder kurz bestätigen, dass keine Übereinstimmung vorliegt.",
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

    print("Berechne eigene Trefferquote (bisherige Kursziele vs. Realität)...")
    eigene_historie, letzte_eintraege, trefferquote_prozent, durchschnittliche_abweichung = (
        berechne_eigene_trefferquote(eigene_historie, heute)
    )
    save_claude_fazit(eigene_historie)  # Nachberechnete Rückblicke sofort sichern
    trefferquote_block = formatiere_trefferquote_block(
        letzte_eintraege, trefferquote_prozent, durchschnittliche_abweichung
    )
    if trefferquote_prozent is not None:
        print(f"  ✓ Eigene aktuelle Trefferquote: {trefferquote_prozent:.0f}% "
              f"({len(letzte_eintraege)} Auswertungen, "
              f"⌀ Abweichung {durchschnittliche_abweichung:.1f} Prozentpunkte)")
    else:
        print("  Noch keine ausgewerteten eigenen Kursziele vorhanden.")

    print("Abrufen: BTC- und ETH-Kurs...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed Index...")
    fear_greed = fetch_fear_greed()

    print("Baue Prompt mit heutigen Ereignissen + Haikus Fazit (als kritisch zu prüfende Analyse) + Trefferquote...")
    prompt = build_prompt(ereignisse_daten, eigene_historie, preise, fear_greed, heute, trefferquote_block)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # NEU (04.08.2026, 11:40): bis zu 3 Versuche (Original + 2 Retries) bei
    # ungültigem JSON, statt sofort abzubrechen - fängt einmalige Ausreißer
    # (z.B. kurzzeitige API-Instabilität) ab, ohne den Prompt oder die
    # gewünschten Klartext-Begründungen zu kürzen.
    result, anzahl_versuche, versuche_protokoll = frage_claude_mit_retry(
        client, prompt, max_versuche=3, pause_sekunden=5
    )

    if result is None:
        print(f"\nFehler: Ungültiges JSON von Claude nach {anzahl_versuche} Versuchen", file=sys.stderr)
        letzter_fehler = versuche_protokoll[-1].get("fehler", "unbekannt")
        print(f"Letzter Fehler: {letzter_fehler}", file=sys.stderr)
        # Vollständige Rohantworten ALLER Versuche + Diagnosedaten sichern,
        # damit man vergleichen kann, ob der Abbruch immer an derselben
        # Stelle passiert.
        save_error_debug(versuche_protokoll)
        print("Alle Rohantworten + Diagnosedaten gespeichert in claude_fazit_error_debug.json", file=sys.stderr)
        sys.exit(1)

    if anzahl_versuche > 1:
        print(f"  ✓ Gültiges JSON erst im Versuch {anzahl_versuche}/3 erhalten (Ausreißer)", file=sys.stderr)

    haiku_fazit_heute = next(
        (f for f in ereignisse_daten.get("fazits", []) if f.get("datum") == heute),
        None
    )
    kurs_eur = haiku_fazit_heute.get("kurs_eur") if haiku_fazit_heute else preise.get("btc_eur")

    haiku_kursziel = haiku_fazit_heute.get("kursziel_eur") if haiku_fazit_heute else None
    eigenes_kursziel = result.get("eigenes_kursziel_eur", {})

    # NEU (27.07.2026): Validierung ergänzt, analog zu update_bitcoin.py -
    # ein technisch gültiges JSON ohne vollständiges eigenes_kursziel_eur
    # soll den Workflow-Lauf sichtbar rot markieren, statt unbemerkt ein
    # leeres/unvollständiges Kursziel abzuspeichern.
    if not isinstance(eigenes_kursziel, dict) or not all(
        f"{h}_tage" in eigenes_kursziel for h in [3, 7, 14, 30]
    ):
        print(f"\nFEHLER: eigenes_kursziel_eur unvollständig oder fehlt: {eigenes_kursziel}", file=sys.stderr)
        print(f"Vorhandene Antwort-Keys: {list(result.keys())}", file=sys.stderr)
        sys.exit(1)

    # Übereinstimmung wird OBJEKTIV aus der Differenz der erwarteten
    # 7-Tage-%-Veränderung berechnet (GEÄNDERT 25.07.2026: vorher aus der
    # Differenz der -5/+5-Zahlen). Nur möglich, wenn Haiku ebenfalls
    # bereits das neue Kursziel-Format liefert.
    uebereinstimmung = "unbekannt"
    if haiku_kursziel and eigenes_kursziel and kurs_eur:
        eigene_erwartung_7t = erwartete_veraenderung_prozent(kurs_eur, eigenes_kursziel.get("7_tage"))
        haiku_erwartung_7t = erwartete_veraenderung_prozent(kurs_eur, haiku_kursziel.get("7_tage"))
        if eigene_erwartung_7t is not None and haiku_erwartung_7t is not None:
            diff = abs(eigene_erwartung_7t - haiku_erwartung_7t)
            if diff <= 2.0:
                uebereinstimmung = "hoch"
            elif diff <= 5.0:
                uebereinstimmung = "mittel"
            else:
                uebereinstimmung = "niedrig"

    neuer_eintrag = {
        "datum": heute,
        "kurs_eur": kurs_eur,
        "identifizierte_schwachstellen": result.get("identifizierte_schwachstellen", []),
        "staerkstes_gegenargument": result.get("staerkstes_gegenargument"),
        "eigenes_kursziel_eur": eigenes_kursziel,
        "eigene_einschaetzung": result.get("eigene_einschaetzung"),
        "eigene_gewichtung": result.get("eigene_gewichtung"),
        "begruendung_bei_uebereinstimmung": result.get("begruendung_bei_uebereinstimmung"),
        "uebereinstimmung_mit_haiku": uebereinstimmung,
        "szenario_bedingungen": result.get("szenario_bedingungen", []),
        "haiku_kursziel_zum_vergleich": haiku_kursziel,
        "erstellt_am": datetime.now().isoformat(),
        "api_versuche": anzahl_versuche,
    }

    eigene_historie.setdefault("fazits", [])
    eigene_historie["fazits"] = [neuer_eintrag] + eigene_historie["fazits"]
    eigene_historie["fazits"] = eigene_historie["fazits"][:90]
    eigene_historie["letzte_aktualisierung"] = heute

    save_claude_fazit(eigene_historie)

    print(f"\n{'='*40}")
    kz = eigenes_kursziel
    print(f"✓ Eigene Kursziele gespeichert (3/7/14/30 Tage): "
          f"€{kz.get('3_tage')} / €{kz.get('7_tage')} / "
          f"€{kz.get('14_tage')} / €{kz.get('30_tage')}")
    print(f"✓ Übereinstimmung mit Haiku (objektiv berechnet, 7-Tage-Basis): {uebereinstimmung}")
    print(f"✓ Schwachstellen identifiziert: {len(result.get('identifizierte_schwachstellen', []))}")
    gew = result.get("eigene_gewichtung", {})
    print(f"✓ Eigene Gewichtung: Bullish {gew.get('bullish')}% / "
          f"Bearish {gew.get('bearish')}% / Neutral {gew.get('neutral')}%")
    if anzahl_versuche > 1:
        print(f"⚠ Hinweis: {anzahl_versuche} API-Versuche nötig (siehe api_versuche im Eintrag)")


if __name__ == "__main__":
    main()
