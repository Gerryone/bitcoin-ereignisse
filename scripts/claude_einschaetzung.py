name: Claude Sonnet Bitcoin-Einschätzung

on:
  schedule:
    # Täglich um 08:00 UTC (10:00 MESZ / 09:00 MEZ) - eine Stunde NACH
    # dem Haiku-Workflow (07:00 UTC), damit Haikus Tagesfazit für
    # heute garantiert schon in ereignisse.json vorliegt.
    - cron: '0 8 * * *'
  workflow_dispatch:
    # Manuell auslösbar über GitHub UI (Actions → Run workflow)

jobs:
  einschaetzung:
    runs-on: ubuntu-latest
    permissions:
      contents: write  # Nötig für git push
    steps:
      - name: Repository auschecken
        uses: actions/checkout@v4

      - name: Python einrichten
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Abhängigkeiten installieren
        run: pip install anthropic requests

      - name: Claude (Sonnet) Einschätzung erstellen
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python scripts/claude_einschaetzung.py

      - name: Änderungen committen und pushen
        run: |
          git config user.name "Claude-Einschaetzung-Bot"
          git config user.email "claude-einschaetzung-bot@github.com"
          git add claude_fazit.json
          if git diff --staged --quiet; then
            echo "Keine Änderungen – nichts zu committen."
          else
            git commit -m "Update: Claude-Einschätzung $(date +%Y-%m-%d)"
            git push
            echo "✓ Erfolgreich gepusht."
          fi
