import asyncio
import logging
from sqlalchemy import select
from weatheredge.db import get_session
from weatheredge.models import Signal, Result, Market

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analyze_slippage")

async def analyze():
    async with get_session() as session:
        # Find all signals that have a result with an actual_fill_price recorded
        stmt = (
            select(Signal, Result, Market)
            .join(Result, Result.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .where(Result.actual_fill_price.is_not(None))
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        logger.info("No trades with actual_fill_price found in the database yet.")
        logger.info("Once live execution logs actual_slippage, empirical Kyle's Lambda validation can proceed.")
        return

    total_expected = 0.0
    total_actual = 0.0
    count = 0

    print(f"{'Market ID':<25} | {'Bet Size':<10} | {'Mkt Vol':<10} | {'Exp Slip':<10} | {'Act Slip':<10} | {'Diff'}")
    print("-" * 85)

    for sig, res, mkt in rows:
        expected = sig.slippage or 0.0
        actual = res.actual_slippage or 0.0
        
        diff = actual - expected
        total_expected += expected
        total_actual += actual
        count += 1
        
        print(f"{sig.market_id:<25} | ${sig.bet_size_usd:<9.2f} | ${mkt.volume:<9.2f} | {expected:<10.4f} | {actual:<10.4f} | {diff:+.4f}")

    print("-" * 85)
    print(f"Total Trades Analyzed: {count}")
    print(f"Average Expected Slippage: {total_expected / count:.4f}")
    print(f"Average Actual Slippage:   {total_actual / count:.4f}")
    print(f"Systematic Error:          {(total_actual - total_expected) / count:+.4f}")
    
    # Simple linear scaling factor for KYLE'S LAMBDA tuning
    if total_expected > 0:
        ratio = total_actual / total_expected
        logger.info(f"\nRecommended LAMBDA_COEFF adjustment multiplier: {ratio:.2f}x")
        logger.info("If actual slippage > expected, increase LAMBDA_COEFF in slippage.py.")

if __name__ == "__main__":
    asyncio.run(analyze())
