import sqlite3
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("migrate_db")

def migrate():
    db_path = "weatheredge.db"
    if not os.path.exists(db_path):
        logger.error(f"Database file {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. Migrate 'signals' table
        cursor.execute("PRAGMA table_info(signals)")
        signals_columns = [row[1] for row in cursor.fetchall()]

        if "confidence_multiplier" not in signals_columns:
            logger.info("Adding 'confidence_multiplier' to 'signals' table...")
            cursor.execute("ALTER TABLE signals ADD COLUMN confidence_multiplier FLOAT DEFAULT 1.0")
        
        if "regime_flags" not in signals_columns:
            logger.info("Adding 'regime_flags' to 'signals' table...")
            cursor.execute("ALTER TABLE signals ADD COLUMN regime_flags JSON")

        # 2. Migrate 'results' table
        cursor.execute("PRAGMA table_info(results)")
        results_columns = [row[1] for row in cursor.fetchall()]

        if "actual_fill_price" not in results_columns:
            logger.info("Adding 'actual_fill_price' to 'results' table...")
            cursor.execute("ALTER TABLE results ADD COLUMN actual_fill_price FLOAT")

        if "actual_slippage" not in results_columns:
            logger.info("Adding 'actual_slippage' to 'results' table...")
            cursor.execute("ALTER TABLE results ADD COLUMN actual_slippage FLOAT")

        # 3. Migrate 'brier_scores' table
        cursor.execute("PRAGMA table_info(brier_scores)")
        brier_scores_columns = [row[1] for row in cursor.fetchall()]

        if "baseline_brier" not in brier_scores_columns:
            logger.info("Adding 'baseline_brier' to 'brier_scores' table...")
            cursor.execute("ALTER TABLE brier_scores ADD COLUMN baseline_brier FLOAT")

        conn.commit()
        logger.info("Database migration completed successfully.")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
