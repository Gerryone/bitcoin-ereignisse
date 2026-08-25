# 25.08.2026 17:00
#!/usr/bin/env python3
"""
Bitcoin-Einschätzung (Claude Sonnet 5)

NEUFASSUNG 13.08.2026 — ersetzt die Red-Team-Fassung vom 04.08.2026.

Was sich geändert hat und warum:

1. KEINE GEGENPRÜFUNG VON HAIKU MEHR.
   Die alte Rolle ("prüfe Haikus Fazit kritisch") war kein echtes Ensemble:
   Sonnet bekam Haikus fertige Kursziele in den Prompt und war darauf
   geankert. Die Auswertung vom 13.08.2026 hat gezeigt, dass beide Modelle
   denselben Fehler machen (Dauer-Skepsis), nur unterschiedlich stark — es
   gab nichts zu mitteln. Haiku recherchiert weiterhin die Ereignisse; die
   Prognose macht ausschliesslich dieses Skript, ohne Haikus Fazit zu sehen.
   Als Ersatz fuer die Red-Team-Funktion liefert das Modell im selben Aufruf
   das staerkste Gegenargument GEGEN DIE EIGENE Prognose.

2. WAHRSCHEINLICHKEIT + SPANNE STATT PUNKTGENAUER KURSZIELE.
   Bei einem Kursziel kann ein Modell nicht "ich weiss es nicht" sagen —
   jede Zahl wirkt wie eine Aussage. Neu: eine Richtungswahrscheinlichkeit
   (0-100) und eine Spanne mit angegebener Konfidenz. Damit ist 50 % eine
   zulaessige, unbestrafte Antwort, und die Qualitaet wird per Brier-Score
   und Trefferabdeckung messbar statt per Treffer/Daneben.
   Das abgeleitete EUR-Kursziel (Spannenmitte) wird weiterhin mitgeschrieben,
   damit die bestehende Historie und die Auswertung vom 13.08. anschlussfaehig
   bleiben.

3. DAS MODELL SIEHT JETZT DEN EIGENEN KURSVERLAUF.
   Bisher bekam es Nachrichten und Indikatoren, aber NICHT den Kursverlauf.
   Nachrichtenlage ist strukturell negativ gefaerbt; wer nur daraus
   extrapoliert, landet zwangslaeufig bei "runter". Das ist die vermutete
   Hauptursache des gefundenen Pessimismus-Bias (33 von 35 Prognosen negativ
   bei +6,4 % Kursentwicklung im selben Zeitraum).
   Neu im Prompt: 90-Tage-Kursverlauf, abgeleitete Kennzahlen und vor allem
   die BASISRATE — wie oft der Kurs historisch ueber diesen Horizont gestiegen
   ist. Das Modell muss die Basisrate zuerst nennen und dann begruenden,
   warum es davon abweicht.

4. ALLE ABLEITUNGEN RECHNET DAS SKRIPT, NICHT DAS MODELL.
   Veraenderungen, Mittelwertabstand, Schwankungsbreite und Basisraten sind
   reine Rechnerei — deterministisch, kostenlos und per Konstruktion nicht
   halluzinierbar.

5. TREFFERBILANZ KOMMT AUS HOME ASSISTANT, NICHT MEHR AUS COINGECKO.
   Die alte Nachberechnung per CoinGecko-Historie ist ersatzlos entfallen.
   HA hat den Kurs alle 15 Minuten statt eines Tageswerts, rechnet also
   genauer, und es gab bisher zwei getrennt gerechnete Trefferquoten mit
   verschiedenen Methoden (51/49 im Dashboard, andere Zahl im Prompt).
   HA veroeffentlicht die Bilanz unter /local/bitcoin_feedback.json; dieses
   Skript liest sie. Faellt HA aus, laeuft der Lauf OHNE Bilanzblock weiter
   statt abzubrechen.

6. JEDER DATENSATZ TRAEGT MODELL UND PROMPT-VERSION.
   Ein Modellwechsel ist damit ein Fuenf-Minuten-Vorgang, ohne dass die
   Historie stillschweigend vermischt wird. Gilt genauso fuer Prompt-
   Aenderungen — die passieren haeufiger und machen die Zahlen davor und
   danach genauso unvergleichbar.

WICHTIG ZUR EINORDNUNG: Das ist eine Markteinordnung, keine verlaessliche
Kursprognose.
"""

import json
import os
import statistics
import sys
import time
from datetime import date, datetime

import anthropic
import requests


# ─── Konfiguration ─────────────────────────────────────────────────────────

MODELL = "claude-sonnet-5"

# Anlagewert dieser Prognose. Steht als Konstante da, weil absehbar weitere
# Werte dazukommen — dann bekommt jeder seinen eigenen Lauf, statt dass die
# Zuordnung im Nachhinein geraten werden muss.
ASSET = "BTC"

# Bei JEDER inhaltlichen Aenderung am Prompt hochzaehlen. Wird an jedem
# Datensatz mitgespeichert, damit die Auswertung sauber schneiden kann.
PROMPT_VERSION = 4

