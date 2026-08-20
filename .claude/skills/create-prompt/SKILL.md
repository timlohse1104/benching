---
name: create-prompt
description: Erzeugt einen neuen Benchmark-Prompt für dieses Projekt und legt ihn in prompts/ als .txt mit fortlaufender Nummer ab. Auslösen bei Anfragen wie "neuen Prompt erstellen", "Prompt hinzufügen", "Benchmark-Aufgabe anlegen", "leg einen Prompt für X an" oder dem Befehl /create-prompt.
---

Erzeuge einen neuen Benchmark-Prompt für dieses Test-Bench-Projekt und speichere ihn in `prompts/` als `.txt`. Falls `$ARGUMENTS` übergeben wurde, ist das die Aufgabenidee (Thema) für den Prompt. Andernfalls frage den Nutzer zuerst, worum es gehen soll.

## Kontext: Wozu diese Prompts dienen

Jeder Prompt in `prompts/` ist eine Aufgabe, die das Bench über die Prompt × Modell-Matrix an jedes konfigurierte LLM schickt. Erwartet wird als Antwort **eine einzige, in sich geschlossene HTML-Datei** (alles inline: CSS + JS, keine externen Ressourcen). Das Ergebnis wird zweifach validiert (html5lib-Parser + Playwright-Headless-Render) und im Dashboard als Thumbnail gezeigt.

Ein guter Prompt ist deshalb:
- **Self-contained-tauglich** — die Lösung muss als eine `.html`-Datei ohne Netzwerk/Build laufen.
- **Konkret und prüfbar** — klare Requirements, damit sich Modelle unterscheiden lassen.
- **Fordernd, aber fair** — genug Spielraum, dass schwache und starke Modelle sichtbar auseinanderdriften.

## Schritte

### 1. Thema klären
- Wenn `$ARGUMENTS` gesetzt ist: nutze es als Aufgabenidee.
- Sonst: frage den Nutzer knapp nach Thema/Idee (z. B. "Was soll der neue Prompt abfragen? z. B. ein Spiel, eine Visualisierung, ein UI-Komponente").
- Optional nachfragen, ob strenger Stil (wie `01`/`02`, mit "Output ONLY the HTML") oder freier, kreativer Stil (wie `03`) gewünscht ist. Im Zweifel: strenger Stil.

### 2. Nächste Nummer und Dateiname bestimmen
- Liste `prompts/` und finde die höchste vorhandene Nummer im Muster `NN-*.txt`.
- Neue Nummer = höchste + 1, **zweistellig null-gepolstert** (`01`, `02`, … `10`, `11`).
- Slug: kurzer, aussagekräftiger `kebab-case`-Name aus dem Thema (nur `a-z0-9-`), z. B. `sortier-visualizer` → `04-sort-visualizer.txt`.
- Ergebnis-Dateiname: `prompts/{NN}-{slug}.txt`.
- Vor dem Schreiben prüfen, dass die Datei noch nicht existiert; bei Kollision Slug anpassen.

### 3. Prompt-Inhalt schreiben
Halte dich an den Stil der bestehenden Prompts (`01-landing-page.txt`, `02-svg-clock.txt`, `03-tower-defense.txt`). Struktur:

1. **Erste Zeile**: ein Satz, der die Aufgabe beschreibt und dabei "self-contained HTML" verlangt.
2. **Leerzeile**, dann ein `Requirements:`-Block mit Aufzählungspunkten (`- ...`), darunter:
   - "Single .html file. Inline CSS and JS only. No external resources."
   - Fachliche, konkrete Anforderungen zum Thema.
   - "Must be valid HTML5" (ggf. "Must not throw any JavaScript errors when loaded." bei interaktiven Aufgaben).
3. **Strenger Stil**: mit `- Output ONLY the HTML. No markdown fences, no commentary, no explanation.` abschließen.
   **Freier Stil**: stattdessen mit einer offenen, kreativitätsfördernden Zeile enden (z. B. "Come up with an original idea and be creative.").

Der komplette Prompt-Text ist **Englisch** (Artefakt im Ökosystem). Keine Markdown-Fences in der `.txt` selbst — nur reiner Prompt-Text.

### 4. Datei schreiben und bestätigen
- Schreibe den Prompt nach `prompts/{NN}-{slug}.txt`.
- Gib eine kurze Bestätigung aus: Dateiname + einzeilige Zusammenfassung der Aufgabe.
- Optional hinweisen: mit `llm-check run --dry-run` lässt sich der neue Prompt sofort smoke-testen.

## Wichtige Regeln

- Dateiname **immer** `NN-kebab-slug.txt`, Nummer zweistellig und lückenlos fortlaufend.
- Prompt-Text **Englisch**, reiner Text (keine Fences, kein YAML-Frontmatter in der `.txt`).
- Die Aufgabe muss als **eine self-contained `.html`** lösbar sein — sonst passt sie nicht ins Bench.
- Bestehende Prompts **nicht** überschreiben oder umnummerieren.
