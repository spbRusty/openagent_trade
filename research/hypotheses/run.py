"""Главный исследовательский пайплайн (ТЗ §6, §8, §11, §23).

Discovery sweep → freeze → validation → OOS → полный шлюз.
Пишет: experiment record (research/experiments/*.json) + отчёт (research/reports/*.md).

Запуск: .venv/bin/python -m research.hypotheses.run
"""
import polars as pl
from datetime import datetime
from pathlib import Path

from config.research import (PERIOD_DISCOVERY, PERIOD_VALIDATION, PERIOD_OOS,
                             BASE_COST_ROUND_TRIP, MAKER_COST_ROUND_TRIP, CANDIDATE_MIN_T)
from config.settings import LOGS_DIR, REPORTS_DIR
from research.backtest import prepare_trades
from research.statistics import full_metrics, block_bootstrap_pvalue
from research.gates import gate_all
from research.hypotheses.registry import (HYPOTHESES, W_GRID, H_GRID, MIN_TRADES_DISCOVERY,
                                          load_daily_full, add_features)
from research.experiments import make_experiment_id, record_experiment
from research.critic import review as critic_review
from infrastructure.notifications import notify
from memory import db

_PROGRESS_LOG = LOGS_DIR / "research_progress.log"


def _progress(msg: str) -> None:
    """Промежуточный отчёт: stdout + журнал logs/research_progress.log."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(_PROGRESS_LOG, "a") as f:
        f.write(line + "\n")


def _monthly_stats(tr: pl.DataFrame) -> dict:
    monthly = (tr.with_columns(pl.col("entry_time").dt.strftime("%Y-%m").alias("m"))
               .group_by("m").agg(pl.col("net_ret").sum().alias("pnl")))
    mpnl = monthly["pnl"].to_numpy()
    m = full_metrics(mpnl)
    return {"t": m["t_stat"], "ev": m["mean"], "n_months": m["n"], "pos_months": int((mpnl > 0).sum())}


def _monthly_pnl(tr: pl.DataFrame) -> pl.DataFrame:
    return (tr.with_columns(pl.col("entry_time").dt.strftime("%Y-%m").alias("m"))
            .group_by("m").agg(pl.col("net_ret").sum().alias("pnl")))


def _diagnose(df, W, H, strat, cond, name) -> dict:
    """Полная картина для сильнейшего (не прошедшего) кандидата."""
    tr = prepare_trades(df, W, H, strat, bar_minutes=1440, cond=cond)
    m = _monthly_stats(tr)
    mpnl = _monthly_pnl(tr)["pnl"].to_numpy()
    p, obs, ci = block_bootstrap_pvalue(mpnl, block=3, n_iter=5000)
    diag = {
        "name": name, "W": W, "H": H,
        "overlap_t": m["t"], "overlap_ev": m["ev"],
        "n_months": m["n_months"], "pos_months": m["pos_months"],
        "bootstrap_p": p, "bootstrap_obs": obs,
        "costs": {},
    }
    for label, cost in [("taker_0.15%", BASE_COST_ROUND_TRIP), ("maker_0.08%", MAKER_COST_ROUND_TRIP)]:
        net = tr["gross_ret"].to_numpy() - cost
        s = full_metrics(net)
        diag["costs"][label] = {"n": s["n"], "ev": s["mean"], "t": s["t_stat"], "winrate": s["winrate"]}
    gate = gate_all(df, W, H, strat, bar_minutes=1440,
                    oos_start=datetime.fromisoformat(PERIOD_OOS[0]), cond=cond)
    diag["gate_passed"] = gate.passed
    diag["gate_fail_reason"] = gate.fail_reason
    diag["gate_results"] = [(n, ok, d) for n, ok, d in gate.results]
    return diag


def _sync_hypotheses_memory(rows: list[dict], experiment_id: str) -> None:
    """Записать в memory статус каждой проверенной гипотезы и причину (ТЗ §22):
    AI должен знать, какие гипотезы проверялись и почему отклонены."""
    best_t = {}
    for r in rows:
        if r["id"] not in best_t or r["t"] > best_t[r["id"]]:
            best_t[r["id"]] = r["t"]
    with db.connect() as conn:
        for h in HYPOTHESES:
            db.upsert_hypothesis(conn, h.to_record())
            t = best_t.get(h.id)
            if t is not None:
                status = "FAILED" if t < CANDIDATE_MIN_T else "CANDIDATE"
                db.set_hypothesis_verdict(conn, h.id, status,
                                          f"max overlap t={t:.2f}, порог {CANDIDATE_MIN_T}",
                                          experiment_id)


def _hypothesis_history(hypothesis_id: str) -> list[dict]:
    """Последние прогоны этой гипотезы из memory (ТЗ §22): контекст для Critic —
    какие результаты уже были, чтобы отличить новое исследование от перебора."""
    with db.connect() as conn:
        return db.experiments_for_hypothesis(conn, hypothesis_id)


def _critic_result(**kw) -> dict:
    """Базовый вход Critic: сетка свипа + история экспериментов гипотезы."""
    return {"n_trials": len(W_GRID) * len(H_GRID),
            "hypothesis_history": _hypothesis_history(kw.get("hypothesis_id", "")), **kw}


def main():
    df = add_features(load_daily_full())
    D = lambda s: datetime.fromisoformat(s)
    disc = df.filter((pl.col("open_time") >= D(PERIOD_DISCOVERY[0])) & (pl.col("open_time") < D(PERIOD_DISCOVERY[1])))
    val = df.filter((pl.col("open_time") >= D(PERIOD_VALIDATION[0])) & (pl.col("open_time") < D(PERIOD_VALIDATION[1])))
    oos = df.filter((pl.col("open_time") >= D(PERIOD_OOS[0])) & (pl.col("open_time") < D(PERIOD_OOS[1])))

    params = {"periods": {"discovery": PERIOD_DISCOVERY, "validation": PERIOD_VALIDATION, "oos": PERIOD_OOS},
              "W_grid": W_GRID, "H_grid": H_GRID, "min_t": CANDIDATE_MIN_T}
    experiment_id = make_experiment_id("hypotheses", params)
    _progress(f"START {experiment_id} | гипотез={len(HYPOTHESES)} W×H={len(W_GRID)}×{len(H_GRID)} "
              f"| discovery={disc.height:,} val={val.height:,} oos={oos.height:,} баров")
    notify("RESEARCH", "PIPELINE_STARTED",
           {"experiment_id": experiment_id, "hypotheses": len(HYPOTHESES)})
    print(f"Эксперимент: {experiment_id}")
    print(f"Фичи: {[c for c in ['rvol_5','rvol_21','atr_rel','realized_vol','vol_high','vol_low','rel_vol'] if c in df.columns]}")
    print(f"Discovery: {disc.height:,} баров | Validation: {val.height:,} | OOS: {oos.height:,}\n")

    # --- 1. Discovery sweep: каждая гипотеза × (W, H) ---
    print("=== DISCOVERY SWEEP (2020-2024), гипотезы × W × H ===")
    rows = []
    for h in HYPOTHESES:
        h_rows = []
        for W in W_GRID:
            for H in H_GRID:
                tr = prepare_trades(disc, W, H, h.strategy, bar_minutes=1440, cond=h.cond)
                if tr.height < MIN_TRADES_DISCOVERY:
                    continue
                m = _monthly_stats(tr)
                h_rows.append({"id": h.id, "name": h.name, "strategy": h.strategy, "cond": h.cond,
                               "W": W, "H": H, "n": tr.height, **m})
        rows.extend(h_rows)
        if h_rows:
            bt = max(r["t"] for r in h_rows)
            _progress(f"HYPOTHESIS {h.id} ({h.name}): cells={len(h_rows)}, max t={bt:.2f}")
            notify("RESEARCH", "HYPOTHESIS_DONE",
                   {"hypothesis": h.id, "cells": len(h_rows), "max_t": round(bt, 2)})
        else:
            _progress(f"HYPOTHESIS {h.id} ({h.name}): недостаточно трейдов ни в одной ячейке")

    # --- 2. Freeze: лучший (гипотеза, W, H) по overlap-adjusted t на discovery ---
    viable = [r for r in rows if r["t"] >= CANDIDATE_MIN_T and r["ev"] > 0]
    if not viable:
        _progress(f"SWEEP: cells={len(rows)}, viable={len(viable)} → НЕТ КАНДИДАТА (порог t>={CANDIDATE_MIN_T})")
        print("\nНе обнаружено кандидата (overlap-adjusted t >= 2.0 на discovery). Вердикт: нет edge.")
        best = max(rows, key=lambda r: r["t"])
        print(f"Сильнейший: {best['name']} W={best['W']} H={best['H']} t={best['t']:.2f} — диагностика.")
        _progress(f"DIAGNOSTICS: bootstrap 5000 iter + полный шлюз для {best['name']} "
                  f"(самая тяжёлая часть, ~минуты)")
        diag = _diagnose(df, best["W"], best["H"], best["strategy"], best["cond"], best["name"])
        critic = critic_review(_critic_result(hypothesis_id=best["id"], **diag,
                                              n=diag["costs"]["taker_0.15%"]["n"]))
        record_experiment(experiment_id, {
            "pipeline": "hypotheses", "params": params,
            "result": "NO_CANDIDATE", "hypothesis_id": best["id"],
            "best": {k: v for k, v in best.items() if k != "cond"},
            "diagnostic": diag,
            "critic": critic.to_dict(),
        })
        _sync_hypotheses_memory(rows, experiment_id)
        _write_report(experiment_id, params, best, None, diag, passed=False, critic=critic)
        return

    viable.sort(key=lambda r: -r["t"])
    best = viable[0]
    _progress(f"SWEEP: cells={len(rows)}, viable={len(viable)} → ФИНАЛИСТ {best['name']} "
              f"W={best['W']} H={best['H']} t={best['t']:.2f}")
    notify("RESEARCH", "FINALIST", {"hypothesis": best["id"], "W": best["W"], "H": best["H"],
                                    "t": round(best["t"], 2)})
    print(f"\n=== ФИНАЛИСТ (зафиксирован по discovery): {best['name']} W={best['W']} H={best['H']} ===")

    # --- 3. Validation и OOS БЕЗ возврата к параметрам ---
    tr_val = prepare_trades(val, best["W"], best["H"], best["strategy"], bar_minutes=1440, cond=best["cond"])
    mv = full_metrics(tr_val["net_ret"].to_numpy())
    tr_oos = prepare_trades(oos, best["W"], best["H"], best["strategy"], bar_minutes=1440, cond=best["cond"])
    mo = full_metrics(tr_oos["net_ret"].to_numpy())

    # --- 4. Полный шлюз на всех данных ---
    gate = gate_all(df, best["W"], best["H"], best["strategy"], bar_minutes=1440,
                    oos_start=datetime.fromisoformat(PERIOD_OOS[0]), cond=best["cond"])
    gate.print()
    passed = gate.passed
    _progress(f"GATES: {'PASSED' if passed else 'FAILED — ' + gate.fail_reason}")
    notify("RESEARCH", "GATES_DONE", {"passed": passed,
                                      "fail_reason": gate.fail_reason or ""})

    oos_taker_ev = (tr_oos["gross_ret"].to_numpy() - BASE_COST_ROUND_TRIP).mean()
    critic = critic_review(_critic_result(
        hypothesis_id=best["id"],
        overlap_t=best["t"], overlap_ev=best["ev"],
        n_months=best["n_months"], pos_months=best["pos_months"],
        bootstrap_p=None, n=mv["n"], oos_t=mo["t_stat"],
        costs={"taker_0.15%": {"ev": oos_taker_ev}},
        gate_passed=passed, gate_fail_reason=gate.fail_reason))

    record_experiment(experiment_id, {
        "pipeline": "hypotheses", "params": params,
        "result": "CANDIDATE_PASSED" if passed else "CANDIDATE_REJECTED",
        "hypothesis_id": best["id"],
        "finalist": {k: v for k, v in best.items() if k != "cond"},
        "validation": {"n": mv["n"], "ev": mv["mean"], "t": mv["t_stat"], "winrate": mv["winrate"]},
        "oos": {"n": mo["n"], "ev": mo["mean"], "t": mo["t_stat"], "winrate": mo["winrate"]},
        "gates": [(n, ok, d) for n, ok, d in gate.results],
        "gate_passed": passed, "gate_fail_reason": gate.fail_reason,
        "critic": critic.to_dict(),
    })
    _sync_hypotheses_memory(rows, experiment_id)
    _write_report(experiment_id, params, best, {"val": mv, "oos": mo, "gate": gate}, None,
                  passed=passed, critic=critic)


def _write_report(experiment_id, params, best, results, diag, passed: bool, critic):
    """Отчёт по ТЗ §23: experiment_id, hypothesis, data, method, parameters, results,
    costs, gates, critic, verdict, next_action."""
    lines = [
        f"# Отчёт {experiment_id}",
        f"Дата: {datetime.now():%Y-%m-%d %H:%M}",
        f"Пайплайн: hypotheses (сигнал × условие), data: data/raw/4h",
        "",
        "## Гипотеза",
        f"Финалист: **{best['name']}** (id={best['id']}), W={best['W']}, H={best['H']}, "
        f"discovery overlap-t={best['t']:.2f}, EV_мес={best['ev']:+.5f}, месяцев={best['n_months']}, плюс={best['pos_months']}",
        "",
        "## Метод",
        "Сигнал на close t, вход open t+1, выход open t+1+H. Discovery sweep → freeze → "
        "validation (2025) → OOS (2026) → полный шлюз. Параметры зафиксированы до просмотра OOS.",
        "",
        "## Издержки",
        f"taker раунд-трип {BASE_COST_ROUND_TRIP:.4%}, maker {MAKER_COST_ROUND_TRIP:.4%}, "
        f"сетка стресса {params['periods'] and ''}0.10-0.30%.",
    ]
    if results:
        val, oos, gate = results["val"], results["oos"], results["gate"]
        lines += [
            "",
            "## Результаты",
            f"Validation (2025): n={val['n']} EV={val['mean']:+.5f} t={val['t_stat']:.2f} win={val['winrate']:.1%}",
            f"OOS (2026): n={oos['n']} EV={oos['mean']:+.5f} t={oos['t_stat']:.2f} win={oos['winrate']:.1%}",
            "",
            "## Gates",
        ]
        lines += [f"- [{('PASS' if ok else 'FAIL')}] {n}: {d}" for n, ok, d in gate.results]
        lines += [f"ИТОГ: {'ПРОШЁЛ' if passed else 'ОТКЛОНЁН — ' + gate.fail_reason}", ""]
    if diag:
        lines += [
            "",
            "## Диагностика (не кандидат)",
            f"overlap-adjusted: t={diag['overlap_t']:.2f} EV={diag['overlap_ev']:+.5f} "
            f"месяцев={diag['n_months']} плюс={diag['pos_months']}",
            f"block bootstrap: p={diag['bootstrap_p']:.3f}",
            f"costs: {diag['costs']}",
            f"gate: {'ПРОШЁЛ' if diag['gate_passed'] else 'ОТКЛОНЁН — ' + diag['gate_fail_reason']}",
            "",
        ]
    lines += [
        "## Critic",
        f"Вердикт: **{critic.verdict}** (confidence={critic.confidence:.2f})",
        f"Риски: {critic.identified_risks or 'не выявлено'}",
        f"Альтернативные объяснения: {critic.alternative_explanations or '—'}",
        f"Требуемые тесты: {critic.required_tests or '—'}",
        f"Рекомендация: {critic.recommendation}",
        "",
        "## Вердикт",
        "**CANDIDATE_PASSED** — подготовить к paper trading." if passed else
        "**НЕТ EDGE** — кандидат не обнаружен, гипотеза отклонена с причиной.",
        "",
        "## Next action",
        "Paper trading / следующий цикл гипотез." if passed else
        "Новая категория данных (funding rates) или новая гипотеза.",
        "",
    ]
    out = REPORTS_DIR / f"{experiment_id}.md"
    out.write_text("\n".join(lines))
    notify("RESEARCH", "EXPERIMENT_DONE", {
        "experiment_id": experiment_id,
        "result": "CANDIDATE_PASSED" if passed else ("CANDIDATE_REJECTED" if results else "NO_CANDIDATE"),
        "hypothesis": best["name"],
        "report": out.name,
    })
    print(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()