# Denk-Token zaehlen in dasselbe Budget wie die sichtbare Antwort. Sonnet 5
# denkt standardmaessig mit, 8192 reichten danach nicht mehr.
#
# ACHTUNG: Ohne Streaming lehnt das SDK jeden Aufruf mit max_tokens ueber
# rund 21.333 clientseitig ab (Rechnung in _base_client.py: 3600 * max_tokens
# / 128000 > 600 Sekunden). Genau daran ist der Lauf vom 17.08.2026 dreimal
# gescheitert, bevor ueberhaupt eine Anfrage rausging. Der Aufruf unten
# streamt deshalb — damit ist dieser Wert nach oben frei.
MAX_TOKENS = 24000

# Reduziert von [3, 7, 14, 30]: 3 Tage sind bei Bitcoin fast reines Rauschen,
# 14 und 30 ueberlappen stark. Zwei Horizonte brauchen halb so viele Daten
# fuer eine belastbare Aussage.
HORIZONTE_TAGE = [7, 30]

# Konfidenzniveau der Spanne. Fest vorgegeben, damit die Trefferabdeckung
# ueber die Zeit ueberhaupt pruefbar ist.
KONFIDENZ_PROZENT = 80

KURSHISTORIE_TAGE = 90

# FIX 25.08.2026 17:00: Adresse von ha.renken2019.de auf gerry07.duckdns.org
# umgestellt. Der alte Name wurde vom Reverse Proxy der Synology-NAS bedient
# und per CNAME auf die MyFRITZ-Adresse gefuehrt. Die NAS ist abgeschaltet,
# seitdem beantwortet der NGINX des Home Assistant die Anfrage mit seinem
# Zertifikat fuer gerry07.duckdns.org - der Name passt nicht, die TLS-Pruefung
# scheitert. Sichtbar in claude_fazit.json: feedback_verfuegbar sprang am
# 23.08.2026 von true auf false, die Einschaetzungen vom 23. und 24.08.
# entstanden also ohne Brier-Score und Trefferbilanz.
# MUSS mit FEEDBACK_URL_HINWEIS in bitcoin/konstanten.py uebereinstimmen.
HA_FEEDBACK_URL = "https://gerry07.duckdns.org/local/bitcoin_feedback.json"
HA_FEEDBACK_TIMEOUT = 15

ERLAUBTE_KATEGORIEN = {
    "ETF", "Regulierung", "Institutionell", "Makro",
    "OnChain", "Technik", "Persönlichkeiten",
}


# ─── Datenabruf ────────────────────────────────────────────────────────────

