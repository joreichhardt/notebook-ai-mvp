# Upload full-flow: unabhängiges Source-Review und nächster Schnitt

## Stand und Aussagegrenzen

Geprüfter Ausgangsstand: `24e33e6bb5108e6b935c1f04cda660d9b6be4631`, Branch `notebook-upload-flow-review`. Nur dieses Dokument wird geändert; vorhandenes `.pi/` bleibt unberührt. Source-only Review, keine Implementierung, Testausführung, DB-Diagnose, Infrastrukturaktion oder Produktionsabnahme. Maßstab: [Produktspezifikation](../product-spec.md), besonders §§6, 9, 11–13, [Verträge](../architecture/contracts.md), A5/B3/D3a-Handoffs und tatsächliche Aufrufgrenzen.

**Ergebnis:** Kein neuer bestätigter Sicherheitsdefekt innerhalb der dokumentierten vertrauenswürdigen Aufrufergrenzen gefunden. Das ist keine vollständige Sicherheitsfreigabe. Der öffentliche Upload-bis-Quelle-Pfad fehlt weiterhin; vorhandene Bausteine sind kein aktivierter End-to-End-Pfad. Ein bestätigtes funktionales Skalierungslimit und eine unbestätigte Fehlerbehandlungshypothese sind unten ausdrücklich getrennt.

Phase A auf `88ad94d` gilt gemäß aktuellem Auftrag als akzeptiert: 14 Relationen, 26 Funktionen, 3 Akteure, Cleanup 0/0/0. Die vom Main gemeldete unabhängige Prüfung von 69 Dateien/Nachweisen sowie 64 lokalen Tests mit einem expliziten DB-Skip wird **nicht** als eigene Testausführung ausgegeben. Ältere Blockertexte in CONTINUE/Handoffs eröffnen Phase A nicht erneut. Phase A beweist weder Storage HTTP noch reale private Objektpolicies. Parallel vorbereitetes A5 Stage B und B4s unregistrierter/default-closed GET sind nicht Bestandteil dieses eingefrorenen Source-Stands und werden nicht dupliziert.

## Priorisierte Beobachtungen: Defekt, Grenze oder Hypothese?

### P2 — bestätigte funktionale Grenze: Quellen jenseits der ersten 100 sind nicht erreichbar

- **Ort:** `backend/app/documents/service.py:195–210`, besonders `:205–206`; `frontend/src/api.ts:186–192` und `frontend/src/Sources.tsx:39–44`.
- **Nachweis aus Source:** Listing akzeptiert höchstens 100, sortiert aufsteigend nach Erstellungszeit/ID und hat keinen Cursor/Offset. Frontend lädt genau eine `items`-Liste ohne Fortsetzung. Bei 101 lebenden Quellen bleibt die neueste außerhalb dieser Liste, auch nach Reload. Die Grenzen in `backend/migrations/versions/0006_quota.sql:103–106` betreffen ausstehende Arbeit, nicht die Gesamtzahl bereits verarbeiteter Quellen.
- **Erreichbarkeit/Vertrauen:** Gilt schon für den vertrauenswürdig aufgerufenen Listing-Service bei entsprechendem Datenbestand. Öffentliche UI-Auswirkung ist **bedingt** durch spätere GET-Registrierung/Capability-Aktivierung mit unverändertem Vertrag; am geprüften Stand nicht öffentlich erreichbar. Kein Tenant-Leak, kein neuer Defekt im ausdrücklich begrenzten One-page-Service; eine Lücke zur vollständigen Quellenansicht.
- **Kleinster Folgeschritt:** B4/Main entscheiden einen begrenzten, stabilen Cursorvertrag samt Fortsetzungsanzeige; keine unbeschränkte Liste und keine bloße Sortierumkehr als vermeintliche Vollständigkeit. Für den ersten kleinen TXT-Schnitt kann das Limit explizit bleiben, darf aber nicht als vollständiger Bestand ausgegeben werden.
- **Minimaler Test:** 101 kanonisch abgeschlossene Quellen, wiederholtes Laden und nächste Seite; alle Quellen genau einmal erreichbar, stabile Reihenfolge sowie unveränderte Notebook-/Mitgliedsfilter. Hier nicht ausgeführt.

