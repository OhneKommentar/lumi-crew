# crew: eine Multi-Agent-Coding-CLI

`crew` ist ein Terminal-Tool im Stil von Claude Code. Im Hintergrund arbeitet ein **Team spezialisierter Agenten**:
Ein **Orchestrator** entscheidet dynamisch, welcher Spezialist als Nächstes dran ist. Er klärt den Auftrag mit
Gegenfragen, plant, lässt Code schreiben, reviewen und testen und recherchiert bei Bedarf über MCP im Web.

```
┌ crew ─────────────────────────────────────────────┬ Agenten ──────────────────────┐
│ › Bau mir ein wc-Tool in Python                   │ 🎯 orchestrator agy:… ✔       │
│ ❓ clarifier ▶ Kläre die Anforderungen …           │ ├ ❓ clarifier ✔               │
│ ❓ clarifier fragt: Sollen Unicode-Wörter …?        │ ├ 🗺 planner ✔                 │
│ › ja                                              │ ├ ⌨ coder                      │
│    ⌨ coder ⚙ write_file(path=wordcount.py, …)      │ │ ├ ⚙ write_file(…) ✓         │
│ ▸ 🔍 reviewer: Ergebnis                            │ │ └ ⚙ write_file(…) ✓         │
│ ✅ Fertig: wordcount.py + test_wordcount.py …      │ └ 🧪 tester ✔                  │
├───────────────────────────────────────────────────┴───────────────────────────────┤
│ › _                                                                                │
│  auto  ⇧⇥ │ 📁 ~/proj │ 🎯 agy:gemini-3.8-flash-medium                              │
└────────────────────────────────────────────────────────────────────────────────────┘
```

## Architektur

```mermaid
flowchart TD
    U[User] <--> TUI[Textual TUI / headless CLI]
    TUI <--> S[Session]
    S --> O[🎯 Orchestrator]
    O -- delegate --> C[❓ Clarifier] & P[🗺 Planner] & K[⌨ Coder] & R[🔍 Reviewer] & T[🧪 Tester] & W[🌐 Researcher]
    C -- ask_user --> TUI
    O -- ask_user --> TUI
    subgraph Tools
      FS[read_file · list_dir · grep]
      WR[write_file · edit_file]
      SH[run_shell]
      MCP[MCP: web_search · fetch_page · search_pdf]
    end
    C & P & R --> FS
    K --> FS & WR
    T --> FS & SH
    W --> FS & MCP
    WR & SH & MCP --> PM{PermissionManager<br/>manual · accept-edits · auto · plan}
    PM -- "ask" --> TUI
    O & C & P & K & R & T & W --> PL[Provider-Layer]
    PL --> agy[agy CLI] & cl[claude CLI] & cx[codex CLI] & an[Anthropic API] & ge[Gemini API] & oa[OpenAI-kompatibel<br/>LM Studio · Ollama · OpenRouter]
```

- **Orchestrator-Pattern:** Nur der Orchestrator hat das Tool `delegate(agent, task)`. Jeder Spezialist startet
  mit leerem Kontext und eigenem Tool-Set und bekommt einen vollständigen Auftrag. Sein Ergebnis geht als
  Tool-Result an den Orchestrator zurück, wie bei Subagenten in Claude Code.
- **Harte Grenzen:** `max_steps` pro Agent, `max_delegations` und `max_review_rounds` pro Anfrage. Damit kann die
  Coder⇄Reviewer-Schleife nicht endlos laufen.
- **Rechte nach Rolle:** Nur der Coder schreibt, nur der Tester führt Shell-Befehle aus, nur der Researcher nutzt
  MCP. Reviewer und Planner können nur lesen.

| Agent | Tools | Ergebnis |
|---|---|---|
| 🎯 orchestrator | `delegate`, `ask_user`, lesen | Abschlussbericht |
| ❓ clarifier | `ask_user`, lesen | Spec (Ziel, Anforderungen, Annahmen, Akzeptanzkriterien) |
| 🗺 planner | lesen, `grep` | Dateien + Schritte + Teststrategie |
| ⌨ coder | lesen, `write_file`, `edit_file` | geänderte Dateien |
| 🔍 reviewer | lesen | `APPROVED` / `CHANGES_REQUESTED` + Issues |
| 🧪 tester | lesen, `run_shell` | `PASS` / `FAIL` + Output |
| 🌐 researcher | lesen, MCP-Tools | Zusammenfassung + Quellen |

## Provider: viele Modelle, ein Interface

| Provider | Auth | Tool-Calling |
|---|---|---|
| `agy` | Antigravity-Login (Google) | JSON-Text-Protokoll |
| `claude` | Claude-Code-Login (`claude -p`, Tools aus) | JSON-Text-Protokoll |
| `codex` | Codex-Login (`codex exec`, Sandbox read-only) | JSON-Text-Protokoll |
| `anthropic` | `ANTHROPIC_API_KEY` oder `ant auth login` | nativ |
| `gemini` | `GEMINI_API_KEY` | nativ |
| `openai`, `lmstudio`, `ollama`, eigene | Key per Env / lokal | nativ |

