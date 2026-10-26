[Polski](TESTING.md) | [English](TESTING.en.md)

# Testowanie LocalFlow

Testy należy uruchamiać z katalogu projektu. Aplikacja używa biblioteki standardowej
Pythona; testy frontendu wymagają Node.js z wbudowanym `node:test` (Node.js 18 lub
nowszy). Testy natywnego launchera wymagają Windows, .NET Framework i jego
kompilatora C#. Test istniejącego pliku wykonywalnego wymaga zbudowanego launchera.

## Polecenia przed wydaniem

```powershell
node --check web/app.js
node --test web/test_app.cjs
python -m unittest discover -v
```

Na Windows zbuduj launcher przed uruchomieniem testów jego pliku wykonywalnego:

```powershell
.\BuildLauncher.ps1
python -m unittest test_launcher -v
```

Przed testami paczki zbuduj archiwum i sprawdź jego zawartość po rozpakowaniu:

```powershell
.\scripts\Build-Release.ps1
python -m unittest test_release -v
```

Workflow `.github/workflows/ci.yml` buduje launcher i paczkę na Windows,
uruchamia cały zestaw Python/Node i udostępnia sprawdzony ZIP jako artefakt.
Nie zmienia konfiguracji prawdziwego IDE ani autostartu użytkownika.

Opcjonalny test rzeczywistego WakaTime CLI wymaga zainstalowanego klienta. Można
wskazać jego plik parametrem `--cli`:

```powershell
python verify_wakatime_cli.py
python verify_wakatime_cli.py --mirror
```

Przy ocenie wyniku sprawdź także pozycje `skipped`. Brak Windows, kompilatora
lub pliku launchera może spowodować pominięcie testów natywnych; pominięcie nie
potwierdza poprawności danej funkcji. Node nie jest wymagany do używania aplikacji.

## Zakres automatycznej weryfikacji

### Frontend — `web/test_app.cjs`

Testy wykonują produkcyjny `app.js` w izolowanym środowisku JavaScript z atrapami
DOM i odpowiedzi HTTP. Dane testowe znajdują się wyłącznie w pliku testów;
dashboard nie generuje przykładowej aktywności.

- Sześć widoków: przegląd, projekty, aplikacje, AI, oś czasu i ustawienia.
  Renderowanie sprawdzane jest dla czterech motywów: Midnight, Paper, Terminal
  i Focus, również przy pustej odpowiedzi API.
- Stare linki `mode=demo` oraz zapamiętane ustawienie demonstracyjne nie
  przywracają fikcyjnych danych. Pozostałe parametry adresu i wybrany widok
  zostają zachowane.
- Nawigacja po kliknięciu projektu i przy opóźnionym pobieraniu ustawień;
  filtrowanie projektu; zakres dat obejmujący zmianę czasu.
- Odświeżanie automatyczne i ręczne, zachowanie aktywnego pola wyszukiwania,
  utrata połączenia oraz odrzucenie starych wyników po zmianie filtra.
- Błędny JSON nie zastępuje poprawnej konfiguracji; szkic edycji pozostaje
  dostępny. Zmiana portu sygnalizuje potrzebę restartu.
- Polecenia integracji WakaTime i pauzy wysyłają żądania do lokalnego API;
  import przekazuje tekst pliku, a eksport CSV/JSON pobiera odpowiedź API.
  Błędy eksportu nie tworzą zastępczego pliku.
- Ekranowanie metadanych użytkownika oraz zachowanie pełnej sumy w wykresach
  po zgrupowaniu mniej aktywnych projektów.

Testy te nie uruchamiają silnika przeglądarki. Potwierdzają logikę i wygenerowany
HTML, a nie rzeczywisty układ CSS, położenie elementów, obsługę klawiatury przez
przeglądarkę ani wygląd wykresów na wszystkich rozdzielczościach. Style wymagają
osobnego przeglądu wizualnego; kontrola źródła CSS nie zastępuje takiego przeglądu.

### Backend i HTTP — `test_backend.py`

- Scalanie nakładających się pomiarów z pierwszeństwem IDE, filtrowanie po
  scaleniu, obcinanie przedziałów do zakresu i rozdzielenie AI oraz procesów w tle.
- Granice dni, północ, zmiana czasu oraz zgodność zapasowej obsługi strefy czasowej.
- Atomowy zapis konfiguracji, backup, odzyskanie po błędnej edycji i brak
  mutowania aktywnej konfiguracji przez zwracany obiekt.
