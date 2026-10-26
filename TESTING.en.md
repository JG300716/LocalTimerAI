[Polski](TESTING.md) | [English](TESTING.en.md)

# Testing LocalFlow

Run the tests from the project directory. The application uses the Python
standard library; frontend tests require Node.js with the built-in `node:test`
module (Node.js 18 or later). Native launcher tests require Windows, .NET
Framework, and its C# compiler. Tests of the executable require a built launcher.

## Commands before a release

```powershell
node --check web/app.js
node --test web/test_app.cjs
python -m unittest discover -v
```

On Windows, build the launcher before running tests of its executable:

```powershell
.\BuildLauncher.ps1
python -m unittest test_launcher -v
```

Before testing the release package, build the archive and check its extracted contents:

```powershell
.\scripts\Build-Release.ps1
python -m unittest test_release -v
```

The `.github/workflows/ci.yml` workflow builds the launcher and package on Windows,
runs the full Python/Node test suite, and uploads the verified ZIP as an artifact.
It does not change the user's actual IDE configuration or startup settings.

The optional test using the real WakaTime CLI requires an installed client. You
can specify its executable with `--cli`:

```powershell
python verify_wakatime_cli.py
python verify_wakatime_cli.py --mirror
```

When assessing the results, check for `skipped` tests too. Native tests may be
skipped if Windows, the compiler, or the launcher executable is unavailable;
a skipped test does not confirm that the feature works. Node is not required
to use the application.

## Automated test coverage

### Frontend — `web/test_app.cjs`

The tests execute the production `app.js` in an isolated JavaScript environment
with a mock DOM and mocked HTTP responses. Test data exists only in the test
file; the dashboard does not generate sample activity.

- Six views: overview, projects, applications, AI, timeline, and settings.
  Rendering is checked for all four themes—Midnight, Paper, Terminal, and
  Focus—including an empty API response.
- Old `mode=demo` links and a saved demo preference cannot restore fictitious
  data. Other URL parameters and the selected view are preserved.
- Navigation after selecting a project and during a delayed settings request;
  project filtering; date ranges spanning a daylight saving time transition.
- Automatic and manual refresh, preservation of the active search field,
  connection failures, and discarding stale results after a filter change.
- Invalid JSON cannot replace a valid configuration; the draft remains
  available. Changing the port displays a restart requirement.
- WakaTime integration and pause commands send requests to the local API;
  import passes the file's text, while CSV/JSON export downloads the API
  response. Export failures do not create a substitute file.
- HTML escaping of user metadata and preservation of the full chart total
  when less active projects are grouped together.

These tests do not run a browser engine. They verify logic and generated HTML,
not the actual CSS layout, element positions, browser keyboard handling, or
chart appearance at every screen resolution. Styles need a separate visual
review; checking CSS source does not replace that review.

### Backend and HTTP — `test_backend.py`

- Merging overlapping measurements with IDE priority, filtering after merging,
  clipping intervals to the selected range, and keeping AI and background
  process time separate.
- Day boundaries, midnight, daylight saving time, and consistency of the
  fallback time zone implementation.
- Atomic configuration writes, backups, recovery after an invalid edit, and
  preventing returned objects from mutating the active configuration.
- Event idempotency, coalescing process samples, and bounding heartbeat duration.
- Real requests to an isolated loopback server: Host, Origin, write token,
  single and bulk heartbeats, invalid JSON, and CSV export.
- Server shutdown and restart while preserving configuration and data.

### Collectors — `test_tracker.py`, `test_integrations.py`

- Separating active-window time from process presence; idle time, session
  locking, sleep, clock changes, optional window titles, and configuration changes.
- Pausing discards a sample obtained during shutdown; resuming does not count
  the paused interval as work time.
- Importing the legacy timer preserves weekly precision and does not duplicate entries.
- Recognizing the editor from heartbeats and handling local AI metadata formats:
  partial lines, resuming reads, cumulative counters, deduplication, cache,
  invalid dates, and interrupted imports.
- Updating the WakaTime configuration preserves its primary settings, existing
  rules, and a backup. These tests use temporary files.

### Windows launcher — `test_launcher.py`

The tests use a separate directory and test services. They cover detecting open
and closed ports, stopping an inactive service again, Start/Stop, shutting down
the application message loop and backend, exiting during startup, and preventing
a second instance in the same directory. Some cases use the real backend;
others use a mock service.

This verifies native code and lifecycle behavior. It does not confirm manual
clicking of every button, startup integration, the system tray, the user's
script, or taskbar icons on every Windows configuration.

### WakaTime protocol — `verify_wakatime_cli.py`

The script runs the real CLI client with a temporary configuration, a separate
database, and a single controlled heartbeat. With `--mirror`, the primary
destination is also a local test server. The script does not use the actual
offline queue, change the user's configuration, or send heartbeats to the cloud.
It checks that LocalFlow stores the heartbeat and identifies the editor.

### Windows package — `test_release.py`

- Exact ZIP file list, CRC integrity, and SHA256 checksum.
- Configuration comes from `config.example.json`; no private history, local
  keys, logs, backups, or demo data is included.
- Starting the extracted server with a separate port and database; HTML, JS,
  CSS, and API responses; authorized shutdown and process termination.
- Internal self-test of the EXE extracted from the archive. The package
  requires a separate Python installation.

## Limits and verification in the actual environment

A successful CLI test does not confirm the configuration of a particular Rider,
VS Code, or other IDE plugin. After intentionally connecting and restarting the
IDE, confirm that a new heartbeat appears in the local dashboard. Plugin settings
may take precedence over `.wakatime.cfg`.

The **Główny zapis lokalnie** option (local primary destination) changes the
primary WakaTime endpoint and key. It preserves additional destinations from
`[api_urls]`, which may continue receiving data. This is neither a test nor a
guarantee that all plugin network traffic has been disabled.

Windows collector tests use controlled samples. They do not replace testing
actual active windows, idle time, session locking, and system sleep. AI parser
tests cover supported sample logs; they do not confirm compatibility with every
future client version or measure exact inference time.

Before publishing a package, separately verify startup from the extracted
directory and the absence of a private database, local keys, logs, and user
configuration. Results from mock-based tests must not be described as a complete
test of the user's actual environment.
