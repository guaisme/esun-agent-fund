from __future__ import annotations

import json
from decimal import Decimal, localcontext
from pathlib import Path

from src.competition.models import AccountingError, PortfolioState, PriceBar, TradeOrder
from src.competition.simulator import PortfolioSimulator
from src.data.universe import UniverseLoader
from src.dplan.generator import DPlanGenerator, write_dplan
from src.dplan.validation import DPlanValidator


def replay_fixture(path: Path, output: Path | None = None) -> dict:
    fixture = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if fixture.get("synthetic") is not True or fixture.get("model_version") != "SYNTHETIC_FIXTURE":
        raise AccountingError("Offline replay requires clearly labeled synthetic fixture")
    universe = UniverseLoader()
    tickers = fixture["tickers"]
    if any(not universe.is_valid_ticker(t) for t in tickers):
        raise AccountingError("Fixture ticker outside official universe")
    state = PortfolioState()
    previous_close = Decimal(fixture["previous_close"])
    history = []
    sessions = {row["trade_date"] for row in fixture["sessions"]}
    for row in fixture["sessions"]:
        on = row["trade_date"]
        previous_closes = {t: previous_close for t in tickers}
        previous_nav = state.cash + sum(
            (p.shares * previous_close for p in state.positions.values()), Decimal(0)
        )
        with localcontext() as context:
            context.prec = 50
            target_shares = {t: p.shares for t, p in state.positions.items()}
            for ticker, shares in row["target_shares"].items():
                if type(shares) is not int or shares < 0 or shares % 1000:
                    raise AccountingError("Fixture target shares must be whole lots")
                target_shares[ticker] = shares
            targets = {
                t: (
                    (Decimal(shares) + Decimal("0.1")) * previous_close / previous_nav
                    if shares
                    else Decimal(0)
                )
                for t, shares in target_shares.items()
            }
            intent = row["posture"]
            sources = [
                {
                    "source_id": "S1",
                    "authority": "other",
                    "url": "https://example.test/SYNTHETIC_FIXTURE",
                    "content_as_of": row["previous_content_as_of"],
                    "name": "SYNTHETIC fixture; never actual market data",
                }
            ]
            observations = [
                {
                    "obs_id": "O1",
                    "source_ref": ["S1"],
                    "statement": "合成測試股票的前一交易日收盤價格相同，用於精確帳務驗證。",
                    "values": {"synthetic_previous_close": previous_close},
                }
            ]
            metadata = {
                "model_provider": "other",
                "model_version": "SYNTHETIC_FIXTURE",
                "run_started_at": f"{on}T08:00:00+08:00",
                "run_completed_at": f"{on}T08:10:00+08:00",
                "code_version": "synthetic-golden-v1",
            }
            plan = DPlanGenerator().generate(
                team_id="FIXTURE_ONLY",
                trade_date=on,
                state=state,
                previous_nav=previous_nav,
                previous_closes=previous_closes,
                targets=targets,
                sources=sources,
                observations=observations,
                market_view={
                    "basis_refs": ["O1"],
                    "logic": "依合成測試預先保存的決策意圖重播，目的為驗證證據鏈與帳務，不代表真實模型決策。",
                    "regime": "neutral",
                    "stance": "neutral",
                    "posture": {
                        "net_exposure_intent": intent,
                        "target_cash_pct_range": [0.15, 0.24],
                    },
                    "counter_evidence": None,
                },
                rationale={
                    t: {
                        "premise_refs": ["O1"],
                        "logic": "依合成測試記錄的目標部位及前日收盤價格，機械推導整張委託以驗證連續多日帳簿。",
                        "counter_evidence": None,
                    }
                    for t in targets
                },
                metadata=metadata,
            )
            validation = DPlanValidator().validate(
                plan,
                state=state,
                previous_nav=previous_nav,
                previous_closes=previous_closes,
                cutoff=f"{on}T08:30:00+08:00",
                authenticated_team="FIXTURE_ONLY",
                trading_days=sessions,
                strict=False,
            )
            if validation.status != "VALID":
                raise AccountingError(f"Fixture D-Plan invalid: {validation.issues}")
            close, average = Decimal(row["close"]), Decimal(row["average"])
            bars = {
                t: PriceBar(
                    t, on, close, 1000000, average * 1000000, "SYNTHETIC_FIXTURE", synthetic=True
                )
                for t in tickers
            }
            orders = [
                TradeOrder(order["ticker"], order["side"], order["shares"], order["decision_ref"])
                for order in plan["orders"]
            ]
            result = PortfolioSimulator().execute_day(
                state,
                on,
                orders,
                bars,
                trading_days=sessions,
                calendar_verified=False,
                active_shares={"SYNTHETIC_ETF": Decimal("0.4")},
                required_benchmarks={"SYNTHETIC_ETF"},
                strict=False,
                fixture_mode=True,
            )
            if result.status != "EXECUTED":
                raise AccountingError(f"Fixture day failed: {result.violations}")
            if row.get("expected_nav") and result.daily_nav.nav != Decimal(row["expected_nav"]):
                raise AccountingError("Hand-calculated golden fixture NAV mismatch")
            if output:
                write_dplan(output / "dplans", plan)
            history.append({"plan": plan, "validation": validation, "result": result})
            state = result.state
            previous_close = close
    return {
        "status": "OFFLINE_FIXTURE_PASSED",
        "mode": "synthetic_fixture",
        "live_model_called": False,
        "synthetic": True,
        "history": history,
        "final_state": state,
        "terminal_nav": history[-1]["result"].daily_nav.nav,
        "readiness": "NOT_COMPETITION_READY; scripted synthetic decisions, not live Gemini",
    }