- Idempotencja zdarzeń, łączenie próbek procesów i ograniczenie czasu heartbeatów.
- Rzeczywiste żądania do odizolowanego serwera loopback: Host, Origin, token
  zapisu, heartbeat pojedynczy i zbiorczy, błędny JSON oraz eksport CSV.
- Zatrzymanie i ponowne uruchomienie serwera z zachowaniem konfiguracji i danych.

### Kolektory — `test_tracker.py`, `test_integrations.py`

- Rozdzielenie aktywnego okna i obecności procesu, bezczynność, blokada,
  uśpienie, zmiana zegara, opcjonalne tytuły okien i zmiana konfiguracji.
- Pauza odrzuca próbkę pobraną w trakcie zatrzymania; wznowienie nie zalicza
  przerwy do czasu pracy.
- Import starszego timera zachowuje dokładność tygodniową i nie dubluje wpisów.
- Rozpoznawanie edytora z heartbeatów oraz lokalnych formatów metadanych AI:
  częściowe wiersze, wznowienie odczytu, liczniki narastające, deduplikacja,
  cache, błędne daty i przerwanie importu.
- Zmiana konfiguracji WakaTime zachowuje jej podstawowe ustawienia, istniejące
  reguły i kopię zapasową. Testy używają plików tymczasowych.

### Launcher Windows — `test_launcher.py`

Testy korzystają z oddzielnego katalogu i serwisów testowych. Obejmują wykrywanie
otwartego i zamkniętego portu, ponowne zatrzymanie nieaktywnej usługi, Start/Stop,
zamknięcie pętli aplikacji i backendu, zamknięcie podczas startu oraz blokadę
drugiej instancji w tym samym katalogu. Część przypadków korzysta z rzeczywistego
backendu, a część z jego atrapą.

To weryfikacja kodu natywnego i cyklu życia, a nie potwierdzenie ręcznego
kliknięcia każdego przycisku, działania autostartu, zasobnika systemowego,
skryptu użytkownika lub ikon na pasku zadań w każdej konfiguracji Windows.

### Protokół WakaTime — `verify_wakatime_cli.py`

Skrypt uruchamia rzeczywisty klient CLI z tymczasową konfiguracją, odrębną bazą
i pojedynczym kontrolowanym heartbeat. W wariancie `--mirror` także główny
odbiornik jest lokalnym serwerem testowym. Skrypt nie korzysta z rzeczywistej
kolejki offline, nie zmienia konfiguracji użytkownika i nie wysyła heartbeatów
do chmury. Sprawdza zapis i rozpoznanie edytora po stronie LocalFlow.

### Paczka Windows — `test_release.py`

- Dokładna lista plików ZIP, poprawność CRC i suma SHA256.
- Konfiguracja pochodzi z `config.example.json`; brak prywatnej historii,
  lokalnych kluczy, logów, kopii zapasowych i danych demonstracyjnych.
- Uruchomienie rozpakowanego serwera z oddzielnym portem i bazą; odpowiedzi
  HTML, JS, CSS i API, autoryzowane zamknięcie oraz zakończenie procesu.
- Wewnętrzny test EXE wyciągniętego z archiwum. Paczka wymaga osobnego Pythona.

## Granice i sprawdzenie rzeczywistego środowiska

Pomyślny test CLI nie potwierdza konfiguracji konkretnej wtyczki Ridera, VS Code
ani innego IDE. Po świadomym podłączeniu i restarcie IDE należy potwierdzić nowy
heartbeat w lokalnym dashboardzie. Ustawienia wtyczki mogą mieć pierwszeństwo
przed plikiem `.wakatime.cfg`.

Opcja **Główny zapis lokalnie** zmienia główny endpoint i klucz WakaTime.
Pozostawia dodatkowych odbiorców z `[api_urls]`, którzy mogą nadal otrzymywać
dane. Nie jest to test ani gwarancja wyłączenia całego ruchu sieciowego wtyczki.

Testy kolektora Windows używają kontrolowanych próbek. Nie zastępują próby
rzeczywistego aktywnego okna, bezczynności, blokady i uśpienia systemu. Testy
parserów AI pokrywają obsługiwane przykłady logów; nie potwierdzają zgodności
ze wszystkimi przyszłymi wersjami klientów ani dokładnego czasu inferencji.

Przed publikacją paczki sprawdź osobno uruchomienie z rozpakowanego katalogu
oraz brak prywatnej bazy, lokalnych kluczy, logów i konfiguracji użytkownika.
Wyników testów na atrapach nie należy opisywać jako pełnego testu rzeczywistego
środowiska użytkownika.
