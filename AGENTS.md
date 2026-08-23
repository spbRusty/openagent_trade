# AGENTS.md

Инструкция для AI-агентов, работающих в этом проекте.

## Первичный документ

`~/Документы/openagent_trade_architecture.md` — архитектурное ТЗ. Оно ПЕРВИЧНО.
Существующие наработки в `~/Документы/байбит/` — вторичны (исходный материал).

## Структура проекта

- `config/` — settings.py (пути, секреты из .env), research.yaml/paper.yaml/live.yaml
- `research/` — исследовательский пайплайн (самое зрелое):
  - `backtest/` — per-trade (`__init__.py`), cross-sectional (`xs.py`), portfolio, load_data
  - `statistics/` — full_metrics, block_bootstrap_pvalue
  - `gates/` — gate_all (формальные проверки, НЕ зависят от LLM)
  - `hypotheses/` — registry.py (реестр гипотез), run.py (пайплайн)
  - `experiments/` — JSON-записи экспериментов (ТЗ §24)
  - `critic/` — детерминированный Critic (ТЗ §4.4/§13): вердикт PROCEED/NEED_TESTS/REJECT,
    риски (multiple testing, regime dependence, hidden costs, bootstrap), вызывается в run.py
- `trading/` — исполнение, риск, сверка (§15-17):
  - `execution/` — единый интерфейс (Paper/Live) + `paper.py` (PaperExecutor: fills, slippage, fees, позиции, PnL, персистентность в JSON)
  - `risk/` — RiskManager: max position/exposure, open positions, daily loss, drawdown, kill switch
  - `reconciliation/` — reconcile(internal, exchange) → список расхождений (ТЗ §17)
  - `live/` — скелет
- `memory/` — SQLite: hypotheses (статусы/вердикты), experiments, strategies (заполнено §22)
- `data/` — конвейер ТЗ §7: `normalize.py` (raw 1m → normalized/1m, валидация,
  манифест data/metadata/1m_manifest.json с checksum/rows/gaps; порча данных
  отклоняет символ, пропуски фиксируются как warnings — не заполняются)
- `exchanges/bybit.py` — Bybit V5 adapter (ТЗ §18): контракт исполнения + публичные
  klines/instruments; HMAC-подпись, retry транзиентных ошибок, ключи из .env
- `infrastructure/notifications/` — единый NotificationService (ТЗ §20): Telegram + ntfy,
  категории INFO/WARNING/CRITICAL/TRADE/RESEARCH, stdlib urllib, пайплайн шлёт RESEARCH-уведомления
- `infrastructure/scheduler/` — jobs ТЗ §21 (data_update/daily_research/paper_monitor/health_check),
  запуск через cron (scripts/crontab.txt)
- `infrastructure/realtime/` — realtime-монитор (paper-only исполнительный контур):
  `ws.py` (Bybit WS: стаканы depth50 топ-50 по turnover, reconnect), `beacons.py`
  (маячки: imbalance/wall/spread_expansion, cooldown), `monitor.py` (главный цикл:
  журнал logs/realtime/*.jsonl + ntfy + opencode-хук с snapshot, SIGHUP-перезагрузка
  config/realtime.yaml). Демон: systemd-сервис `realtime_monitor` (deploy/), Restart=always
- `strategies/registry/` — lifecycle §14; переход в LIVE требует approval (ТЗ §27)
- `strategies/implementations/` — пусто: код стратегии не пишется до доказанного edge (ТЗ §32)

## Конвенции

1. **Секреты** — только `.env`, никогда в коде и логах (ТЗ §26). В prompts не передавать.
2. **Конфиг** — research-параметры в `config/research.yaml`; код импортирует константы
   из `config/research.py` (совместимый слой). Не хардкодить параметры в research-коде.
3. **Gates детерминированы** — никакого LLM в математической валидации.
4. **Критический контур**: backtest no-lookahead (сигнал close t → вход open t+1).
   Любое изменение `research/backtest/` требует обновления контрактных тестов.
5. **Эксперименты воспроизводимы**: каждый прогон пишет JSON в `research/experiments/`.
6. **Не подгонять параметры по OOS** (ТЗ §11): свип только на discovery, freeze до validation/OOS.
7. **Не включать live автоматически** — только после human approval (ТЗ §27).
8. **Ponytail** — минимальные изменения, без лишних абстракций и зависимостей.

## Запуск

```bash
.venv/bin/python -m pytest              # тесты
.venv/bin/python -m research.hypotheses.run   # исследовательский пайплайн
```

## Правила для изменений

- Сначала план → implementation → test → review.
- Не ломать работающие части без необходимости (ТЗ §38.3).
- Не удалять существующие исследования и результаты.