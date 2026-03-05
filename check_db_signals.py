import sqlite3
import pandas as pd

conn = sqlite3.connect('weatheredge.db')
query = "SELECT timestamp, question, outcome, model_prob, market_prob, edge FROM signals ORDER BY timestamp DESC LIMIT 20"
df = pd.read_sql_query(query, conn)
print(df)
conn.close()
