# OpenAgent Trade

Исследовательско-торговая система для крипто-фьючерсов (Bybit). Архитектурное ТЗ:
`~/Документы/openagent_trade_architecture.md` (первично). Существующие наработки
(`~/Документы/байбит/`) — исходный материал, втягиваются в эту структуру по фазам ТЗ §36.

## Структура (ТЗ §29)

```
config/          settings.py (пути, секреты) + research.yaml / paper.yaml / live.yaml
data/            raw / normalized / validated / features
research/        hypotheses/, experiments/, backtest/, statistics/, gates/, critic/, reports/
strategies/      registry/, implementations/
trading/         risk/, execution/, paper/, live/, reconciliation/
exchanges/       base.py, bybit.py, binance.py
infrastructure/  scheduler/, events/, monitoring/, notifications/, watchdog/
memory/          experiments/, decisions/, failures/, lessons/
tests/           контрактные тесты
```

## Статус фаз

- [x] Phase 0 — карта репозитория, маппинг, пробелы (`ARCHITECTURE.md` в байбит/)
- [x] Phase 1 (частично) — конфиг, research-пакет, тесты
- [ ] Phase 2 — research pipeline (critic, эксперименты)
- [ ] Phase 3-10 — registry, memory, paper, risk, exchanges, monitoring, scheduler, live

## Быстрый старт

```bash
# Тесты
.venv/bin/python -m pytest

# Исследовательский пайплайн (discovery → freeze → validation → OOS → gates)
.venv/bin/python -m research.hypotheses.run
```

Результат: отчёт `research/reports/<experiment_id>.md` + запись эксперимента
`research/experiments/<experiment_id>.json` + запись в memory SQLite
(`memory/openagent.db`, ТЗ §22-24).

## Данные

- 4h-история: `data/raw/4h/` (скопирована из байбит/research/data_4h)
- 1m klines (~1.3 ГБ): лежат вне проекта, путь в `.env` → `DATA_1M_DIR`

## Конвенции

- Секреты — только `.env`, никогда в коде (ТЗ §26)
- Research-параметры — в `config/research.yaml`, код читает через `config/research.py`
- Новый эксперимент обязан писать JSON-запись (воспроизводимость, ТЗ §24)
- Тесты — обязательны для критических контуров (ТЗ §37)