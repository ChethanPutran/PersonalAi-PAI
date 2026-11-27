# TODO: suggestions and improvements

Not started. Items are grouped by area; nothing here is a known regression.

## Capability retrieval (planner)
- [ ] Turn on `orchestration.capability_retrieval` and compare traces (`retrieval`, `retrieval_shown`, `retrieval_of`, `retrieval_missed` on the planner span). Raise `capability_retrieval_families` if re-plans are frequent.
- [ ] Wire `context["last_capabilities"]` from the previous turn so follow-ups ("do that again") keep the right capabilities. Until then, short follow-ups with history fall back to the full list.
- [ ] Embedding-based ranker (reuse the existing embedding model) if word matching falls back to the full list too often. Index capability id + description + parameter names per user; re-index on plugin install/remove/enable.
- [ ] Re-run `scripts/replay_capability_select.py` on new traces periodically; only 59 of 160 recorded plans were actually shortened.
- [ ] Send full parameter schemas only for the chosen capabilities and a one-line "also available" list for the rest.

## Document search (RAG)
- [ ] PDF text extraction on Android (docx and text only today). Needs a Dart or native library (e.g. pdfbox-android via the plugin channel) and a test on a real phone; skip encrypted and scanned PDFs with a reason. Remove `kPdfSupportNote` afterwards.
- [ ] "Append" option on the document upload API so files longer than 400 chunks can be indexed in parts.
- [ ] Try real PDF extraction on Linux (`pypdf` is not installed in this environment, so it is untested).
- [ ] Check real search relevance with the embedding model on real documents.

## Plugin settings and devices
- [ ] On reconnect the device applies the backend's saved settings even if a live `plugin.config` frame was refused; decide whether that is right.
- [ ] Linux device: a corrupt local settings file is skipped silently; surface it to the user.
- [ ] Android 10 and older: shared-folder writes depend on the existing storage permission; verify on an old device.
- [ ] Confirm the Linux native files plugin really reads `PAI_FILES_ROOTS` (the `;` separator is confirmed, the reading was assumed).

## Scheduling
- [ ] The planner can create duplicate schedules for one request (seen with two "Call mom" schedules). Dedupe in the planner or in `schedule.create`.

## App
- [ ] Flutter Help screen (the web Help page exists and is linked from Settings).

## Verification and housekeeping
- [ ] Run everything on a real phone and a live backend: restart backend, rebuild/reinstall the Android app, reinstall files 1.1.0, grant All files access, switch on "Index documents", restart the Linux agent.
- [ ] Stop any leftover Gradle daemon (`pkill -f GradleDaemon`).
- [ ] Rotate the exposed Docker Hub token.
- [ ] Known failing tests, unrelated to recent work: `tests/memory/test_memory.py` (stale), `tests/llm/test_airouter_provider.py`, flaky `tests/admin/test_admin_history.py`, consent `conftest` clash when directories run together, Flutter chat golden test hangs.