### P2, unbestätigte Hypothese — Low-level Multipart-Syntaxfehler möglicherweise nicht normalisiert

- **Ort:** `backend/app/documents/multipart.py:103–127`; gefangen werden Starlettes `MultiPartException`, `TextRejected` und `TimeoutError`.
- **Offene Frage:** Ob der gepinnte Low-level Parser für fehlerhafte Boundary-/Header-Syntax eine andere Exception nach außen gibt. Die vorhandenen Malformed-Cases in `backend/tests/documents/test_multipart.py:68–76` belegen nicht allein alle Low-level Exception-Pfade. Installierte Parserimplementierung/Runtime wurden hier nicht unabhängig geprüft; deshalb **kein bestätigter Bug** und keine Behauptung eines heute erreichbaren HTTP500.
- **Erreichbarkeit/Vertrauen:** Erst ein authentifizierter, zugelassener Editor/Owner am künftigen POST dürfte den Parser erreichen; konkrete HTTP-Auswirkung hängt auch von dessen noch fehlender Fehlerabbildung ab. `finally: parser.close_spools()` bleibt unabhängig davon vorhanden; kein nachgewiesenes Spool-Leck.
- **Minimaler Entscheidungsnachweis:** Unter gepinntem Parser einen syntaktisch ungültigen Startdelimiter und eine ungültige Headerzeile senden; 400/opaque `invalid_upload`, geschlossene Spools und null DB/Storage-Aufrufe verlangen. Nur falls RED: konkrete Parserexception eng normalisieren, nicht pauschal `Exception`/Cancellation verschlucken.

### Keine neuen Findings: ausdrücklich getrennte Integrationsgrenzen

1. **Trusted worker ist kein öffentliches Actor-API.** `backend/app/workers/text.py:1–5,69–90,182–190` bindet den vom vertrauenswürdigen Aufrufer erhaltenen Actor. `backend/app/workers/dispatch.py:95–99` prüft den Typ, authentifiziert aber keine frei konstruierten `Identity`-Instanzen; genau das dokumentiert sein Modulvertrag. Ohne untrusted Aufrufpfad ist dies kein belegter Identitäts-Bypass. Produktions-Issuance bleibt ein eigener Arbeitsschritt.
2. **Quoten/Lease sind keine physische Quieszenz.** `backend/app/workers/text.py:229–251` behält bei `yielded` den Running-Versuch, beendet lokale Tasks und meldet keine Fertigstellung. Das garantiert weder Ende eines Threads noch Ende einer entfernten Operation. Nicht durch Token-Reset, Slotfreigabe oder unmittelbares Wiederholen „reparieren“.
3. **Fairness ist nicht One-shot-Sicherheit.** `backend/app/workers/dispatch.py:63–79,88–94,142–147` wählt genau einen frühesten sichtbaren queued/attempts0-Kandidaten; Sticky-Cancel wird erst beim Claim verbindlich abgewiesen. Ein späterer naiver Poller kann denselben nicht claimbaren Kandidaten wiederholt treffen. Das ist eine dokumentierte Scheduler-Lücke, kein neuer Defekt der zugesagten One-shot-Semantik. Kein Zugriff auf private Cancel-Metadaten allein zur Schedulerbequemlichkeit.
4. **Cleanup ist kein sofortiger DELETE-Fallback.** `backend/migrations/versions/0005_upload_lifecycle.sql:309–345` rediscoveriert auch `cleaned`; `backend/app/db/uploads.py:269–296` bietet nur Wrapper. Ein beendeter Cleanup-Pass garantiert keine ewige Abwesenheit bei verspätetem PUT. Fehlender Executor/Abwesenheitsnachweis ist ein Release-Gate, keine Widerlegung der bewusst erhaltenen Tombstones.

## Tatsächlicher vertikaler Bestand

