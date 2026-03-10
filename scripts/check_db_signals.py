import sqlite3
import pandas as pd

import os
db_path = os.path.join(os.path.dirname(__file__), "..", "data", "weatheredge.db")
conn = sqlite3.connect(db_path)
query = "SELECT timestamp, question, outcome, model_prob, market_prob, edge FROM signals ORDER BY timestamp DESC LIMIT 20"
df = pd.read_sql_query(query, conn)
print(df)
conn.close()
