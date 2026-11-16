# Entwicklung

In kleinen vertikalen Schritten arbeiten: Anforderung klären, implementieren, prüfen, unabhängig reviewen und gezielt integrieren.

Pro Schreibbereich ein verantwortlicher Agent; Agenten verwenden Pi. Der Hauptagent koordiniert gemeinsame Dateien, Integration und Veröffentlichung. Vorhandene Nutzeränderungen und geparkte Arbeit bleiben erhalten.

Verhalten und Schnittstellen werden zusammen mit der Implementierung dokumentiert.

Gezielte Python-Prüfungen: `uv run python -m pytest <Testpfad>`. Container- und Datenbanktests auf kube1 ausführen.

[Arbeitsanweisungen](../../AGENTS.md) · [Zuständigkeiten](../../multi-agent.md)
