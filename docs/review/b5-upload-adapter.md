# B5 Upload-Adapter: unabhängiges Delta-Review

## Stand und Urteil

- Exakte Lieferung: `a454f5a771b5c6c93cbe81923fc2f0e506967377`, Basis `24e33e6bb5108e6b935c1f04cda660d9b6be4631`.
- Gelesen: vollständiger neuer `upload_router.py`, vollständige geänderte Servicekörper, alle 674 Zeilen der eigenen Tests und B5-Handoff; dazu Auth/Session/CSRF, Multipart, Retry-Vertrag, Produktspezifikation und HTTP-Verträge. Main-GET/Root-Mount zusätzlich auf `1f1777b` gelesen; relevante gemeinsame Auth-/Session-/Multipart-Dateien sind gegenüber der Basis unverändert.
- Originalworker-Workspace vor/nach Prüfung auf dem exakten Liefer-SHA, nur dessen vorhandenes `.pi/` untracked. Kein Merge/Reset/Checkout und keine Implementierungs-/Teständerung durch diesen Reviewer. Eigene Ausgabe ausschließlich dieses Dokument.
- **Kein bestätigter Produktions-Sicherheitsdefekt im Delta. Ein bestätigter P2-Testdefekt:** Die Unregistered-Assertion erkennt eine tatsächlich registrierte Route unter der gepinnten FastAPI-Version nicht. Empfehlung: eng korrigieren und bei Main-Integration durch wirksamen Root-Mount-/Gate-Nachweis ersetzen. Kein Anlass, den funktionalen Adapter neu zu entwerfen oder bekannte Produktionsgates als neue Defekte auszugeben.

## P2 — bestätigter Testdefekt: Registration-Negative ist falsch grün

**Ort:** `backend/tests/documents/test_upload_router.py:590–595`, insbesondere `:595`.

Die Prüfung iteriert nur `app.routes` und vergleicht jeweils `r.endpoint` mit `module.upload_document`. FastAPI 0.162.4 hält eingeschlossene Router in dieser Sicht lazy/nested; die Route kann wirksam registriert sein, ohne dass ihre Endpointfunktion dort direkt erscheint. Die Assertion beweist deshalb nicht die behauptete Nichtregistrierung.

**Unabhängiger tatsächlicher Nachweis, ohne Dateiedits oder DB:** In einem frischen `FastAPI()` wurde exakt `module.router` aus a454f5a mit `include_router` eingebunden. Anschließend wurden die bestehende Assertion und das tatsächlich generierte OpenAPI-Mapping ausgewertet:

```text
existing_test_assertion_passes_with_registered_router= True
actual_post_registered= True
```

Die zweite Prüfung war `'post' in app.openapi()['paths']['/api/notebooks/{notebook_id}/documents']`. Keine Auth-, Netzwerk-, DB- oder Storageaktion nötig. Das ist ein echtes negatives Kontrollresultat der **Testaussage**, kein behaupteter fehlgeschlagener Produktionsupload und kein künstlich deklarierter pytest-RED.

**Reichweite:** Jeder versehentliche Root-Mount derselben Route kann von diesem Test unbemerkt bleiben. Im gelieferten Source ist sie tatsächlich nicht registriert; das wurde unabhängig am `main.py`-Delta geprüft. Selbst ein Mount öffnet den Literal-True-Gate nicht automatisch. Daher Testabdeckung P2, **kein** aktuell nachgewiesener öffentlicher Upload oder Auth-Bypass. Entspricht der bereits bei Main beobachteten Lazy-Router-Introspektionsklasse, nun konkret in B5 bestätigt.

**Minimaler Fix/Test:** Für diesen unregistrierten Liefercheckpoint das generierte Schema oder eine wirksame Routerauflösung auf das Fehlen der POST-Operation prüfen; positive Kontrolle mit bewusst eingebundenem Router muss die Abwesenheitsprüfung widerlegen. Bei der später gewollten Main-Registrierung die alte Unregistered-Behauptung entfernen/ersetzen: POST-Operation tatsächlich vorhanden, bei geschlossenem Gate opaque404 und null Identity/Service/Body-Aufrufe; GET am gleichen Pfad weiterhin vorhanden. Nur eine 404 ohne Wirksamkeitskontrolle unterscheidet fehlende Route und geschlossenes Gate nicht zuverlässig.