Die CLI-Agenten laufen als **reine Text-LLMs**: in einem leeren Temp-Ordner und ohne eigene Tools. Sie antworten
mit `{"tool": …, "args": …}` oder `{"final": …}` (`crew/providers/text_tools.py`), sodass ausschließlich crews
Tools mit Permission-Prüfung den Workspace anfassen. Abo-Logins werden nie ausgelesen. crew ruft nur die
offiziellen CLIs auf.

Jeder Agent kann ein anderes Modell nutzen (`crew.toml` → `[agents]` oder zur Laufzeit `/model coder codex`).

## Modi

| Modus | Dateien schreiben | Shell | MCP |
|---|---|---|---|
| **manual** | fragt (mit Diff) | fragt | fragt |
| **accept-edits** | automatisch | fragt | automatisch |
| **auto** | automatisch | automatisch, gefährliche Befehle geblockt | automatisch |
| **plan** | ✗ | ✗ | automatisch |

Wechseln mit **Shift+Tab** oder `/mode <name>`. Nach einem Lauf im Plan-Modus fragt crew, in welchem Modus der Plan
umgesetzt werden soll. Im Dialog bedeutet „Immer“, dass das Tool für den Rest der Session erlaubt bleibt. Alle
Pfade sind auf den Workspace begrenzt (inkl. Symlinks).

## Setup & Nutzung

```bash
cd crew
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
ln -s "$PWD/bin/crew" ~/.local/bin/crew   # `crew` überall nutzbar (schon eingerichtet)

crew providers                       # welche Provider sind nutzbar?
crew -w ~/mein-projekt               # TUI starten
crew -w ~/mein-projekt --mode plan   # erst nur planen
crew -p "Bau X" --mode auto -w demo  # headless, ein Auftrag
crew --model claude:sonnet           # alle Agenten auf ein Modell
cp crew.example.toml ~/mein-projekt/crew.toml   # Modelle pro Agent, MCP, Limits
```

> `bin/crew` ruft die venv direkt über `PYTHONPATH` auf. Der Grund: macOS/iCloud markiert Dateien in `~/Documents`
> manchmal als „hidden“, und Python ignoriert dann die `.pth`-Datei der Editable-Installation (`.venv/bin/crew` würde
> mit `No module named 'crew'` scheitern).

TUI-Befehle: `/help`, `/mode`, `/model <agent|all> <provider:model>`, `/agents`, `/providers`, `/mcp`, `/clear`, `/quit`.
Tasten: **Shift+Tab** Modus · **Esc** abbrechen · **Ctrl+C** beenden.

### Researcher mit dem MCP-Server aus Lab 4

```bash
labs/.venv/bin/python labs/lab4_mcp/mcp_server.py      # Terminal 1
crew -w demo                                                 # Terminal 2 (crew.toml mit [mcp.lab-websearch])
› Recherchiere, was FastMCP ist, und schreib eine NOTES.md
```

## Demo-Szenarien

1. **Gegenfragen:** `mach mir ein Spiel` → der Clarifier fragt Sprache, Art des Spiels, Steuerung … und dann baut
   die Crew es.
2. **Voller Ablauf:** `--mode auto`, `Baue wordcount.py wie wc, mit pytest-Tests` → Plan → Code → Review → Test (PASS).
3. **Plan-Modus:** Shift+Tab bis `plan` → Auftrag → Plan wird gezeigt → „Accept-Edits“ wählen → Umsetzung.
4. **Gemischte Modelle:** `/model coder codex`, `/model reviewer claude:sonnet` → in der Agenten-Leiste sieht man, wer
   welches Modell nutzt.

## Tests

```bash
pytest -q     # Parser, Permission-Matrix, Pfad-Jail, Delegation, Review-Limit, TUI (Textual Pilot)
```

## Projektstruktur

```
crew/
  cli.py            Einstieg: TUI, headless (-p), `crew providers`
  session.py        Session: Agenten, delegate, ask_user, Limits
  config.py         crew.toml
  permissions.py    Modi, Blockliste, Pfad-Jail
  events.py         Events Agent → UI, UI-Interface
  agents/           Agent-Loop, Rollen, System-Prompts (prompts/*.md)
  providers/        agy · claude · codex (Text-Protokoll), Anthropic · Gemini · OpenAI-kompatibel (nativ)
  tools/            Dateisystem, Shell, MCP-Client
  tui/              Textual-App + Dialoge
tests/
```

## Grenzen (MVP)

- Shell-Befehle laufen im Workspace, sind aber nicht hart gesandboxt (ein `cd /` ist möglich). Deshalb gibt es
  Freigaben in manual/accept-edits und die Blockliste in auto.
- CLI-Provider sind zustandslos. Pro Aufruf wird der Verlauf als Transkript mitgeschickt und bei Bedarf gekürzt.
- Keine Sessions zum Fortsetzen, keine parallelen Agenten.

## Labs (Vorarbeiten)

`labs/` enthält die Labore, auf denen crew aufbaut: **RAG** (`labs/lab_rag/rag_pdf.py`, PDF-Fragen mit FAISS + agy)
und **MCP** (`labs/lab4_mcp/mcp_server.py`, FastMCP-Server mit `web_search`, `fetch_page`, `search_pdf`, den der
Researcher von crew nutzt). Setup und Nutzung: `labs/README.md`.
