# Admin UI screenshots

Real screenshots of the admin panel in headless Chrome, kept here like Flutter goldens so UI
changes are visible in review. They are produced against the REAL app (isolated temp DB) after
seeding a realistic mix of runs through the API (completed / failed / skipped / cancelled /
running / pending nodes).

Regenerate:

```bash
.venv/bin/python tests/admin/serve_isolated.py --port 8765 --data-dir /tmp/pai-admin-demo &   # prints READY 8765
node tests/admin/browser/screenshots.mjs http://127.0.0.1:8765                      # writes ./screenshots/*.png
```

Needs Node >= 22 and Google Chrome/Chromium (set `CHROME=/path/to/chrome` if it is not `google-chrome`).
`cdp.mjs` is a dependency-free DevTools-protocol driver. The script also prints the browser console;
any `error:` line there is a UI bug.

The script drives the INTEGRATED page (`GET /api/v1/system/admin`): it logs in through the page's own
form, visits every ORIGINAL tab (overview, devices, plugins, users, tasks, traces incl. opening a trace,
raw) and every new tab (Live DAGs, Active Tasks, Playground, Events, Resources), checks the sidebar
badges, the Active Tasks -> DAG deep link and sign-out, and prints PASS/FAIL per check (exit code 1 on any FAIL).

| file | what it shows |
|---|---|
| 01-login | the existing page's sign-in overlay |
| 02..08 `*-existing` | original tabs (overview, devices, plugins, users, tasks, traces, raw) — regression proof |
| 09-active-tasks | new tab: live table of not-finished tasks with Pause / Kill |
| 10-dag, 11/12-dag-node-* | new tab: live DAG with every node state, minimap, node panel with Kill |
| 13-kill-confirm | confirm dialog (rendered inside the tab's shadow root) |
| 14/15-playground, editor-error | demos, goal runner, JSON editor with inline error |
| 16-events | live event firehose |
| 17-resources | process / memory / component overview |
| 18/19-tablet-* | 820 px wide layout |
| 20-after-logout | page's own sign-out returns to the login overlay |