| Übergang | Vorhanden | Noch nicht dadurch erledigt |
|---|---|---|
| Browser → Upload HTTP | Auth/CSRF-Bausteine, rohe Body-Grenze, expliziter Multipart-Parser | Kein registrierter Upload-POST; `backend/app/main.py:38–46` registriert keinen Document-Router, `backend/app/capabilities.py:28` bleibt false |
| Validierter Text → Reservation | `service.py:212–262`, `db/uploads.py:43–117`: Editorprüfung, Idempotenz, kanonische IDs, Quota-Gate, Commit vor I/O | Produktionskomposition frischer ActorTx/IoTx und begrenzter Acquisition |
| Reservation → privates Original | `service.py:263–304`, `storage.py:95–172`: Single-use Claim, fresh checks, non-upsert, privates Bucket-Metadatum, bounded Readback/Hash | Reales Storage HTTP/Policies/Runtime-Credentials; Metadatenprüfung ersetzt keine Policy-Abnahme |
| Original → Receipt → queued | `service.py:296–328`, `db/uploads.py:127–205`: Receipt, erneute Autorisierung, kanonischer Job, Commit vor Rückgabe | HTTP202-Mapping und Crash-/Ambiguitätsnachweis über die reale Kombination |
| queued → Textverarbeitung | `dispatch.py:82–147`, `text.py:182–251`: begrenzte One-shot-Selektion und bestehender Worker | Vertrauenswürdiger produktiver Caller/Actor-Issuer, dauerhafte Registry/Fairness, Restart-/Running-Recovery-Policy |
| Text → sichtbare Quelle | `text.py:152–180`: Chunks und Finish atomar; `service.py:175–210`: ausschließlich aktuelle sichere Projektion; D3a readonly UI | B4-GET integrieren/verifizieren, Gate beibehalten; Upload-UI fehlt |

TXT/Markdown ohne Embeddings ist der kleinste unterstützte Schnitt. PDF, Ersatzversionen, persönliche Provider, Chat und automatische Running-Recovery bleiben außerhalb. `ready` bezeichnet hier erfolgreiches Text-Processing, nicht belegte RAG-/Embedding-/Chat-Fähigkeit. Bestehende fremde Raw-Jobs müssen für die Mitgliedsansicht nicht lesbar gemacht werden.

## Kleinste fehlende Vertikale und nichtdefekter Gap-DAG

```text
A  akzeptierte Phase A (nicht erneut diagnostizieren)
└─ B  A5 Stage B: reales synthetisches Storage HTTP + private Policy-Abnahme
   ├─ C  Produktionskomposition: getrennte begrenzte ActorTx/IoTx, Storage-Konfiguration
   │  └─ D  default-closed Upload-POST: verified actor → admission/editor/CSRF
   │     → RawBodyBudget → parse_text_form → UploadService → committed receipt/202
   └─ R  separater Reconciler: enger Maintenance-Zweck, exakter Key,
         bounded delete/absence, retained tombstones, late-PUT-Revisits

I  expliziter vertrauenswürdiger Actor-/Job-Issuer (kein Browser-UUID)
└─ W  begrenzte One-shot-Ausführung des vorhandenen queued-only Dispatchers
   └─ T  vorhandener TextWorker → atomare Chunks/status

G  B4 readonly GET integrieren + D3a-Projektion testen (parallel zu B/D/I)
D + W + T + G → V  gated TXT/MD upload→ready→source-Nachweis

I → S  spätere dauerhafte Actor-Registry, bounded Scheduling/Fairness
S + separate Recovery-Entscheidung → Restart-/expired-running-Nachweis
R + physische Quieszenz-/Retention-Entscheidung → sichere Betriebsfreigabe
V + Betriebs-/Security-Nachweise → bewusste Capability-/UI-Freigabe
```