def fetch_crypto_prices():
    """Aktuellen BTC- und ETH-Kurs in EUR sowie deren 24h-Aenderung."""
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
        btc, eth = data["bitcoin"], data["ethereum"]
        return {
            "btc_eur": float(btc["eur"]),
            "btc_change_24h": float(btc.get("eur_24h_change", 0)),
            "eth_eur": float(eth["eur"]),
            "eth_change_24h": float(eth.get("eur_24h_change", 0)),
            "stand": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        # Kein stiller Rueckfall auf Platzhalter: ohne aktuellen Kurs ist die
        # gesamte Prognose sinnlos, also lieber sichtbar abbrechen.
        print(f"FEHLER: Kursabruf fehlgeschlagen ({e})", file=sys.stderr)
        sys.exit(1)


def fetch_fear_greed():
    """Crypto Fear & Greed Index (kostenlos, kein Schluessel noetig)."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {
            "wert": int(data["value"]),
            "klassifikation": data.get("value_classification", "unbekannt"),
            "stand": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        print(f"  Warnung: Fear&Greed nicht abrufbar ({e}) — Block entfaellt", file=sys.stderr)
        return None


def fetch_kurshistorie_eur(tage=KURSHISTORIE_TAGE):
    """Holt den BTC-Kursverlauf in EUR und verdichtet ihn zu Tagesschluss-
    kursen. CoinGecko liefert bei 90 Tagen stuendliche Punkte; die
    Verdichtung passiert hier, damit wir nicht vom interval-Parameter
    abhaengen (der auf manchen Tarifen nicht verfuegbar ist).

    Rueckgabe: Liste von (datum_str, kurs) aufsteigend, oder None.
    """
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "eur", "days": tage},
            timeout=30,
        )
        resp.raise_for_status()
        rohpunkte = resp.json().get("prices", [])
        if not rohpunkte:
            return None

        # Letzter Wert je Kalendertag = Tagesschlusskurs
        pro_tag = {}
        for ts_ms, kurs in rohpunkte:
            tag = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            pro_tag[tag] = float(kurs)

        return sorted(pro_tag.items())
    except Exception as e:
        print(f"  Warnung: Kurshistorie nicht abrufbar ({e})", file=sys.stderr)
        return None


def fetch_ha_feedback():
    """Holt die von Home Assistant veroeffentlichte Trefferbilanz.

    Bewusst fehlertolerant: Ist HA nicht erreichbar, laeuft der Lauf ohne
    Bilanzblock weiter. Der Prompt ist dann schwaecher, aber der Tag faellt
    nicht aus — und die Luecke ist im Ergebnis sichtbar (feedback_verfuegbar).
    """
    try:
        resp = requests.get(HA_FEEDBACK_URL, timeout=HA_FEEDBACK_TIMEOUT)
        resp.raise_for_status()
        daten = resp.json()

        # Die Datei ist nach Anlagewert gegliedert, damit weitere Werte ohne
        # Formatbruch dazukommen koennen.
        block = (daten.get("assets") or {}).get(ASSET)
        if block is None:
            print(f"  Warnung: Trefferbilanz enthält keinen Block für {ASSET} "
                  f"(vorhanden: {list((daten.get('assets') or {}).keys())}) — "
                  f"Lauf geht ohne Bilanzblock weiter", file=sys.stderr)
            return None

        block["erstellt_am"] = daten.get("erstellt_am")
        print(f"  ✓ Trefferbilanz für {ASSET} von Home Assistant geladen "
              f"(Stand {daten.get('erstellt_am', 'unbekannt')})", file=sys.stderr)
        return block
    except Exception as e:
        print(f"  Warnung: Trefferbilanz von HA nicht abrufbar ({e}) — "
              f"Lauf geht ohne Bilanzblock weiter", file=sys.stderr)
        return None


# ─── Ableitungen: reine Rechnerei, kein Modell ─────────────────────────────

def berechne_kennzahlen(historie, horizonte=HORIZONTE_TAGE):
    """Leitet aus dem Tageskursverlauf alles ab, was das Modell als Anker
    braucht. Deterministisch — hier kann per Konstruktion nichts
    halluziniert werden.

    Enthaelt insbesondere die BASISRATE je Horizont: wie oft der Kurs im
    Beobachtungszeitraum ueber genau diesen Horizont gestiegen ist. Das ist
    die Zahl, gegen die sich jede Prognose rechtfertigen muss.
    """
    if not historie or len(historie) < 15:
        return None

    kurse = [k for _, k in historie]
    aktuell = kurse[-1]

    def veraenderung(tage):
        if len(kurse) <= tage:
            return None
        frueher = kurse[-(tage + 1)]
        if not frueher:
            return None
        return round(((aktuell - frueher) / frueher) * 100, 2)

    mittel_30 = statistics.fmean(kurse[-30:]) if len(kurse) >= 30 else statistics.fmean(kurse)

    # Tagesrenditen fuer die realisierte Schwankungsbreite
    renditen = []
    for i in range(1, len(kurse)):
        if kurse[i - 1]:
            renditen.append((kurse[i] - kurse[i - 1]) / kurse[i - 1])
    tages_vola = statistics.pstdev(renditen) if len(renditen) > 1 else 0.0

    basisraten = {}
    for h in horizonte:
        if len(kurse) <= h:
            basisraten[h] = None
            continue
        gestiegen = sum(
            1 for i in range(len(kurse) - h) if kurse[i + h] > kurse[i]
        )
        gesamt = len(kurse) - h
        basisraten[h] = {
            "anteil_gestiegen_prozent": round(gestiegen / gesamt * 100),
            "anzahl_faelle": gesamt,
            # Erwartete Schwankung ueber diesen Horizont, als Anker fuer die
            # Spanne: Tagesvola hochskaliert mit Wurzel(Zeit).
            "typische_schwankung_prozent": round(tages_vola * (h ** 0.5) * 100, 1),
        }

    return {
        "kurs_aktuell": round(aktuell, 2),
        "veraenderung_7_tage_prozent": veraenderung(7),
        "veraenderung_30_tage_prozent": veraenderung(30),
        "veraenderung_90_tage_prozent": veraenderung(90),
        "mittel_30_tage": round(mittel_30, 2),
        "abstand_zum_mittel_prozent": round((aktuell - mittel_30) / mittel_30 * 100, 2),
        "tages_schwankung_prozent": round(tages_vola * 100, 2),
        "hoch_90_tage": round(max(kurse), 2),
        "tief_90_tage": round(min(kurse), 2),
        "basisraten": basisraten,
        "beobachtete_tage": len(kurse),
    }


# ─── Prompt-Bausteine ──────────────────────────────────────────────────────

def formatiere_kursverlauf(historie, letzte_tage=14):
    """Kompakte Darstellung: Wochenpunkte ueber den ganzen Zeitraum plus die
    letzten Tage taeglich. Vollstaendig waeren 90 Zeilen Ballast."""
    if not historie:
        return "Kursverlauf nicht verfügbar."

    zeilen = ["Wochenverlauf (jeder 7. Tag):"]
    for datum, kurs in historie[:-letzte_tage][::7]:
        zeilen.append(f"  {datum}: €{kurs:,.0f}")

    zeilen.append(f"\nLetzte {letzte_tage} Tage (täglich):")
    for datum, kurs in historie[-letzte_tage:]:
        zeilen.append(f"  {datum}: €{kurs:,.0f}")

    return "\n".join(zeilen)


def formatiere_kennzahlen(kz):
    if not kz:
        return "Kennzahlen nicht verfügbar."

    zeilen = [
        f"Aktueller Kurs: €{kz['kurs_aktuell']:,.0f}",
        f"Veränderung 7 Tage:  {kz['veraenderung_7_tage_prozent']:+.2f} %"
        if kz["veraenderung_7_tage_prozent"] is not None else "Veränderung 7 Tage: n/a",
        f"Veränderung 30 Tage: {kz['veraenderung_30_tage_prozent']:+.2f} %"
        if kz["veraenderung_30_tage_prozent"] is not None else "Veränderung 30 Tage: n/a",
        f"Veränderung 90 Tage: {kz['veraenderung_90_tage_prozent']:+.2f} %"
        if kz["veraenderung_90_tage_prozent"] is not None else "Veränderung 90 Tage: n/a",
        f"30-Tage-Mittel: €{kz['mittel_30_tage']:,.0f} "
        f"(Kurs liegt {kz['abstand_zum_mittel_prozent']:+.2f} % davon entfernt)",
        f"Spanne über {kz['beobachtete_tage']} Tage: "
        f"€{kz['tief_90_tage']:,.0f} bis €{kz['hoch_90_tage']:,.0f}",
        f"Tägliche Schwankung (Standardabweichung): {kz['tages_schwankung_prozent']:.2f} %",
    ]
    return "\n".join(zeilen)


def formatiere_basisraten(kz):
    """Der wichtigste Block im ganzen Prompt: der Ausgangspunkt, von dem das
    Modell begründet abweichen muss."""
    if not kz or not kz.get("basisraten"):
        return "Basisraten nicht verfügbar — arbeite ersatzweise mit 50 %."

    zeilen = [
        f"Ausgewertet über die letzten {kz['beobachtete_tage']} Tage "
        f"(überlappende Fenster):"
    ]
    for h, br in sorted(kz["basisraten"].items()):
        if not br:
            zeilen.append(f"  {h} Tage: zu wenig Historie")
            continue
        zeilen.append(
            f"  {h} Tage: In {br['anteil_gestiegen_prozent']} % der "
            f"{br['anzahl_faelle']} Fälle stand der Kurs danach HÖHER. "
            f"Typische Schwankung über diesen Zeitraum: "
            f"±{br['typische_schwankung_prozent']} %."
        )
    return "\n".join(zeilen)


def formatiere_bilanz(feedback):
    """Die eigene Trefferbilanz aus Home Assistant, inklusive Bias-Kennzahl
    im Klartext. Ohne diesen Block kalibriert sich nichts."""
    if not feedback:
        return (
            "\nDEINE BISHERIGE TREFFERBILANZ:\n"
            "Nicht verfügbar (Home Assistant war nicht erreichbar). "
            "Arbeite ohne Kalibrierungsrückmeldung, aber bleibe bei der "
            "Basisrate als Ausgangspunkt.\n"
        )

    bilanz = feedback.get("eigene_bilanz") or {}
    if not bilanz.get("anzahl_bewertet"):
        return (
            "\nDEINE BISHERIGE TREFFERBILANZ:\n"
            "Noch keine ausgewerteten Prognosen im neuen Format vorhanden.\n"
        )

    zeilen = ["\nDEINE BISHERIGE TREFFERBILANZ (objektiv nachgerechnet, nicht selbst eingeschätzt):"]
    zeilen.append(f"Ausgewertete Prognosen: {bilanz['anzahl_bewertet']}")

    for h in HORIZONTE_TAGE:
        brier = bilanz.get(f"brier_{h}_tage")
        if brier is not None:
            bewertung = (
                "besser als blindes Raten" if brier < 0.25
                else "schlechter als blindes Raten (!)" if brier > 0.25
                else "exakt so gut wie blindes Raten"
            )
            zeilen.append(f"Brier-Score {h} Tage: {brier:.3f} — {bewertung} (0 = perfekt, 0,25 = Münzwurf)")

    abdeckung = bilanz.get("abdeckung_spanne_prozent")
    if abdeckung is not None:
        zeilen.append(
            f"Trefferabdeckung deiner {KONFIDENZ_PROZENT}-%-Spannen: {abdeckung} % "
            f"(Sollwert {KONFIDENZ_PROZENT} %. Deutlich darunter = zu selbstsicher, "
            f"deutlich darüber = zu breit und damit nichtssagend)"
        )

    bearisch = bilanz.get("anteil_bearische_prognosen_prozent")
    gestiegen = bilanz.get("anteil_tatsaechlich_gestiegen_prozent")
    if bearisch is not None and gestiegen is not None:
        zeilen.append(
            f"\nDEINE RICHTUNGSVERTEILUNG: In {bearisch} % deiner bisherigen "
            f"Prognosen hast du fallende Kurse erwartet. Tatsächlich ist der Kurs "
            f"in {gestiegen} % der Fälle gestiegen."
        )
        if bearisch - (100 - gestiegen) > 15:
            zeilen.append(
                "DAS IST EIN SYSTEMATISCHER PESSIMISMUS-BIAS. Korrigiere ihn aktiv: "
                "Wenn deine Analyse dich erneut zu einer bärischen Wahrscheinlichkeit "
                "führt, prüfe zuerst, ob du wirklich Belege hast oder nur die "
                "übliche negative Färbung der Nachrichtenlage extrapolierst."
            )
        elif (100 - gestiegen) - bearisch > 15:
            zeilen.append(
                "DAS IST EIN SYSTEMATISCHER OPTIMISMUS-BIAS. Korrigiere ihn aktiv."
            )

    letzte = bilanz.get("letzte_prognosen") or []
    if letzte:
        zeilen.append("\nDeine letzten Prognosen mit Ergebnis:")
        for p in letzte[:8]:
            ergebnis = p.get("tatsaechlich_hoeher")
            ergebnis_text = "gestiegen" if ergebnis else ("gefallen" if ergebnis is False else "noch offen")
            try:
                wahrscheinlichkeit = f"{int(round(float(p.get('wahrscheinlichkeit_hoeher'))))}"
            except (TypeError, ValueError):
                wahrscheinlichkeit = "?"
            zeilen.append(
                f"  {p.get('datum')} ({p.get('horizont_tage')} T): "
                f"{wahrscheinlichkeit} % für höher → {ergebnis_text}"
            )

    return "\n".join(zeilen) + "\n"


def formatiere_ereignisse(ereignisse_daten, heute):
    """Nur die Ereignisse — Haikus Tagesfazit wird bewusst NICHT mehr
    eingebunden, damit keine Ankerwirkung entsteht."""
    heutige = [
        e for e in ereignisse_daten.get("ereignisse", [])
        if e.get("datum") == heute
    ]
    if not heutige:
        return "Keine neuen Ereignisse für heute recherchiert."

    zeilen = []
    for e in heutige:
        kategorie = e.get("kategorie", "?")
        if kategorie not in ERLAUBTE_KATEGORIEN:
            kategorie = f"{kategorie} (unerwartete Kategorie)"
        zeilen.append(f"- [{kategorie}] {e.get('titel', '')}\n  {e.get('beschreibung', '')}")
    return "\n".join(zeilen)


# ─── Prompt ────────────────────────────────────────────────────────────────

def build_prompt(heute, preise, fear_greed, kennzahlen, historie, feedback,
                 ereignisse_daten):
    kurs = preise["btc_eur"]

    fg_text = (
        f"{fear_greed['wert']} ({fear_greed['klassifikation']})"
        if fear_greed else "nicht verfügbar"
    )

    beispiel_prognosen = ",\n".join(
        f"""    {{
      "horizont_tage": {h},
      "wahrscheinlichkeit_hoeher": 50,
      "spanne_unten": {int(kurs * 0.95)},
      "spanne_oben": {int(kurs * 1.05)},
      "konfidenz": {KONFIDENZ_PROZENT},
      "kursziel_abgeleitet": {int(kurs)}
    }}"""
        for h in HORIZONTE_TAGE
    )

    return f"""Du bist ein Bitcoin-Marktanalyst. Heute ist der {heute}.

Deine Aufgabe ist NICHT, eine mutige Prognose abzugeben, sondern eine gut
kalibrierte. Du wirst mit dem Brier-Score bewertet: Der belohnt
Wahrscheinlichkeiten, die auf Dauer zutreffen, und bestraft Selbstsicherheit
ohne Deckung. Eine Wahrscheinlichkeit von 50 % ist eine vollwertige,
korrekte Antwort, wenn du es nicht besser weisst — sie kostet dich nichts.
Eine Wahrscheinlichkeit von 20 %, die dann in der Haelfte der Faelle
danebenliegt, kostet dich viel.

═══ KURSVERLAUF ═══
{formatiere_kursverlauf(historie)}

═══ KENNZAHLEN (vorberechnet, nicht von dir zu ueberpruefen) ═══
{formatiere_kennzahlen(kennzahlen)}

═══ BASISRATEN — DEIN AUSGANGSPUNKT ═══
{formatiere_basisraten(kennzahlen)}

Diese Zahlen sind dein Startpunkt. Ohne eigene Erkenntnis ist die Basisrate
die richtige Antwort. Jede Abweichung davon musst du begruenden.
{formatiere_bilanz(feedback)}
═══ MARKTSTIMMUNG ═══
Crypto Fear & Greed Index (0-100): {fg_text}
Ethereum: €{preise['eth_eur']:,.0f} (24h: {preise['eth_change_24h']:+.1f} %)

═══ HEUTE RECHERCHIERTE EREIGNISSE ═══
{formatiere_ereignisse(ereignisse_daten, heute)}

WARNUNG ZUR EINORDNUNG DIESER EREIGNISSE: Sie stammen ueberwiegend aus
Krypto-Fachmedien. Solche Quellen berichten strukturell haeufiger ueber
Risiken, Warnungen und Rueckschlaege als ueber ruhige Aufwaertsphasen. Wenn
dich die Nachrichtenlage zu einer bearischen Einschaetzung draengt, pruefe,
ob das an den Fakten liegt oder an der Auswahl. Frage dich ausserdem bei
jedem Ereignis: Hat der Markt darauf bereits reagiert? Ein Ereignis, das im
Kurs schon verarbeitet ist, aendert an der kuenftigen Richtung nichts.

═══ DEINE AUFGABE ═══

Fuer jeden Horizont ({" und ".join(str(h) for h in HORIZONTE_TAGE)} Tage):

1. Nenne die Basisrate als Ausgangspunkt.
2. Entscheide, ob du davon abweichst — und begruende jede Abweichung mit
   konkreten Belegen aus Kursverlauf, Kennzahlen oder Ereignissen. Ohne
   Beleg bleibst du bei der Basisrate.
3. Gib die Wahrscheinlichkeit an, dass Bitcoin in diesem Horizont HOEHER
   steht als die aktuellen €{kurs:,.0f}.
4. Gib eine Spanne an, in der der Kurs mit {KONFIDENZ_PROZENT} %
   Wahrscheinlichkeit liegen wird. Nutze die angegebene typische Schwankung
   als Anker — eine deutlich engere Spanne behauptest du nur mit Begruendung.
5. Setze das abgeleitete Kursziel auf die Mitte deiner Spanne.

Danach zusaetzlich:

6. STAERKSTES GEGENARGUMENT: Das ernsthafteste Argument GEGEN deine eigene
   Einschaetzung. Kein Strohmann — das Argument, das dich am ehesten
   umstimmen wuerde.
7. WICHTIGSTE TREIBER: Die zwei bis vier Faktoren, die deine Einschaetzung
   tatsaechlich getragen haben.
8. UMSCHLAGPUNKT: Was muesste konkret eintreten, damit das Gegenteil deiner
   Einschaetzung passiert? Ueberpruefbar formuliert, ohne Zeitangabe.

Antworte AUSSCHLIESSLICH mit einem gueltigen JSON-Objekt, ohne Markdown und
ohne Text davor oder danach:

{{
  "prognosen": [
{beispiel_prognosen}
  ],
  "basisrate_verwendet": 50,
  "abweichung_begruendung": "Warum weichst du von der Basisrate ab, oder warum nicht? Konkrete Belege, 2-4 Saetze.",
  "einschaetzung": "Deine Markteinordnung in 3-5 Saetzen.",
  "staerkstes_gegenargument": "Das ernsthafteste Argument gegen deine eigene Einschaetzung, 2-3 Saetze.",
  "wichtigste_treiber": [
    "Treiber 1",
    "Treiber 2"
  ],
  "umschlagpunkt": "Was muesste eintreten, damit das Gegenteil passiert?"
}}

Hinweise: "wahrscheinlichkeit_hoeher" ist eine ganze Zahl von 0 bis 100.
"basisrate_verwendet" ist die Basisrate des 7-Tage-Horizonts, die du oben
abgelesen hast. Alle EUR-Werte sind ganze Zahlen ohne Tausendertrennzeichen.
"""


# ─── API-Aufruf ────────────────────────────────────────────────────────────

def extrahiere_text(message):
    """Sammelt ALLE Textbloecke der Antwort und ueberspringt alles andere.

    Sonnet 5 schaltet adaptives Denken standardmaessig ein und stellt der
    Antwort einen ThinkingBlock voran. Der hat kein .text — ein Zugriff auf
    content[0].text stuerzt deshalb ab (Ursache der Ausfaelle ab 14.08.2026).
    Der Filter nach Blocktyp ist auch gegen kuenftige neue Blocktypen immun.
    """
    teile = [b.text for b in message.content
             if getattr(b, "type", None) == "text"]
    return "\n".join(teile).strip()


def frage_modell_mit_retry(client, prompt, max_versuche=3, pause_sekunden=5):
    """Bis zu drei Versuche, bis eine Antwort gueltiges JSON ergibt.
    Protokolliert je Versuch stop_reason, output_tokens und die vollstaendige
    Rohantwort — im Fehlerfall geht damit nichts verloren.

    Der API-Aufruf steht INNERHALB des try. Vorher lag er davor: eine
    fehlgeschlagene Antwort loeste dadurch keinen zweiten Versuch aus, es
    entstand kein Fehlerartefakt und im Log stand nur ein nackter Traceback.

    GESTREAMT statt messages.create(): Bei nicht gestreamten Aufrufen schaetzt
    das SDK die Dauer allein aus max_tokens und verweigert alles ueber rund
    21.333 Token mit einem ValueError, ohne die Anfrage abzuschicken. Das hat
    am 17.08.2026 drei Versuche in Folge gekostet. get_final_message() liefert
    dasselbe Message-Objekt wie create(), also bleiben extrahiere_text(),
    stop_reason und usage unveraendert nutzbar — und die Schwelle betrifft
    uns kuenftig nicht mehr, egal wie hoch MAX_TOKENS steht.
    """
    versuche_protokoll = []

    for versuch_nr in range(1, max_versuche + 1):
        eintrag = {"versuch": versuch_nr}

        try:
            with client.messages.stream(
                model=MODELL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()

            stop_reason = message.stop_reason
            output_tokens = message.usage.output_tokens
            blocktypen = [getattr(b, "type", "?") for b in message.content]
            response_text = extrahiere_text(message)

            eintrag.update({
                "stop_reason": stop_reason,
                "output_tokens": output_tokens,
                "blocktypen": blocktypen,
                "response_text_vollstaendig": response_text,
                "response_text_laenge": len(response_text),
            })

            print(f"  Versuch {versuch_nr}/{max_versuche} — stop_reason: {stop_reason}, "
                  f"output_tokens: {output_tokens}, Bloecke: {blocktypen}, "
                  f"Textlaenge: {len(response_text)}", file=sys.stderr)

            if not response_text:
                raise ValueError(
                    f"Antwort ohne Textblock (Blocktypen: {blocktypen}, "
                    f"stop_reason: {stop_reason})"
                )

            geparster_text = response_text
            if "```json" in geparster_text:
                geparster_text = geparster_text.split("```json")[1].split("```")[0].strip()
            elif "```" in geparster_text:
                geparster_text = geparster_text.split("```")[1].split("```")[0].strip()

            result = json.loads(geparster_text)
            versuche_protokoll.append(eintrag)
            return result, versuch_nr, versuche_protokoll

        except Exception as e:
            eintrag["fehler"] = f"{type(e).__name__}: {e}"
            versuche_protokoll.append(eintrag)
            print(f"    fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            if versuch_nr < max_versuche:
                time.sleep(pause_sekunden)

    return None, max_versuche, versuche_protokoll


# ─── Validierung ───────────────────────────────────────────────────────────

def validiere_antwort(result, kurs_referenz):
    """Prueft die Modellantwort vollstaendig, BEVOR irgendetwas geschrieben
    wird. Grundsatz: Was durchfaellt, wird nicht gespeichert, sondern laut
    protokolliert — kein stiller Rueckfall auf Platzhalter.

    Rueckgabe: Liste von Fehlermeldungen (leer = in Ordnung).
    """
    fehler = []

    prognosen = result.get("prognosen")
    if not isinstance(prognosen, list) or not prognosen:
        return ["'prognosen' fehlt oder ist keine nicht-leere Liste"]

    gefundene_horizonte = set()

    for i, p in enumerate(prognosen):
        praefix = f"prognosen[{i}]"

        if not isinstance(p, dict):
            fehler.append(f"{praefix}: kein Objekt")
            continue

        h = p.get("horizont_tage")
        if h not in HORIZONTE_TAGE:
            fehler.append(f"{praefix}: horizont_tage={h!r}, erlaubt sind {HORIZONTE_TAGE}")
            continue
        gefundene_horizonte.add(h)

        w = p.get("wahrscheinlichkeit_hoeher")
        if not isinstance(w, (int, float)) or isinstance(w, bool) or not (0 <= w <= 100):
            fehler.append(f"{praefix}: wahrscheinlichkeit_hoeher={w!r} nicht zwischen 0 und 100")

        unten, oben = p.get("spanne_unten"), p.get("spanne_oben")
        for name, wert in (("spanne_unten", unten), ("spanne_oben", oben)):
            if not isinstance(wert, (int, float)) or isinstance(wert, bool) or wert <= 0:
                fehler.append(f"{praefix}: {name}={wert!r} ist keine positive Zahl")

        if isinstance(unten, (int, float)) and isinstance(oben, (int, float)):
            if unten >= oben:
                fehler.append(f"{praefix}: spanne_unten ({unten}) >= spanne_oben ({oben})")
            else:
                ziel = p.get("kursziel_abgeleitet")
                if not isinstance(ziel, (int, float)) or isinstance(ziel, bool):
                    fehler.append(f"{praefix}: kursziel_abgeleitet={ziel!r} ist keine Zahl")
                elif not (unten <= ziel <= oben):
                    fehler.append(
                        f"{praefix}: kursziel_abgeleitet ({ziel}) liegt ausserhalb "
                        f"der Spanne ({unten}–{oben})"
                    )
                # Plausibilitaet gegen den Referenzkurs: eine Spanne, die den
                # aktuellen Kurs um Faktoren verfehlt, ist ein Zahlendreher.
                if kurs_referenz and not (kurs_referenz * 0.3 <= ziel <= kurs_referenz * 3):
                    fehler.append(
                        f"{praefix}: kursziel_abgeleitet ({ziel}) unplausibel weit vom "
                        f"Referenzkurs ({kurs_referenz:.0f}) entfernt"
                    )

        k = p.get("konfidenz")
        if k != KONFIDENZ_PROZENT:
            fehler.append(f"{praefix}: konfidenz={k!r}, erwartet {KONFIDENZ_PROZENT}")

    fehlende = set(HORIZONTE_TAGE) - gefundene_horizonte
    if fehlende:
        fehler.append(f"Horizonte fehlen: {sorted(fehlende)}")

    for feld in ("abweichung_begruendung", "einschaetzung", "staerkstes_gegenargument",
                 "umschlagpunkt"):
        if not isinstance(result.get(feld), str) or not result[feld].strip():
            fehler.append(f"'{feld}' fehlt oder ist leer")

    basisrate = result.get("basisrate_verwendet")
    if not isinstance(basisrate, (int, float)) or isinstance(basisrate, bool) \
            or not (0 <= basisrate <= 100):
        fehler.append(f"'basisrate_verwendet'={basisrate!r} nicht zwischen 0 und 100")

    treiber = result.get("wichtigste_treiber")
    if not isinstance(treiber, list) or not treiber:
        fehler.append("'wichtigste_treiber' fehlt oder ist leer")

    return fehler


# ─── Dateizugriff ──────────────────────────────────────────────────────────

def load_json(path, standard=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return standard if standard is not None else {}


def save_json(daten, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


def save_error_debug(versuche_protokoll, validierungsfehler=None,
                     path="claude_fazit_error_debug.json"):
    save_json({
        "zeitpunkt": datetime.now().isoformat(),
        "modell": MODELL,
        "prompt_version": PROMPT_VERSION,
        "anzahl_versuche": len(versuche_protokoll),
        "validierungsfehler": validierungsfehler or [],
        "versuche": versuche_protokoll,
    }, path)


# ─── Hauptlauf ─────────────────────────────────────────────────────────────

def main():
    heute = str(date.today())
    print(f"\n=== Einschätzung {ASSET} {heute} "
          f"({MODELL}, Prompt-Version {PROMPT_VERSION}) ===\n")

    ereignisse_daten = load_json("ereignisse.json", {"ereignisse": [], "fazits": []})
    historie_datei = load_json("claude_fazit.json", {"fazits": []})

    if heute in {f.get("datum") for f in historie_datei.get("fazits", [])}:
        print("Einschätzung für heute bereits vorhanden. Nichts zu tun.")
        sys.exit(0)

    print("Abrufen: aktuelle Kurse...")
    preise = fetch_crypto_prices()

    print("Abrufen: Fear & Greed...")
    fear_greed = fetch_fear_greed()

    print(f"Abrufen: Kursverlauf {KURSHISTORIE_TAGE} Tage...")
    kurshistorie = fetch_kurshistorie_eur()
    kennzahlen = berechne_kennzahlen(kurshistorie)
    if kennzahlen:
        br7 = kennzahlen["basisraten"].get(7)
        print(f"  ✓ {kennzahlen['beobachtete_tage']} Tage geladen, "
              f"30-Tage-Veränderung {kennzahlen['veraenderung_30_tage_prozent']:+.2f} %"
              + (f", Basisrate 7 Tage: {br7['anteil_gestiegen_prozent']} %" if br7 else ""))
    else:
        print("  Warnung: keine Kennzahlen — Prompt läuft ohne Basisraten", file=sys.stderr)

    print("Abrufen: Trefferbilanz aus Home Assistant...")
    feedback = fetch_ha_feedback()

    print("Baue Prompt...")
    prompt = build_prompt(heute, preise, fear_greed, kennzahlen, kurshistorie,
                          feedback, ereignisse_daten)
    print(f"  Prompt-Länge: {len(prompt):,} Zeichen")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    result, anzahl_versuche, protokoll = frage_modell_mit_retry(client, prompt)

    if result is None:
        print(f"\nFEHLER: kein gültiges JSON nach {anzahl_versuche} Versuchen", file=sys.stderr)
        save_error_debug(protokoll)
        sys.exit(1)

    validierungsfehler = validiere_antwort(result, preise["btc_eur"])
    if validierungsfehler:
        print("\nFEHLER: Antwort hat die Validierung nicht bestanden:", file=sys.stderr)
        for f in validierungsfehler:
            print(f"  - {f}", file=sys.stderr)
        save_error_debug(protokoll, validierungsfehler)
        print("Es wurde NICHTS gespeichert (kein stiller Rückfall auf Platzhalter).",
              file=sys.stderr)
        sys.exit(1)

    if anzahl_versuche > 1:
        print(f"  ✓ gültiges JSON erst im Versuch {anzahl_versuche}/3", file=sys.stderr)

    neuer_eintrag = {
        "datum": heute,
        "asset": ASSET,
        "erstellt_am": datetime.utcnow().isoformat() + "Z",
        "modell": MODELL,
        "prompt_version": PROMPT_VERSION,
        "kurs_eur_referenz": round(preise["btc_eur"], 2),
        "konfidenz_prozent": KONFIDENZ_PROZENT,

        "prognosen": result["prognosen"],

        "basisrate_verwendet": result["basisrate_verwendet"],
        "abweichung_begruendung": result["abweichung_begruendung"],
        "einschaetzung": result["einschaetzung"],
        "staerkstes_gegenargument": result["staerkstes_gegenargument"],
        "wichtigste_treiber": result.get("wichtigste_treiber", []),
        "umschlagpunkt": result["umschlagpunkt"],

        "kennzahlen_zum_zeitpunkt": kennzahlen,
        "feedback_verfuegbar": feedback is not None,
        "api_versuche": anzahl_versuche,
        "datenstand": {
            "kurs": preise.get("stand"),
            "fear_greed": fear_greed.get("stand") if fear_greed else None,
            "trefferbilanz": (feedback or {}).get("erstellt_am"),
        },
    }

    historie_datei.setdefault("fazits", [])
    historie_datei["fazits"] = [neuer_eintrag] + historie_datei["fazits"]
    historie_datei["fazits"] = historie_datei["fazits"][:120]
    historie_datei["letzte_aktualisierung"] = heute
    save_json(historie_datei, "claude_fazit.json")

    print(f"\n{'=' * 50}")
    for p in result["prognosen"]:
        print(f"✓ {p['horizont_tage']:>2} Tage: {p['wahrscheinlichkeit_hoeher']:>3} % für höher | "
              f"Spanne €{p['spanne_unten']:,} – €{p['spanne_oben']:,} "
              f"({p['konfidenz']} %) | Mitte €{p['kursziel_abgeleitet']:,}")
    print(f"✓ Basisrate als Ausgangspunkt: {result['basisrate_verwendet']} %")
    print(f"✓ Trefferbilanz von HA: {'ja' if feedback else 'nein'}")
    print(f"✓ Anlagewert {ASSET}, Modell {MODELL}, Prompt-Version {PROMPT_VERSION}")


if __name__ == "__main__":
    main()
