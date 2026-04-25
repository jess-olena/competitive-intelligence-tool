import sqlite3
tickers_to_keep = ('AAPL', 'MSFT', 'NVDA', 'GOOGL', 'NEE', 'XOM', 'CVX')
conn = sqlite3.connect(r'db\financial.db')
conn.execute(f"DELETE FROM news_articles WHERE ticker NOT IN {tickers_to_keep}")
conn.execute(f"DELETE FROM company_sentiment_index WHERE ticker NOT IN {tickers_to_keep}")
conn.commit()
conn.close()