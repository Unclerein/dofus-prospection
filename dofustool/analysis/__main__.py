"""Aperçu en console du classement des crafts et des tendances (le dashboard fera mieux).

Usage : python -m dofustool.analysis [--top 20] [--my-jobs]
"""
import argparse
import sys
import time

from .. import config, db
from .crafts import CraftCalculator, load_items, load_recipes, rank_crafts, resolve_jobs
from .prices import PriceBook
from .trends import INSUFFICIENT, compute_trends


def kamas(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--my-jobs", action="store_true", help="ne garder que les recettes de tes métiers (config.toml)")
    args = parser.parse_args()

    cfg = config.load()
    conn = db.connect()
    now = time.time()
    prices = PriceBook(conn, now, cfg.last_sale_max_age_hours)
    if prices.snapshot_ts is None:
        print("Aucun relevé de prix : lance une capture d'abord.")
        return 1
    job_levels, unknown = resolve_jobs(conn, cfg.jobs)
    if unknown:
        print(f"Métiers inconnus dans config.toml (ignorés) : {', '.join(unknown)}")
    if args.my_jobs and not job_levels:
        print("--my-jobs : aucun métier valide dans config.toml.")
        return 1
    items = load_items(conn)
    calculator = CraftCalculator(items, load_recipes(conn), prices, cfg.hdv_tax, job_levels or None, cfg.min_liquidity)
    results = rank_crafts(conn, calculator)
    if args.my_jobs:
        results = [r for r in results if r.own_job]
    computable = [r for r in results if r.recursive_margin is not None]
    print(
        f"Dernier relevé : il y a {(now - prices.snapshot_ts) / 3600:.1f} h. Recettes : {len(results)}, "
        f"dont {len(computable)} calculables. Taxe HDV : {cfg.hdv_tax:.0%}."
    )
    print(f"\n{'objet':<34} {'métier':<11} {'niv':>3} {'vente':>10} {'coût':>10} {'marge':>10} {'%':>6}  remarques")
    for r in results[: args.top]:
        pct = "—" if r.margin_pct is None else f"{r.margin_pct:.0%}"
        notes = list(r.flags)
        if r.own_job:
            notes.insert(0, "ton métier")
        if r.crafted_ingredients:
            notes.append(f"{len(r.crafted_ingredients)} sous-craft(s)")
        print(
            f"{r.item.name[:34]:<34} {r.job[:11]:<11} {r.recipe.level:>3} {kamas(r.sell.price if r.sell else None):>10} "
            f"{kamas(r.recursive_cost):>10} {kamas(r.recursive_margin):>10} {pct:>6}  {', '.join(notes)}"
        )

    trends = compute_trends(conn, now, cfg.min_snapshots_for_trend, cfg.last_sale_max_age_hours, prices)
    insufficient = sum(1 for t in trends.values() if t.basis == INSUFFICIENT)
    signals = [(t, t.signal(cfg.trend_threshold)) for t in trends.values()]
    signals = sorted(((t, s) for t, s in signals if s), key=lambda x: x[0].deviation)
    print(f"\nTendances : {len(trends)} items, {insufficient} en « {INSUFFICIENT} », {len(signals)} signal(aux).")
    for t, s in signals[: args.top]:
        print(f"  {items[t.item_id].name[:40]:<40} {kamas(t.current):>10}  {t.deviation:+.0%}  {s}  ({t.basis})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
