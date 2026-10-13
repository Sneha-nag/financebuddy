"""Generate synthetic personal-finance ledgers with planted ground truth."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from .merchants import MERCHANTS


CITIES = [
    ("New York", "NY"),
    ("Austin", "TX"),
    ("Seattle", "WA"),
    ("Chicago", "IL"),
    ("Denver", "CO"),
    ("Boston", "MA"),
    ("Atlanta", "GA"),
    ("San Jose", "CA"),
]


@dataclass
class PlantedEvent:
    user_id: str
    event_type: str
    description: str
    related_merchant_ids: list[str]
    expected_signal: str
    metadata: dict[str, Any]


def _merchant_lookup() -> dict[str, dict]:
    return {m["merchant_id"]: m for m in MERCHANTS}


def _pick_descriptor(rng: np.random.Generator, merchant: dict) -> str:
    return str(rng.choice(merchant["descriptors"]))


def _make_users(n_users: int, rng: np.random.Generator) -> pl.DataFrame:
    rows = []
    for i in range(n_users):
        city, state = CITIES[i % len(CITIES)]
        income = float(rng.normal(4200, 900))
        income = max(2800.0, min(9000.0, income))
        persona = str(rng.choice(["saver", "spender", "balanced"], p=[0.25, 0.35, 0.40]))
        rows.append(
            {
                "user_id": f"u_{i:04d}",
                "display_name": f"User {i:04d}",
                "city": city,
                "state": state,
                "persona": persona,
                "monthly_income": round(income, 2),
                "created_at": "2023-12-01",
            }
        )
    return pl.DataFrame(rows)


def _add_txn(
    txns: list[dict],
    *,
    txn_id: str,
    user_id: str,
    ts: datetime,
    merchant: dict,
    amount: float,
    txn_type: str,
    channel: str,
    city: str,
    state: str,
    is_fraud: int = 0,
    is_duplicate_charge: int = 0,
    planted_tag: str | None = None,
    rng: np.random.Generator,
) -> None:
    signed = abs(amount) if txn_type == "credit" else -abs(amount)
    txns.append(
        {
            "transaction_id": txn_id,
            "user_id": user_id,
            "transaction_ts": ts.isoformat(sep=" ", timespec="seconds"),
            "posted_date": ts.date().isoformat(),
            "merchant_id": merchant["merchant_id"],
            "merchant_name": merchant["name"],
            "merchant_descriptor": _pick_descriptor(rng, merchant),
            "category": merchant["category"],
            "amount": round(float(signed), 2),
            "currency": "USD",
            "txn_type": txn_type,
            "channel": channel,
            "city": city,
            "state": state,
            "status": "completed",
            "is_fraud_label": is_fraud,
            "is_duplicate_charge_label": is_duplicate_charge,
            "planted_tag": planted_tag,
        }
    )


def _generate_user_ledger(
    user: dict,
    start: date,
    months: int,
    rng: np.random.Generator,
    merchants: dict[str, dict],
    counter: list[int],
) -> tuple[list[dict], list[PlantedEvent]]:
    txns: list[dict] = []
    planted: list[PlantedEvent] = []
    end = start + timedelta(days=30 * months)

    def next_id() -> str:
        counter[0] += 1
        return f"t_{counter[0]:08d}"

    subs = [
        ("m_rent", 1, user["monthly_income"] * 0.32 + rng.normal(0, 20)),
        ("m_electric", 12, merchants["m_electric"]["typical_amount"] * rng.uniform(0.85, 1.25)),
        ("m_internet", 8, merchants["m_internet"]["typical_amount"]),
        ("m_netflix", 5, merchants["m_netflix"]["typical_amount"]),
        ("m_spotify", 7, merchants["m_spotify"]["typical_amount"]),
        ("m_gym", 3, merchants["m_gym"]["typical_amount"]),
    ]

    has_duplicate_streaming = rng.random() < 0.20
    if has_duplicate_streaming:
        subs.append(("m_apple_music", 9, merchants["m_apple_music"]["typical_amount"]))
        planted.append(
            PlantedEvent(
                user_id=user["user_id"],
                event_type="duplicate_subscription",
                description="User pays for both Spotify and Apple Music",
                related_merchant_ids=["m_spotify", "m_apple_music"],
                expected_signal="duplicate_music_subscription",
                metadata={"category": "Subscriptions"},
            )
        )

    has_cloud_overlap = rng.random() < 0.10
    if has_cloud_overlap:
        subs.append(("m_icloud", 14, merchants["m_icloud"]["typical_amount"]))
        subs.append(("m_dropbox", 16, merchants["m_dropbox"]["typical_amount"]))
        planted.append(
            PlantedEvent(
                user_id=user["user_id"],
                event_type="duplicate_subscription",
                description="User pays for both iCloud+ and Dropbox",
                related_merchant_ids=["m_icloud", "m_dropbox"],
                expected_signal="duplicate_cloud_storage",
                metadata={"category": "Subscriptions"},
            )
        )

    netflix_creep = rng.random() < 0.15
    if netflix_creep:
        planted.append(
            PlantedEvent(
                user_id=user["user_id"],
                event_type="price_creep",
                description="Netflix amount increases mid-period",
                related_merchant_ids=["m_netflix"],
                expected_signal="subscription_price_increase",
                metadata={"from": 15.49, "to": 22.99},
            )
        )

    day = start
    while day < end:
        if day.day in (1, 15):
            pay_ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=pay_ts,
                merchant=merchants["m_salary"],
                amount=user["monthly_income"] / 2,
                txn_type="credit",
                channel="ach",
                city=user["city"],
                state=user["state"],
                rng=rng,
            )
        day += timedelta(days=1)

    for merchant_id, day_of_month, base_amount in subs:
        month_cursor = date(start.year, start.month, 1)
        month_idx = 0
        while month_cursor < end:
            month_idx += 1
            try:
                post_day = date(month_cursor.year, month_cursor.month, min(day_of_month, 28))
            except ValueError:
                post_day = date(month_cursor.year, month_cursor.month, 28)
            if post_day < start or post_day >= end:
                month_cursor = (month_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                continue

            amount = base_amount
            tag = None
            if merchant_id == "m_netflix" and netflix_creep and month_idx >= max(6, months // 2):
                amount = 22.99
                tag = "price_creep"

            duplicate_this = rng.random() < 0.02
            ts = datetime.combine(post_day, datetime.min.time()) + timedelta(
                hours=int(rng.integers(6, 12)), minutes=int(rng.integers(0, 59))
            )
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=merchants[merchant_id],
                amount=amount,
                txn_type="debit",
                channel="card",
                city=user["city"],
                state=user["state"],
                planted_tag=tag,
                rng=rng,
            )
            if duplicate_this:
                ts2 = ts + timedelta(hours=int(rng.integers(1, 30)))
                _add_txn(
                    txns,
                    txn_id=next_id(),
                    user_id=user["user_id"],
                    ts=ts2,
                    merchant=merchants[merchant_id],
                    amount=amount,
                    txn_type="debit",
                    channel="card",
                    city=user["city"],
                    state=user["state"],
                    is_duplicate_charge=1,
                    planted_tag="duplicate_charge",
                    rng=rng,
                )
                planted.append(
                    PlantedEvent(
                        user_id=user["user_id"],
                        event_type="duplicate_charge",
                        description=f"Double charge at {merchants[merchant_id]['name']}",
                        related_merchant_ids=[merchant_id],
                        expected_signal="same_merchant_double_charge",
                        metadata={"amount": round(amount, 2), "date": post_day.isoformat()},
                    )
                )

            month_cursor = (month_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    dining_mult = {"saver": 0.6, "balanced": 1.0, "spender": 1.7}[user["persona"]]
    n_days = (end - start).days
    for d in range(n_days):
        day = start + timedelta(days=d)
        if day.weekday() in (0, 4) and rng.random() < 0.85:
            m = merchants[str(rng.choice(["m_wholefoods", "m_traderjoe", "m_costco"]))]
            amt = m["typical_amount"] * rng.uniform(0.6, 1.4)
            ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=int(rng.integers(10, 20)))
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=m,
                amount=amt,
                txn_type="debit",
                channel="card",
                city=user["city"],
                state=user["state"],
                rng=rng,
            )
        if rng.random() < 0.35 * dining_mult:
            m = merchants[str(rng.choice(["m_starbucks", "m_chipotle", "m_doordash", "m_uber_eats"]))]
            amt = m["typical_amount"] * rng.uniform(0.7, 1.5)
            if day.weekday() >= 5:
                amt *= 1.0 + 0.4 * dining_mult
            hour = int(rng.choice([8, 12, 13, 18, 19, 20, 21]))
            ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=int(rng.integers(0, 59)))
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=m,
                amount=amt,
                txn_type="debit",
                channel=str(rng.choice(["card", "mobile"])),
                city=user["city"],
                state=user["state"],
                planted_tag="weekend_dining_spike" if day.weekday() >= 5 and user["persona"] == "spender" else None,
                rng=rng,
            )
        if rng.random() < 0.18:
            m = merchants[str(rng.choice(["m_uber", "m_shell", "m_amazon", "m_target", "m_pharmacy", "m_transfer"]))]
            amt = m["typical_amount"] * rng.uniform(0.5, 1.6)
            ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=int(rng.integers(7, 22)))
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=m,
                amount=amt,
                txn_type="debit",
                channel="card",
                city=user["city"],
                state=user["state"],
                rng=rng,
            )

    if rng.random() < 0.25:
        travel_day = start + timedelta(days=int(rng.integers(60, max(90, n_days - 10))))
        for mid in ("m_airline", "m_hotel"):
            m = merchants[mid]
            ts = datetime.combine(travel_day, datetime.min.time()) + timedelta(hours=11)
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=m,
                amount=m["typical_amount"] * rng.uniform(0.9, 1.3),
                txn_type="debit",
                channel="card",
                city=user["city"],
                state=user["state"],
                planted_tag="seasonal_travel",
                rng=rng,
            )
        planted.append(
            PlantedEvent(
                user_id=user["user_id"],
                event_type="seasonal_travel",
                description="Airline + hotel spend in a short window",
                related_merchant_ids=["m_airline", "m_hotel"],
                expected_signal="travel_burst",
                metadata={"around": travel_day.isoformat()},
            )
        )

    if rng.random() < 0.08:
        fraud_day = start + timedelta(days=int(rng.integers(40, max(50, n_days - 5))))
        fraud_city, fraud_state = ("Miami", "FL") if user["state"] != "FL" else ("Las Vegas", "NV")
        m = merchants["m_unknown_fraud"]
        for i in range(int(rng.integers(4, 8))):
            ts = datetime.combine(fraud_day, datetime.min.time()) + timedelta(hours=2, minutes=5 * i)
            _add_txn(
                txns,
                txn_id=next_id(),
                user_id=user["user_id"],
                ts=ts,
                merchant=m,
                amount=float(rng.choice([0.99, 1.0, 1.5, 2.0])),
                txn_type="debit",
                channel="card",
                city=fraud_city,
                state=fraud_state,
                is_fraud=1,
                planted_tag="card_test_burst",
                rng=rng,
            )
        big_ts = datetime.combine(fraud_day, datetime.min.time()) + timedelta(hours=3, minutes=40)
        _add_txn(
            txns,
            txn_id=next_id(),
            user_id=user["user_id"],
            ts=big_ts,
            merchant=m,
            amount=float(rng.uniform(180, 650)),
            txn_type="debit",
            channel="card",
            city=fraud_city,
            state=fraud_state,
            is_fraud=1,
            planted_tag="fraud_large_new_merchant",
            rng=rng,
        )
        planted.append(
            PlantedEvent(
                user_id=user["user_id"],
                event_type="fraud_burst",
                description="Card-test micro-auths then large unknown merchant charge in new city",
                related_merchant_ids=["m_unknown_fraud"],
                expected_signal="fraud_velocity_new_geo",
                metadata={"city": fraud_city, "date": fraud_day.isoformat()},
            )
        )

    return txns, planted


def generate_all(settings: dict) -> dict[str, Path]:
    gen = settings["generator"]
    paths = settings["paths"]
    rng = np.random.default_rng(settings["seed"])
    merchants = _merchant_lookup()

    raw_dir = Path(paths["raw"])
    bronze_dir = Path(paths["bronze"])
    gt_dir = Path(paths["ground_truth"])
    for d in (raw_dir, bronze_dir, gt_dir, Path(paths["silver"]), Path(paths["gold"])):
        d.mkdir(parents=True, exist_ok=True)

    users = _make_users(gen["n_users"], rng)
    start = date.fromisoformat(gen["start_date"])
    counter = [0]
    all_txns: list[dict] = []
    all_planted: list[PlantedEvent] = []

    for user in users.iter_rows(named=True):
        txns, planted = _generate_user_ledger(
            user=user,
            start=start,
            months=int(gen["months"]),
            rng=rng,
            merchants=merchants,
            counter=counter,
        )
        all_txns.extend(txns)
        all_planted.extend(planted)

    transactions = pl.DataFrame(all_txns, infer_schema_length=None).sort(
        ["user_id", "transaction_ts"]
    )
    merchants_df = pl.DataFrame([{k: v for k, v in m.items() if k != "descriptors"} for m in MERCHANTS])

    planted_rows = []
    for p in all_planted:
        d = asdict(p)
        d["related_merchant_ids"] = json.dumps(d["related_merchant_ids"])
        d["metadata"] = json.dumps(d["metadata"])
        planted_rows.append(d)
    planted_df = pl.DataFrame(planted_rows) if planted_rows else pl.DataFrame(
        schema={
            "user_id": pl.Utf8,
            "event_type": pl.Utf8,
            "description": pl.Utf8,
            "related_merchant_ids": pl.Utf8,
            "expected_signal": pl.Utf8,
            "metadata": pl.Utf8,
        }
    )

    users_path = bronze_dir / "users.parquet"
    tx_path = bronze_dir / "transactions.parquet"
    merchants_path = bronze_dir / "merchants.parquet"
    planted_path = gt_dir / "planted_events.parquet"

    users.write_parquet(users_path)
    transactions.write_parquet(tx_path)
    merchants_df.write_parquet(merchants_path)
    planted_df.write_parquet(planted_path)

    users.write_csv(raw_dir / "users.csv")
    transactions.write_csv(raw_dir / "transactions.csv")
    merchants_df.write_csv(raw_dir / "merchants.csv")

    summary = {
        "n_users": users.height,
        "n_transactions": transactions.height,
        "n_planted_events": planted_df.height,
        "fraud_txns": int(transactions["is_fraud_label"].sum()),
        "duplicate_charge_txns": int(transactions["is_duplicate_charge_label"].sum()),
        "date_min": str(transactions["posted_date"].min()),
        "date_max": str(transactions["posted_date"].max()),
    }
    (gt_dir / "generation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "users": users_path,
        "transactions": tx_path,
        "merchants": merchants_path,
        "planted_events": planted_path,
        "summary": gt_dir / "generation_summary.json",
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from config import load_settings

    paths = generate_all(load_settings())
    print("Generated:")
    for k, v in paths.items():
        print(f"  {k}: {v}")