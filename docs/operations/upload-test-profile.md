# Private upload and processing test profile

`compose.upload-test.yaml` is an opt-in overlay for an isolated test database.
It is not loaded by the kube1 deployment helper and does not enable chat or
configure a personal provider. Do not apply it to `notebook-poc`.

## Prerequisites

- Use a separate Compose project, original-file directory, database volume,
  and staged test-only files. Use the approved immutable API/frontend images.
- Initialize and migrate that database through the established private
  operator procedure. The test API login uses its own app DSN. The storage IO
  login can assume only `notebook_storage_io`; the worker scheduler uses
  `notebook_text_scheduler`. Do not substitute a database administrator DSN.
- Set `NOTEBOOK_UPLOAD_TEST_ORIGINALS`, `NOTEBOOK_UPLOAD_TEST_IO_FILE` and
  `NOTEBOOK_UPLOAD_TEST_SCHEDULER_FILE` to absolute paths in that isolated
  staging area. Files must be private, owned by the effective container UID
  10001, and not symlinks. Give the same UID a private originals directory.
  Never print DSNs or render the populated Compose config into logs.

## Start and verify

Use both Compose files, in order: `compose.poc.yaml` and
`compose.upload-test.yaml`. Check `config --quiet`. Use project
`notebook-upload-test` or a dedicated unique test project. By default only
`db`, `auth`, and `api` are selected. Do not run `up` with the browser profile:
its inherited frontend mapping is the live preview port. On the test host run
`compose run --no-deps --use-aliases -d -p 127.0.0.1:18192:8080 frontend`
with the same two files and project; use a separate private TLS preview for it.

Sign in as an authorized test user and upload a small synthetic TXT file. Run
`compose run --rm --no-deps text-worker` with the same project and files. It
handles at most one dispatch; `held` is not success. Verify the document state
and open the extracted text in the browser. Worker mounts are read-only;
only the API receives the upload IO DSN and writable originals mount.
No unattended schedule is introduced by this overlay.

A working chat and citation require an actual chat endpoint, authorized
retrieval, personal provider setup and their own real browser acceptance.
