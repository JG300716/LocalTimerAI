# Integracje LocalFlow

## IDE przez istniejące wtyczki WakaTime

LocalFlow przyjmuje heartbeat z wtyczek Rider/JetBrains, VS Code i innych edytorów
używających WakaTime CLI. Wtyczka nadal odpowiada za rozpoznawanie projektu,
języka, gałęzi i zdarzeń edycji. Sam obserwator procesów Windows dostarcza nazwę
aplikacji i czas aktywności; nie może zastąpić tych informacji.

Bazowy adres do konfiguracji: `http://127.0.0.1:48763/api/v1` (dopasuj port do
uruchomionej aplikacji). Nie dopisuj ścieżki `heartbeats.bulk`.

Tryb **mirror** zachowuje obecne ustawienia WakaTime i dodaje do
`%USERPROFILE%\.wakatime.cfg`:

```ini
[api_urls]
.* = http://127.0.0.1:48763/api/v1|KLUCZ_Z_LOCALFLOW
```

Aktualny CLI obsługuje przekazywanie heartbeatów do kilku serwerów. W starszej
wersji CLI zaktualizuj wtyczkę/CLI lub wybierz tryb **local**, który ustawia:

```ini
[settings]
api_url = http://127.0.0.1:48763/api/v1
api_key = KLUCZ_Z_LOCALFLOW
```

W panelu tryb lokalny nosi nazwę **local-only**; odpowiada parametrowi
`mode="local"` funkcji `configure_wakatime(...)`.
Funkcja wykonuje zmianę dopiero po jej wywołaniu.
Przed zapisaniem tworzy obok konfiguracji kopię `.localflow-backup-DATA`.
Nie wywołuje się podczas startu trackera. Uruchom IDE ponownie po zmianie.
Ustawienia API nadpisane w samym edytorze albo argumentach CLI mogą mieć
pierwszeństwo przed plikiem konfiguracyjnym.

Obsługiwany protokół wysyłania to POST
`/api/v1/users/current/heartbeats` oraz
`/api/v1/users/current/heartbeats.bulk` (do 25 wpisów w partii).
Informacja o edytorze pochodzi z nagłówka `User-Agent`.

Źródła: [WakaTime CLI: konfiguracja i wiele adresów API](https://github.com/wakatime/wakatime-cli/blob/develop/USAGE.md),
[protokół heartbeat](https://wakatime.com/developers#heartbeats).

## Modele AI: lokalne metadane Codex i Claude Code

Kolektor wczytuje pliki `.jsonl` spod katalogów ustawionych w `config.json`:

```json
{
  "ai": {
    "enabled": true,
    "poll_seconds": 15,
    "codex_paths": ["~/.codex/sessions"],
    "claude_paths": ["~/.claude/projects"]
  }
}
```

Pierwsze uruchomienie importuje dostępne pliki historyczne. Następne skany
czytają dopisane bajty; pozycje i minimalny kontekst są zapisywane w bazie
LocalFlow. Powtórzony import zabezpieczają trwałe identyfikatory zdarzeń.
Można wskazać także konkretny plik JSONL, np. do importu kopii archiwalnej.

W bazie lądują wyłącznie: czas, nazwa projektu z katalogu, nazwa narzędzia,
model, liczby tokenów oraz rodzaj pomiaru czasu. Treści promptów, odpowiedzi,
kod, argumenty wywołań narzędzi i ich wyniki nie są kopiowane do bazy.
Kolektor musi parsować pliki transkryptów, ale nie wysyła ich przez sieć.

Codex: preferowane są `token_usage_record` z `response_id` i `usage`.
Starsze logi z `event_msg.token_count` korzystają z przyrostów licznika
`total_token_usage`; lustrzane zdarzenia nie są sumowane ponownie.
Źródłem czasu są dopasowane `task_started` i `task_complete`/przerwanie.

Claude Code: źródłem tokenów jest `assistant.message.usage`. Kolejne fragmenty
tej samej odpowiedzi dostarczają tylko przyrosty liczników. Czas jest dostępny,
jeśli log zawiera `system.turn_duration`. Brak takiego pomiaru oznacza brak
czasu, a nie zero sekund inferencji.

**Czas AI oznacza zaobserwowany czas przebiegu pracy agenta**, obejmujący także
narzędzia i ewentualne oczekiwanie wewnątrz przebiegu. Nie jest dokładnym czasem
obliczeń modelu. Nie jest liczony od początku do końca całej rozmowy i nie
powinien być dodawany do czasu człowieka jako kolejna godzina pracy.
Niedokończony przebieg pojawi się z czasem po zamknięciu przebiegu w logu.

`tokens_input` zawiera wszystkie tokeny wejściowe, `tokens_cached` jest ich
podzbiorem. Tokenów z cache nie dodaje się drugi raz do sumy input + output.
Dla Claude normalizacja uwzględnia cache read i cache creation, które w jego
surowych metadanych są osobnymi polami. Koszt pozostaje nieznany (`null`):
zużycie tokenów w subskrypcji nie jest rachunkiem za API.

Schematy plików lokalnych mogą zmienić się między wersjami narzędzi.
Parser rozpoznaje opisane warianty, ignoruje inne zdarzenia i raportuje błędy
odczytu bez ujawniania treści transkryptów. Nie obejmuje sesji działających
wyłącznie w chmurze, innych klientów AI bez lokalnych metadanych ani pełnej
telemetrii GitHub Copilot. Dla takiego źródła potrzebny jest jego adapter.

Źródła: [oficjalna konfiguracja Codex](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude Code: pomiar użycia i ograniczenia kosztów](https://code.claude.com/docs/en/costs).
Dokładne mapowanie parsera Codex zweryfikowano również na strukturze lokalnych
metadanych, bez kopiowania treści rozmów do materiałów projektu.
