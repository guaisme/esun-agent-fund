from __future__ import annotations

from decimal import Decimal

from src.agent.tools import DecisionContext
from src.competition.models import AccountingError, PortfolioState
from src.data.time_safety import aware
from src.data.universe import UniverseLoader
from src.strategy.availability import AvailabilityPolicy
from src.strategy.features import FeatureEngine
from src.strategy.ranking import AlphaConfig, AlphaModel


def build_context(
    *,
    data: dict,
    state: PortfolioState,
    trade_date: str,
    cutoff: str,
    availability: AvailabilityPolicy,
    alpha: AlphaConfig,
    calendar: set[str] | None = None,
    market: list[dict] | None = None,
    benchmark_evidence: dict | None = None,
) -> DecisionContext:
    if aware(cutoff).date().isoformat() > trade_date:
        raise AccountingError("Decision cutoff must not follow trade date")
    snapshot = FeatureEngine(availability).build(
        **data, cutoff=cutoff, market=market, trading_days=calendar
    )
    ranking = AlphaModel(alpha).rank(snapshot)
    selected, _ = availability.select(data["prices"], "prices", cutoff)
    days = sorted({r["trade_date"] for r in selected})
    if not days:
        raise AccountingError("No PIT-safe previous official closing data")
    previous_day = days[-1]
    price_rows = {
        r["ticker"]: r
        for r in selected
        if r["trade_date"] == previous_day
        and r.get("close") is not None
        and r["close"] > 0
    }
    previous_closes = {t: Decimal(str(r["close"])) for t, r in price_rows.items()}
    if set(state.positions) - set(previous_closes):
        raise AccountingError("Missing previous close for settled holdings")
    previous_nav = state.cash + sum(
        (p.shares * previous_closes[t] for t, p in state.positions.items()), Decimal(0)
    )
    universe = UniverseLoader()
    candidates = [
        r["ticker"] for r in ranking["rankings"] if r["ticker"] in previous_closes
    ][:30]
    candidates = sorted(set(candidates) | set(state.positions))
    if len(candidates) > 60:
        raise AccountingError("Evidence candidate budget exceeded")
    sources, ids = [], {}
    for market_name in sorted({price_rows[t]["market"] for t in candidates}):
        identifier = f"S{len(sources) + 1}"
        ids[market_name] = identifier
        subset = [
            price_rows[t] for t in candidates if price_rows[t]["market"] == market_name
        ]
        source_name = "TWSE" if market_name == "TWSE" else "TPEx"
        # Only exchange-origin observations may claim exchange authority.
        if any(r["source"] != source_name for r in subset) and not (
            availability.mode == "synthetic_fixture"
            and all(
                r.get("synthetic") and r["source"] == "SYNTHETIC_FIXTURE"
                for r in subset
            )
        ):
            raise AccountingError(
                "Unsupported price source for official evidence authority"
            )
        sources.append(
            {
                "source_id": identifier,
                "authority": "twse" if market_name == "TWSE" else "tpex",
                "url": (
                    "https://www.twse.com.tw/exchangeReport/MI_INDEX"
                    if market_name == "TWSE"
                    else "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
                ),
                "name": f"{source_name} daily price and shares/NTD turnover",
                "content_as_of": f"{previous_day}T00:00:00+08:00",
                "fetched_at": max(r["fetched_at"] for r in subset),
            }
        )
    observations = [
        {
            "obs_id": f"O{i}",
            "source_ref": [ids[price_rows[t]["market"]]],
            "topic": "stock_price",
            "statement": f"{t} {universe.get_stock_info(t)['name']}：{previous_day} 官方未調整收盤價 {price_rows[t]['close']} 元；成交股數以股為單位。",
            "values": {
                "ticker_number": int(t),
                "close_ntd": price_rows[t]["close"],
                "volume_shares": price_rows[t]["volume"],
            },
        }
        for i, t in enumerate(candidates, 1)
        if price_rows[t].get("volume") is not None
    ]
    # Fundamental observations must accompany factors so the agent can cite
    # stock-specific evidence rather than infer earnings from price momentum.
    urls = {
        "FinMind": "https://api.finmindtrade.com/api/v4/data",
        "TWSE": "https://www.twse.com.tw/fund/T86",
        "TPEx": "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
    }
    for dataset in ("revenue", "flows"):
        selected_fundamentals, _ = availability.select(data[dataset], dataset, cutoff)
        for ticker in candidates:
            rows = [r for r in selected_fundamentals if r["ticker"] == ticker]
            if not rows:
                continue
            latest = rows[-1]
            source_name = latest["source"]
            if source_name not in urls and availability.mode != "synthetic_fixture":
                raise AccountingError("Unsupported fundamental source")
            source_id = f"S{len(sources)+1}"
            sources.append(
                dict(
                    source_id=source_id,
                    authority={"TWSE": "twse", "TPEx": "tpex"}.get(
                        source_name, "other"
                    ),
                    url=urls.get(source_name, "https://example.test/MOCK"),
                    name=f"{source_name} {dataset} {ticker}",
                    content_as_of=latest["observation_date"] + "T00:00:00+08:00",
                    fetched_at=latest["fetched_at"],
                )
            )
            factors = snapshot.values[ticker]
            if dataset == "revenue":
                values = {
                    name: factors[name]
                    for name in ("revenue_yoy", "revenue_mom", "revenue_acceleration")
                    if factors.get(name) is not None
                }
                if latest.get("monthly_revenue") is not None:
                    values["monthly_revenue_ntd"] = latest["monthly_revenue"]
                statement = (
                    f"{ticker} {latest['revenue_year']}-{latest['revenue_month']:02d} 月營收，金額單位元；"
                    "增長率為比例，非百分點。公布時間未驗證時僅自首次觀測起可用；營收不等於利潤。"
                )
            else:
                values = {
                    name: factors[name]
                    for name in factors
                    if "_flow_" in name and factors[name] is not None
                }
                statement = (
                    f"{ticker} 截至 {latest['observation_date']} 法人淨股數／期間成交股數比例；"
                    "5日／20日按實際交易日對齊，非金額，亦非財報。"
                )
            if values:
                observations.append(
                    dict(
                        obs_id=f"O{len(observations)+1}",
                        source_ref=[source_id],
                        topic=(
                            "monthly_revenue"
                            if dataset == "revenue"
                            else "institutional_flows"
                        ),
                        statement=statement,
                        values=values,
                    )
                )
    # Research-mode fetching can occur after the historical cutoff. Retain that
    # provenance in the snapshot, but do not fabricate a historical fetched_at
    # in the competition-format sources. The production gate remains blocked.
    if availability.mode == "research_assumptions":
        for source in sources:
            if aware(source["fetched_at"]) > aware(cutoff):
                del source["fetched_at"]
    evidence = {
        "status": "AVAILABLE" if market else "UNVERIFIED",
        "market_index": (
            "No verified market index supplied"
            if not market
            else "PIT-filtered supplied series"
        ),
        "universe_breadth_scope": "Official fixed 150 stocks; not entire Taiwan market",
        "factor_version": snapshot.factor_version,
    }
    return DecisionContext(
        trade_date,
        cutoff,
        state,
        previous_nav,
        previous_closes,
        snapshot,
        ranking,
        sources,
        observations,
        evidence,
        benchmark_evidence
        or {
            "status": "UNVERIFIED",
            "reason": "Official ETF snapshots unavailable; no substituted synthetic holdings",
        },
        alpha.version,
        availability.mode == "synthetic_fixture",
    )
