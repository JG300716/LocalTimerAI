# LocalTimerAI — LocalFlow

Lokalny tracker pracy i dashboard dla Windows. Bez konta, abonamentu, zewnętrznych
skryptów ani limitu przechowywania historii. Aplikacja i strona pracują na tym
samym komputerze; serwer słucha wyłącznie na `127.0.0.1`.

## Uruchomienie

Pobierz paczkę Windows z [GitHub Releases](https://github.com/JG300716/LocalTimerAI/releases),
rozpakuj cały katalog w miejscu z prawem zapisu i zainstaluj Python **3.10+**,
jeśli nie jest dostępny jako `py -3` albo `python`. Paczka wymaga Windows 10/11
i .NET Framework 4.x; nie zawiera środowiska Python ani Twoich danych.

1. Uruchom **LocalFlow.exe** z rozpakowanego katalogu. Pojawi się okno ze stanem trackera oraz przyciskami
   **Start**, **Stop**, **Exit**, widoczne na pasku zadań Windows. W zasobniku
   przy zegarku również jest ikona aplikacji. Dashboard działa pod adresem
   **http://127.0.0.1:48763**.
2. U góry strony porównaj **Midnight**, **Paper**, **Terminal** i **Focus**.
   Wybór jest zapamiętywany w tej przeglądarce. Każdy styl pokazuje wyłącznie
   rzeczywiste dane z lokalnej bazy. Jeśli nie ma pomiarów, widok pozostaje pusty.
3. W **Ustawieniach** edytuj listę procesów i podłącz WakaTime, jeżeli chcesz
   zbierać dokładniejsze informacje z IDE.

Nie są wymagane pakiety pip. `LocalFlow.exe` jest
małym launcherem Windows/.NET Framework, uruchamiającym dołączony serwer Python;
przenosząc aplikację, zachowaj cały katalog. Alternatywa: `Start.cmd`.

Zamknięcie strony nie zatrzymuje zbierania. **Start** wznawia pomiar `.exe`,
**Stop** go wstrzymuje, a **Exit** zatrzymuje usługę i zamyka aplikację.
Te same polecenia są w menu ikony przy zegarku. Podczas Stop dashboard nadal
działa; pluginy IDE i odczyt AI są niezależne od pauzy procesów.
Minimalizacja pozostawia aplikację na pasku zadań. `--no-browser` pomija
otwarcie przeglądarki; `--background` wybiera samą ikonę w zasobniku.

Autostart jest opcjonalny: uruchom `EnableAutostart.ps1` albo
`DisableAutostart.ps1`. Skrypty zarządzają wyłącznie skrótem LocalFlow w folderze
autostartu bieżącego użytkownika. Nie zostały automatycznie włączone.

Opcjonalny przykład `BuffedTestVoid.vbs` uruchamia `LocalFlow.exe --no-browser`
ze swojego katalogu, a następnie skrót `TestVoid.exe.lnk` z pulpitu, jeśli istnieje.
Można wskazać inny katalog aplikacji zmienną środowiskową `LOCALFLOW_HOME`.
Przykład nie instaluje ani nie wymaga TestVoid. Ponowne uruchomienie przywołuje
tę samą instancję LocalFlow. `StartHidden.ps1` i `StartHidden.vbs` uruchamiają
sam serwer bez okna; `Stop.cmd` zatrzymuje serwer.

Menu ikony w zasobniku udostępnia **Otwórz dashboard — moje dane** oraz
**Podgląd stylów UI**. Oba otwierają rzeczywiste dane pod adresem
`http://127.0.0.1:48763/?mode=live#overview`. Motywy zmieniasz u góry strony.
Stare odnośniki do trybu demo również pokazują wyłącznie lokalne pomiary.

## Co mierzy

- **IDE przez WakaTime:** projekty, języki, środowiska, kategorie i aktywność
  plików. Działa z istniejącymi pluginami Rider/JetBrains, VS Code i innymi
  korzystającymi z WakaTime CLI. Podłączenie konkretnego IDE wymaga pluginu.
- **Procesy Windows:** znane IDE są już na liście; własne `.exe` można dodać
  w pliku `config.json` lub edytorze na stronie. Tryb `foreground` mierzy aktywne
  okno i pomija bezczynność. Tryb `running` mierzy obecność procesu również w tle,
  jak NostaleTestingTimer. W obu trybach pomijane są blokada sesji i uśpienie.
- **AI:** dostępne lokalne logi **Codex** oraz **Claude Code**. Osobna zakładka
  pokazuje modele, tokeny wejściowe/wyjściowe, cache i zaobserwowany czas tur
  agenta. Równoległe tury mogą się nakładać. Nie jest to dokładny czas inferencji
  ani czas pracy człowieka. Nie zapisujemy treści rozmów lub kodu.

Dashboard scala nakładające się pomiary IDE i aktywnych okien, dając pierwszeństwo
danym IDE. Filtr projektu działa po scaleniu. Czasy procesów `running` i AI są
pokazywane osobno. Heartbeat IDE opisuje przedział do następnego heartbeat,
maksymalnie do ustawionego czasu bezczynności (domyślnie 120 s); ostatni wpis
może więc jeszcze narastać. To estymacja czasu z heartbeatów, nie pomiar każdej
sekundy edycji.

Średnia dzienna dotyczy aktywnych dni. Daty są rozliczane w `Europe/Warsaw`,
z uwzględnieniem dni 23- i 25-godzinnych przy zmianie czasu. Inne strefy można
ustawić w JSON; dla mniej typowych stref Windows może wymagać pakietu `tzdata`.

## WakaTime — podłączenie

W **Ustawienia → Wtyczki IDE** są dwa świadome działania:

- **Podłącz lokalną kopię:** dodaje lokalny odbiornik (`[api_urls]`), zachowując
  dotychczasowe wysyłanie do WakaTime. Wymaga współczesnego WakaTime CLI.
- **Główny zapis lokalnie:** zmienia główny endpoint i klucz na lokalne.
  Dodatkowe mirrory pozostają w pliku i mogą nadal wysyłać dane do innych usług.

Przed zmianą tworzona jest kopia `%USERPROFILE%\.wakatime.cfg` z datą w nazwie.
Po podłączeniu **uruchom ponownie IDE**. Własne ustawienia API w pluginie mogą
mieć pierwszeństwo przed tym plikiem. Aplikacja nie zmienia konfiguracji WakaTime
podczas zwykłego startu. Można przywrócić backup, aby cofnąć podłączenie.

Dokładny protokół i źródła: [INTEGRATIONS.md](INTEGRATIONS.md).
Zgodność endpointu zweryfikowano rzeczywistym, zainstalowanym WakaTime CLI na
oddzielnej konfiguracji, bazie i fikcyjnym heartbeat, bez wysyłania do chmury.

## Szybka edycja .exe

Dodaj wpis do `applications` w **config.json**:

```json
{
  "exe": "MojaAplikacja.exe",
  "label": "Moje testy",
  "category": "testing",
  "project": "Mój projekt",
  "mode": "running"
}
```

Podaj samą nazwę pliku, bez pełnej ścieżki. Wielkość liter nie ma znaczenia.
Zmiany są odczytywane automatycznie, bez restartu. Błędna edycja pliku nie
zastępuje ostatniej poprawnej konfiguracji. Zapis przez UI tworzy `config.json.bak`.
Zmiana **portu** wymaga ponownego uruchomienia aplikacji.

## Dane, import i eksport

- `data/activity.sqlite3` — lokalna baza zdarzeń, bez automatycznego kasowania.
- `data/local.json` — lokalne klucze API i ochrona operacji zapisu.
- `data/localflow.log` — diagnostyka aplikacji.
- `config.json` — edytowalna konfiguracja procesów, kolektorów AI i serwera.

Zatrzymaj aplikację przed kopiowaniem bazy do backupu; podczas pracy SQLite
używa także plików `-wal` i `-shm`. Katalog danych zawiera prywatne metadane
aktywności i lokalne klucze — nie jest częścią paczki źródeł ani repozytorium.

Import `.txt` zachowuje oryginalne **sumy tygodniowe** NostaleTestingTimer,
pokazane w sekcji archiwum. Nie są dodawane do dokładnych sum dziennych ani
przypisywane projektom. Powtórny import aktualizuje tydzień, nie dubluje sumy.
Wpisy testowe `Test - ` i komentarze są pomijane; inne błędne wiersze są zgłaszane.

Eksport CSV/JSON obejmuje wybrany zakres i projekt. CSV zawiera surowe pomiary
źródeł, które mogą się nakładać; JSON zawiera także archiwum tygodniowe.
Pierwszy skan AI importuje dostępne logi historyczne, kolejne czytają nowe bajty.

## Granice pomiaru

To lokalny odpowiednik głównych widoków dashboardu, a nie pełna implementacja
wszystkich funkcji WakaTime. Nie odtwarza historycznych danych chmurowych, których
nie ma na komputerze. Nie wylicza kosztów subskrypcji/API, liczby linii AI,
udziału AI w kodzie ani odsetka human review z samych tokenów. Czaty działające
wyłącznie w przeglądarce/chmurze i inne narzędzia AI potrzebują własnego adaptera.
Schematy lokalnych logów AI mogą wymagać aktualizacji parsera po zmianie klienta.

## Uruchomienie i sprawdzenie ze źródeł

Po sklonowaniu repozytorium utwórz własną konfigurację i zbuduj launcher.
Nie nadpisuj `config.json` podczas aktualizacji istniejącej instalacji.
Git pomija ten plik, lokalną bazę, klucze i artefakty kompilacji.

```powershell
git clone https://github.com/JG300716/LocalTimerAI.git
cd LocalTimerAI
Copy-Item config.example.json config.json
.\BuildLauncher.ps1
.\LocalFlow.exe
```

Pozostałe polecenia (Node.js jest potrzebny tylko do testów frontendu):

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

`--no-tracking` pozwala obejrzeć dashboard bez odczytu procesów lub logów AI;
endpoint heartbeat nadal jest dostępny. `--config` i `--data-dir` umożliwiają
oddzielne konfiguracje/testy. Weryfikacja obejmuje scalanie czasu, DST,
idempotencję importów, formaty metadanych AI, granice lokalnego HTTP oraz
uruchamianie aplikacji tray z katalogu ze spacjami. Rzeczywiste podłączenie
pluginu w konkretnym IDE należy sprawdzić po jego ponownym uruchomieniu.
Zakres testów i ich ograniczenia opisuje [TESTING.md](TESTING.md).

Przy aktualizacji zamknij aplikację przez Exit, zachowaj `config.json` i cały
katalog `data`, a następnie podmień pliki programu. Paczka nie migruje ani nie
usuwa historii użytkownika.
