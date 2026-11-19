# Changelog

Alle relevanten Änderungen an Lumi Crew. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.3.1] - 2026-11-19

### Behoben
- Der Rand um die Oberfläche (Innenabstand des Terminal-Fensters) zeigte die Profilfarbe des Terminals statt des
  Lumi-Hintergrunds. Während die TUI läuft, wird jetzt auch der Terminal-Hintergrund auf Lumi gesetzt und beim
  Beenden zurückgesetzt (Terminal.app per AppleScript für den eigenen Tab, sonst OSC 11/111).
  Abschalten mit `LUMI_CREW_TERMINAL_BG=0`.

## [0.3.0] - 2026-11-19

### Entfernt
- Provider `claude` (Claude-Code-CLI mit claude.ai-Abo-Login). Claude gibt es weiter per API-Key (`anthropic`).

### Hinzugefügt
- Lokale Provider-Plugins: `~/.config/lumi-crew/plugins/*.py` mit `register(api)`; erscheinen in `/agent`,
  `/usage`, `lumi-crew setup` und bei Limit-Vorschlägen. Defekte Plugins erzeugen nur eine Warnung.

## [0.2.0] - 2026-11-19

### Hinzugefügt
- Provider-Einrichtung: Fehlt eine CLI (codex, claude) oder Ollama/LM Studio, bietet Lumi Crew nach Rückfrage die
  offizielle Installation per Homebrew an und startet danach den offiziellen Login bzw. den lokalen Server.
- Claude zusätzlich per API-Key (`anthropic`) im `/agent`-Picker; der Key wird verdeckt abgefragt und im
  macOS-Schlüsselbund gespeichert (sonst `~/.config/lumi-crew/keys.json`, Modus 600).
- `lumi-crew setup`: geht alle Provider im Terminal durch.
- README: Hinweise zu Rechtlichem und Datenschutz.

## [0.1.0] - 2026-11-19

Erstes Release.

### Hinzugefügt
- Multi-Agent-Team: Orchestrator mit Clarifier, Planner, Coder, Reviewer, Tester und Researcher; unabhängige
  Aufgaben werden parallel delegiert.
- Provider: agy, claude und codex über ihre CLIs, Ollama, LM Studio, Anthropic, Gemini und OpenAI-kompatible APIs;
  pro Agent ein eigenes Modell.
- Textual-TUI in Lumi-Farben: Chat mit Agent-Namen, Live-Fenster pro Unter-Agent, Slash-Befehle mit
  Autocomplete, `/agent`-Auswahl mit Live-Modelllisten, `/usage`, Freigaben mit Diff, Rückfragen als Auswahl.
- Modi manual, accept-edits, auto und plan, Pfad-Jail und Befehls-Blockliste.
- Gespeicherte Modellwahl und Chats (`/resume`, `/new`, `lumi-crew -c`, `--resume`, `lumi-crew chats`).
- Usage-Limit-Erkennung mit Vorschlägen für ein Ersatzmodell.
- MCP-Client für den Researcher, Headless-Modus `lumi-crew -p`.

[Unreleased]: https://github.com/OhneKommentar/lumi-crew/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/OhneKommentar/lumi-crew/releases/tag/v0.3.1
[0.3.0]: https://github.com/OhneKommentar/lumi-crew/releases/tag/v0.3.0
[0.2.0]: https://github.com/OhneKommentar/lumi-crew/releases/tag/v0.2.0
[0.1.0]: https://github.com/OhneKommentar/lumi-crew/releases/tag/v0.1.0