## Geprüfte Sicherheits- und Transaktionsgrenzen

| Fokus | Source und Bewertung |
|---|---|
| Gate vor Arbeit | `upload_router.py:116–120`: nur `documents is True`; geschlossen vor Identity, Service und Body, auch bei ungültiger Notebook-ID. Capabilities ist die feste vertrauenswürdige Serverfunktion, kein Requestobjekt. Dass B4 zusätzlich fehlerhafte Capability-Provider defensiv schließt, belegt hier keinen Browser-Bypass. |
| Auth/CSRF vor Body | `upload_router.py:120–127`: vorhandene `identity(request)`, dann bestehender exakter Origin-/CSRF-Check, dann Notebook-/Service-Preflight. `auth/supabase.py:42–51` verifiziert via Supabase und konstruiert die Identity; keine Actor-ID aus Form/Query. |
| Originalidentity und Rollen | `upload_router.py:44–61`: ursprüngliche Identity bleibt im Closure einschließlich E-Mail; abweichende UUID wird vor Factoryöffnung abgewiesen. `db/session.py:46–65` verlangt direkten eingeschränkten Login, setzt transaktionslokale Subject-/Email-Claims und committet beim Context-Exit. `service.py:107–147` prüft Actor-/I/O-Principals und Admission erneut. Ein serverseitig ausgetauschter Injector ist eine Trust-Grenze, kein belegter untrusted Pfad. |
| Write-Preflight | Neu `service.py:212–216`, erneut `:231`: tatsächliche Admission-/Notebook-/Owner-Editor-Prüfung in kurzlebiger `_db`-Transaktion; nach deren Exit Storagekonfiguration prüfen. Erst danach `upload_router.py:134` Parser. Kein Request-DB-Context überspannt das Body-await. Revocation wird nach Parsing erneut und vor Finalize geprüft; kein Anspruch auf physisch atomare Revocation aller entfernten I/O. |
| Idempotenz | `upload_router.py:128–133`: genau ein roher Header, keine Zusammenführung; ASCII `[A-Za-z0-9._:-]{1,128}`, keine leeren/überlangen/Whitespace-/Komma-/Nicht-ASCII-Schlüssel. Originalschlüssel unverändert an Service; keine clientseitigen Actor-/Storagekeys oder optionalen Embedding-/Replacement-Felder. Das ist bewusst enger als die allgemeine 1–128-Zeichen-Formulierung im Vertragsdokument und steht im B5-Handoff; Main sollte die konkrete HTTP-Grammatik bei Integration im maßgeblichen Vertrag nachtragen. |
| Kein äußerer I/O-Retry | `upload_router.py:135–140` ruft `service.upload` genau einmal auf. Bestehendes `_db`/`short_transaction` wiederholt nur eng klassifizierte komplette frische DB-Callbacks; PUT/Read liegen außerhalb. Keine neue Wiederholung eines ambigen Commit oder kompletter Uploads. |
| Erfolg nach Commit | `service.py:310–332` liefert seine Receipt erst nach Context-Exit/Commit. `upload_router.py:141–143` verlangt exakten bestehenden `UploadReceipt`-Typ, Response-Modell ist dieser Typ. Typprüfung alleine wäre kein Commitbeweis; hier kommt die Zusage aus dem konkreten vertrauenswürdigen Service. Synthetischer beliebiger Service könnte sie brechen, ist aber kein Browserinput. |
| Opaque Fehler | `upload_router.py:93–112,144–145`: feste Codes/Status/Texte, unbekannt503. Exakte Exception-/String-/Dict-Typen; Iteration der Builtin-Dict-Items statt Lookup mit möglichem feindlichem Key-Equality-Hook. Keine Übernahme freier Exception-Message oder -Status. Body-Timeout408 versus Service-Timeout503 sind bewusst getrennt. |
| Cancellation/Cleanup | `upload_router.py:144` fängt `Exception`, nicht `asyncio.CancelledError`; vorhandenes Parser-finally schließt Spools. Die lokale echte Parser-Normalisierung wurde zuvor auf `02f887d` verifiziert, kein neuer Catch nötig. Task-Cancellation ist weiterhin kein Beweis für Ende einer entfernten Operation. |
| B4-Koexistenz | B4 GET und B5 POST verwenden dieselbe Pfadform mit unterschiedlichen Methoden und ohne Crossimports; kein neuer Konflikt im überprüften Source. Main `1f1777b` registriert GET bei false Capability, B5 selbst registriert POST nicht im Root. |