**Reihenfolge:** B4 nicht auf den Upload-POST warten lassen; Stage B nicht mit erneuter DB-Phase-A-Diagnose blockieren. D kann zunächst synthetisch/default-closed vorbereitet werden, reale Integration V benötigt B/C. Für V genügt ein ausdrücklich autorisierter One-shot-Caller; eine globale Actor-Registry ist keine Vorbedingung dieses begrenzten Nachweises. Umgekehrt ersetzt V nicht die laut Spezifikation erforderliche autonome Wiederaufnahme. R/Quieszenz müssen nicht die Implementierung unabhängiger read-only/POST-Tests blockieren, bleiben aber Gates für reale dauerhafte Upload-Nutzung.

### Konkreter nächster Implementierungsauftrag an Main/B

Nur den **default-closed TXT/MD POST** und seine Komposition ergänzen, neben dem bereits vergebenen B4-GET. Actor aus Supabase-verifizierter Session, Admission/Editor und exakte Origin-/CSRF-Prüfung **vor Body-Parsing**; keine automatische FastAPI-Dateibindung vor dem Limiter. Erforderlichen 1–128-Zeichen-Idempotency-Key prüfen, genau eine Datei, keine Connection-/Profile-/Replacement-Optionen. Bounded frische Transaktionen liefern, keine requestlange DB-Transaktion über Parsing oder Storage. Nur nach erfolgreichem Commit 202; vorhandene opaque Konflikt-/Quota-/Busy-/Storage-Fehler abbilden und Cancellation nicht als Erfolg behandeln. Kein Actor aus Requestparametern, keine Keys aus Dateinamen, kein Service-Role-Fallback, keine Capability-Aktivierung.

**Kleinste Abnahmematrix (vorgeschlagen, hier nicht ausgeführt):**

1. Auth/Admission/Viewer/CSRF-Negative vor Body-Empfang; malformed/überlange/abgebrochene Datei ohne Reservation oder Storage-Aufruf. Kein Rohfehler/Secret in Antwort.
2. Ein TXT und ein Markdown: POST → exakter privater Key → überprüftes Original → kanonischer queued Job → autorisierter One-shot Worker → ready/current-version GET; anderes Mitglied sieht safe Status, nicht Actor/Connection/Raw-Job.
3. Gleicher Key/Inhalt liefert dieselben IDs, abweichender Inhalt409; paralleler Replay erzeugt keinen zweiten PUT/Job. Commit-Failure erzeugt keine202; Stored-Replay wiederholt nicht I/O.
4. Revoke/Delete während I/O, late PUT, Timeout vor/nach Receipt, Crash nach Finalize vor Antwort: keine ungeprüfte Veröffentlichung; dauerhafte Wiederauffindbarkeit und gezielte spätere Reconciliation. Timeout ist kein Abwesenheitsbeweis.
5. Busy/Capacity und Deferred-Commit-Fehler auf realem DB-Pfad: ganze frische DB-Callbacks begrenzt wiederholen, nie externen PUT/Read oder ambigen Commit. Postclaim `yielded` hält Attempt/Lease; kein Running-Recovery durch diesen queued-only Slice.
6. Tenant-/Notebook-/Viewer-Denials auch am echten Storage-Pfad; B4/D3a zeigen nur aktuelle sichere Versionen. Capability bleibt bis zur ausdrücklichen Entscheidung false. Spätere 101-Quellen- und Fairness-Tests separat verfolgen.

## Prüfnachweis und Übergabe

Gelesen/geprüft wurden die relevanten Funktionskörper von Multipart/Body-/Textvalidierung, UploadService/Storage, Actor-/I/O-Transaktionen, Reservation/Finalisierung, Lease-/Quota-Helpers, TextWorker/Dispatcher und Quellen-Frontend sowie relevante Lifecycle-/Quota-DDL, Verträge und Handoff-Abschnitte. Historische Prüfergebnisse sind Kontext, keine hier erneut erbrachten Nachweise; es wird keine lückenlose DDL-/Abhängigkeitsprüfung behauptet. Keine Tests, Netzwerkzugriffe oder DB-Befehle ausgeführt. Abschlussprüfungen beschränken sich auf Dokumentpfade, Diff-Whitespace, ausschließlich erlaubten Git-Delta und normalen SecretGuard-Commit; Main verantwortet Integration und weitere Laufzeitprüfungen.
