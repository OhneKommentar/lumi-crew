# Changelog

Alle relevanten Änderungen an Lumi Crew. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

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

[Unreleased]: https://github.com/OhneKommentar/lumi-crew/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OhneKommentar/lumi-crew/releases/tag/v0.1.0