## Tatsächlich ausgeführte Prüfung

Im unveränderten Originalworker-Workspace `notebook-upload-http-adapter` auf a454f5a, bestehende Main-venv `../notebook-integration/.venv`, keine Installation:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  ../notebook-integration/.venv/bin/python -m pytest -q -p no:cacheprovider \
  --tb=no --show-capture=no -o log_level=CRITICAL \
  backend/tests/documents/test_upload_router.py
# 59 passed in 2.89s
```

Zusätzlich obige echte In-process-Registration-Kontrolle mit derselben Umgebung. Keine DB-Tests, Netzwerk-/Remote-/Containeraktionen, Servicefälle mit PostgreSQL oder Produktions-Storageprüfung. Keine Body-/Credential-/Rohfehlerausgabe. Frühere B5-Zahlen 91 passed/12 skipped sind dessen Handoff-Evidenz, nicht dieser Reviewlauf.

Die 59 Fälle verwenden überwiegend synthetische Services. `test_real_service_preflight_and_upload_awaits_outside_synthetic_transactions` (`test_upload_router.py:335–492`) führt den echten Service mit geskripteten DB-/Storage-Doubles aus: frische Actor-Sessions, Preflightcommit vor Parser, keine aktive Transaktion bei Storage-awaits und Finalizecommit vor202. Das ist wertvolle Strukturprüfung, aber weder RLS- noch Wire-COMMIT-Evidenz. `:527–540` belegt einmaligen Adapteraufruf nach synthetischem ambigem Fehler, nicht den Ausgang eines echten Commit.

## Eng begrenzte nächste Abnahme

1. **Erforderlich für belastbare Registration-Aussage:** P2-Test oben korrigieren; wirksame positive/negative Root-Kontrolle auch nach Zusammenführung mit B4.
2. **Sinnvolle kleine Ergänzung, kein bestätigter Produktionsbug:** Mit realem `UploadService.authorize_upload` und geskriptetem Session-Factory-Exit einen Preflight-Commitfehler auslösen; opaque503, kein Body-Receive/Parser/PUT. Aktuelle Negativtests injizieren größtenteils eine fertige Preflight-Exception, der echte Servicepfad ist hier überwiegend Happy-path.
3. **Sinnvolle lokale Randfälle:** Schlüssel exakt128 und erlaubte Satzzeichen als positive Kontrolle; doppelte Header mit unterschiedlicher Groß-/Kleinschreibung negativ; tatsächliche Admission-/Viewer-Verweigerung im Service-Doublepfad vor Body. Die vorhandene Implementierung behandelt diese Fälle nach Source korrekt; das sind zusätzliche Nachweise, keine erfundenen Findings.

Kein Reviewauftrag zur Implementierung dieser Punkte wurde hier ausgeführt. Die bereits separat getrackten Runtime-Storage-/Issuer-/Reconciliation-Gates und Pagination werden nicht als neue B5-Defekte wiederholt. Main bleibt für Integration, Vertragspflege und kombinierte Abnahme zuständig; dieses Urteil erlaubt keine Capabilityöffnung.