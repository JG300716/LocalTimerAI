# LocalTimerAI — LocalFlow

[Polski](README.md) | [English](README.en.md)

A local work-time tracker and dashboard for Windows. No account, subscription,
externally hosted scripts, or history retention limit. The application and website
run on the same computer; the server listens only on `127.0.0.1`.

This is the English documentation. The application interface currently uses
Polish labels; their original names are included below where needed.

## Getting started

Download the Windows package from [GitHub Releases](https://github.com/JG300716/LocalTimerAI/releases),
extract the entire archive into a writable folder, and install **Python 3.10+**
if it is not already available as `py -3` or `python`. The package requires
Windows 10/11 and .NET Framework 4.x. It does not bundle Python or user data.

1. Run **LocalFlow.exe** from the extracted folder. A window with tracker status
   and **Start**, **Stop**, and **Exit** buttons appears on the Windows taskbar.
   A system tray icon is also available beside the clock. Open the dashboard at
   **http://127.0.0.1:48763**.
2. Try **Midnight**, **Paper**, **Terminal**, and **Focus** at the top of the page.
   Your browser remembers the selected theme. Every theme displays real data
   from the local database. Views remain empty when there are no measurements.
3. In **Ustawienia** (Settings), edit the process list and connect WakaTime
   if you want more detailed information from your IDE.

No pip packages are required. `LocalFlow.exe` is a small Windows/.NET Framework
launcher for the included Python server. Keep the entire folder together when
moving the application. You can also start the server with `Start.cmd`.

Closing the browser does not stop collection. **Start** resumes `.exe` tracking,
**Stop** pauses it, and **Exit** stops the service and closes the application.
The tray menu provides the same commands. While process tracking is paused,
the dashboard remains available; IDE plugins and AI log collection operate
independently. Minimizing keeps the application on the taskbar. `--no-browser`
skips opening a browser; `--background` starts with only the tray icon.

Autostart is optional. Run `EnableAutostart.ps1` or `DisableAutostart.ps1` to
manage the LocalFlow shortcut in the current user's Startup folder. It is not
enabled automatically.

The optional `BuffedTestVoid.vbs` example starts `LocalFlow.exe --no-browser`
from its own folder, then launches the desktop shortcut `TestVoid.exe.lnk`
if it exists. Set the `LOCALFLOW_HOME` environment variable to use another
application folder. This example neither installs nor requires TestVoid.
Repeated launches bring back the existing LocalFlow instance.
`StartHidden.ps1` and `StartHidden.vbs` start only the server without a window;
`Stop.cmd` stops that server.

The tray actions **Otwórz dashboard — moje dane** (Open dashboard — my data)
and **Podgląd stylów UI** (Preview UI themes) both open real data at
`http://127.0.0.1:48763/?mode=live#overview`. Change themes at the top of the
page. Old demo links also display only local measurements.

## What it tracks

- **IDEs through WakaTime:** projects, languages, editors, categories, and file
  activity. Existing Rider/JetBrains, VS Code, and other plugins using WakaTime
  CLI can send data to LocalFlow. Each IDE needs its own compatible plugin.
- **Windows processes:** common IDEs are included in the example configuration.
  Add other `.exe` files in `config.json` or the dashboard editor. The
  `foreground` mode measures the active window and excludes idle time. The
  `running` mode measures process presence, including background execution,
  as in NostaleTestingTimer. Both modes exclude locked sessions and suspend gaps.
- **AI:** available local **Codex** and **Claude Code** logs. A separate view
  shows models, input/output tokens, cached tokens, and observed agent-turn
  duration. Concurrent turns may overlap. This is neither exact inference time
  nor human work time. Conversation contents and source code are not stored.

The dashboard merges overlapping IDE and foreground-window intervals, giving
IDE records priority. Project filtering happens after merging. `running`
process time and AI time are shown separately. An IDE heartbeat describes
the interval until the next heartbeat, capped by the configured idle timeout
(120 seconds by default); the last interval may still be growing. This is an
estimate based on heartbeats, not a measurement of every second of editing.

The daily average uses active days. Dates default to `Europe/Warsaw`, including
23- and 25-hour days at daylight-saving transitions. Other time zones can be
configured in JSON; Windows may need the optional `tzdata` package for them.

## Connecting WakaTime

Under **Ustawienia → Wtyczki IDE** (Settings → IDE plugins), choose one of:

- **Podłącz lokalną kopię** (Connect a local copy): adds a local receiver through
  `[api_urls]` while preserving delivery to the existing WakaTime service.
  This requires a recent WakaTime CLI.
- **Główny zapis lokalnie** (Use a local primary endpoint): changes the primary
  endpoint and key to LocalFlow. Additional mirror rules remain in the file and
  may continue sending data to other services.

A timestamped backup of `%USERPROFILE%\.wakatime.cfg` is created before changes.
**Restart your IDE** after connecting it. API settings inside the plugin may
override this file. Normal application startup does not change WakaTime
configuration. Restore the backup to undo the change.

See [INTEGRATIONS.en.md](INTEGRATIONS.en.md) for protocol details and sources.
Endpoint compatibility was tested with an installed WakaTime CLI using separate
configuration, a temporary database, and a synthetic heartbeat, without sending
it to the cloud.

## Editing tracked executables

Add an entry to `applications` in **config.json**:

```json
{
  "exe": "MyApplication.exe",
  "label": "My tests",
  "category": "testing",
  "project": "My project",
  "mode": "running"
}
```

Use the filename only, without a full path. Matching is case-insensitive.
Changes are loaded automatically without a restart. An invalid edit does not
replace the last valid configuration. Saving through the dashboard creates
`config.json.bak`. Changing the server **port** requires an application restart.

## Data, import, and export

- `data/activity.sqlite3`: the local event database, with no automatic deletion.
- `data/local.json`: local API keys and protection for write operations.
- `data/localflow.log`: application diagnostics.
- `config.json`: editable process, AI collector, and server settings.

Stop the application before copying the database for a backup. While running,
SQLite also uses `-wal` and `-shm` files. The data directory contains private
activity metadata and local keys; it is excluded from the source package and
repository.

Importing a NostaleTestingTimer `.txt` file preserves its original **weekly
totals** in the archive section. These values are not added to exact daily
totals or assigned to projects. Importing the same week again updates it
instead of duplicating it. Explicit `Test - ` entries and comments are skipped;
other invalid rows are reported.

CSV/JSON export uses the selected date range and project. CSV contains raw
measurements from different sources, which can overlap. JSON also includes
the weekly archive. The first AI scan imports available historical logs;
later scans read newly appended bytes.

## Measurement limits

LocalFlow provides local equivalents of the main dashboard views, not every
WakaTime feature. It cannot reconstruct cloud history that is unavailable
locally. Token counts alone do not establish subscription/API costs, AI line
counts, AI's share of the code, or human-review percentages. Cloud-only browser
chats and other AI tools need dedicated adapters. Changes to client log formats
may require parser updates.

## Running and testing from source

After cloning, create your own configuration and build the launcher. Do not
overwrite `config.json` when updating an existing installation. Git ignores
this file, the local database, keys, and build artifacts.

```powershell
git clone https://github.com/JG300716/LocalTimerAI.git
cd LocalTimerAI
Copy-Item config.example.json config.json
.\BuildLauncher.ps1
.\LocalFlow.exe
```

Other useful commands (Node.js is needed only for frontend tests):

```powershell
python app.py
python app.py --no-browser
python app.py --no-tracking --port 48764
python app.py --stop
python -m unittest discover -v
node --check web/app.js
node --test web/test_app.cjs
python verify_wakatime_cli.py
python verify_wakatime_cli.py --mirror
.\scripts\Build-Release.ps1
```

`--no-tracking` lets you inspect the dashboard without reading processes or AI
logs; the heartbeat endpoint remains available. `--config` and `--data-dir`
allow isolated configurations and tests. Verification covers interval merging,
daylight-saving transitions, import idempotence, AI metadata formats, local
HTTP boundaries, and launcher startup from folders containing spaces.
Verify the actual plugin connection in each IDE after restarting it.
See [TESTING.en.md](TESTING.en.md) for coverage and limitations.

To update, close the application with Exit, preserve your `config.json` and
the entire `data` folder, then replace the program files. The package does not
migrate or delete user history.
