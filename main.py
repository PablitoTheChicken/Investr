import praw
import openai
import yfinance as yf
import requests
import re
import matplotlib.pyplot as plt
from datetime import datetime
from io import BytesIO
import json
import time
from prawcore.exceptions import ServerError, RequestException, ResponseException

# --- CONFIGURATION ---

key = "sk"
# add to string
key += "-proj-bczafP9hW7WLBs_Q3SnHkKXmevDuYyqYaFSyMu0V-7Y_68dhD1rrcJjmkVsrTx0qkNEyycsvHdT3BlbkFJIeq5E8HpPRXGV_nWua_IQ4e49VmVfLHrgIq_aUvvrTNzJUvlCIAPCGgpWtEpfDST1ZfJ-CzDIA"
openai.api_key = key

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1423613687293542470/mN6-UuFaa-w9aHu6NUE5sEQXaqmnXye6ihfOue79xKVF4ztBkZPDmwFbbaTzGpLxdz7q"

IGNORE_WORDS = {"NASDAQ", "NYSE", "PENNY", "STOCKS", "THE", "AND", "FOR", "WITH", "TO"}

# --- REDDIT SETUP ---

reddit = praw.Reddit(
    client_id="zzlkUQIB-HY53ICxyGh41g",
    client_secret="U_E9HVWjgnJMQ_tgSYoCqVBirkMNIw",
    user_agent="pennystocks-bot by u/Jableeto"
)

subreddit = reddit.subreddit("pennystocks")

# --- HELPER FUNCTIONS ---
def format_currency(value):
    if value is None:
        return "N/A"
    return "${:,.2f}".format(value)

def calculate_percentage_change(hist):
    """Return percentage change over last 1 day and last 5 days"""
    closes = hist['Close'].tail(5)
    if len(closes) < 2:
        return None, None
    last1 = ((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]) * 100
    last5 = ((closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0]) * 100
    return last1, last5

def extract_tickers(text):
    print(text)
    tickers = set()
    
    # $TICKER format: only letters, 1–5 characters
    for word in text.split():
        if word.startswith("$"):
            candidate = re.match(r"\$([A-Z]{1,5})$", word)
            if candidate:
                tickers.add(candidate.group(1))
    
    # Fully uppercase words, 2–5 letters, ignore file extensions and numbers
    uppercase_words = re.findall(r'\b[A-Z]{2,5}\b', text)
    for word in uppercase_words:
        if word not in IGNORE_WORDS and not re.search(r'\d', word):
            # Ignore if it looks like a filename (gif, png, jpg, etc.)
            if not re.search(r'\.(GIF|PNG|JPG|JPEG|MP4|MOV)$', word, re.IGNORECASE):
                tickers.add(word)
    
    return list(tickers)

def analyze_sentiment(text):
    """Analyze sentiment with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            prompt = f"""
You are a financial analyst focused on short-term penny stock movements. 
Analyze the following Reddit post and comments. 

1. Determine if the sentiment is Positive, Negative, or Neutral.
2. Identify potential catalysts (reasons for a price increase or decrease).
3. Summarize in JSON format with fields: sentiment, catalyst.

Text: {text}
"""
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=30
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"OpenAI API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                return json.dumps({"sentiment": "Neutral", "catalyst": "Analysis failed"})

def get_stock_data(ticker):
    """Get stock data with error handling"""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="7d")
        info = {
            "current_price": stock.info.get('currentPrice', None),
            "market_cap": stock.info.get('marketCap', None),
            "volume": stock.info.get('volume', None)
        }
        return hist, info
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None, None

def generate_graph(hist, ticker):
    plt.figure(figsize=(6,3))
    hist['Close'].plot(title=f"{ticker} Last 7 Days")
    plt.ylabel("Price ($)")
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    plt.close()
    buf.seek(0)
    return buf

def generate_html(title, url, sentiment, tickers_info):
    html_content = f"<html><body><h2><a href='{url}'>{title}</a></h2>"
    html_content += f"<p>Sentiment & Catalyst: {sentiment}</p>"
    
    for ticker, info in tickers_info.items():
        html_content += f"<h3>{ticker}</h3>"
        html_content += f"<p>Current Price: ${info['info']['current_price']} | Market Cap: {info['info']['market_cap']} | Volume: {info['info']['volume']}</p>"
        # Table of last 5 days
        last5 = info['hist'].tail(5)[['Close', 'Volume']].reset_index()
        html_content += last5.to_html(index=False)
        # Embed graph as base64
        buf = generate_graph(info['hist'], ticker)
        import base64
        img_str = base64.b64encode(buf.getvalue()).decode()
        html_content += f"<img src='data:image/png;base64,{img_str}'/><br>"
    
    html_content += "</body></html>"
    return html_content

def send_discord_alert(title, url, sentiment, tickers_info, report_url=None):
    """Send Discord alert with retry logic"""
    color_map = {"Positive": 0x00ff00, "Neutral": 0xffa500, "Negative": 0xff0000}
    try:
        sentiment_json = json.loads(sentiment)
        sentiment_value = sentiment_json.get("sentiment", "Neutral")
    except:
        sentiment_value = "Neutral"

    embed_color = color_map.get(sentiment_value, 0x808080)

    fields = []
    for ticker, info in tickers_info.items():
        hist = info['hist']
        info_data = info['info']

        if not info_data['current_price']:
            continue
        
        last_prices = hist['Close'].tail(5).tolist()
        last_prices_str = ", ".join([f"${p:.2f}" for p in last_prices])
        
        pct1, pct5 = calculate_percentage_change(hist)
        pct_str = ""
        if pct1 is not None and pct5 is not None:
            pct_str = f"1-day: {pct1:+.2f}% | 5-day: {pct5:+.2f}%"

        fields.append({
            "name": f"{ticker} - Current: {format_currency(info_data['current_price'])}",
            "value": f"[View Chart](https://www.google.com/finance/quote/{ticker}:NASDAQ)\n"
                     f"Last 5 days: {last_prices_str}\n"
                     f"Market Cap: {format_currency(info_data['market_cap'])}\n"
                     f"Volume: {info_data['volume']}\n"
                     f"Change: {pct_str}",
            "inline": False
        })

    embed = {
        "username": "Jon Wedel",
        "embeds": [{
            "title": title,
            "url": url,
            "color": embed_color,
            "fields": fields,
            "footer": {"text": f"Sentiment & Catalyst: {sentiment}"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }

    if report_url:
        embed['embeds'][0]['description'] = f"View full report [here]({report_url})"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=embed, timeout=10)
            response.raise_for_status()
            print(f"Discord alert sent successfully")
            return
        except requests.exceptions.RequestException as e:
            print(f"Discord webhook error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

def process_submission(submission):
    """Process a single submission with error handling"""
    try:
        title = submission.title
        body = submission.selftext
        full_text = f"{title}\n{body}"

        sentiment_result = analyze_sentiment(full_text)
        tickers = extract_tickers(full_text)
        tickers_info = {}
        
        for ticker in tickers:
            hist, info = get_stock_data(ticker)
            if hist is not None and info is not None and not hist.empty:
                tickers_info[ticker] = {"hist": hist, "info": info}

        if tickers_info:
            send_discord_alert(title, submission.url, sentiment_result, tickers_info)
            print(f"Alert sent for {title} with tickers {list(tickers_info.keys())}")
        else:
            print(f"No valid tickers detected for post: {title}")
    except Exception as e:
        print(f"Error processing submission '{submission.title}': {e}")

# --- MAIN LOGIC WITH RETRY ---
def main():
    print("Starting stream...")
    retry_delay = 5
    max_retry_delay = 300  # 5 minutes
    
    while True:
        try:
            for submission in subreddit.stream.submissions(skip_existing=True, pause_after=0):
                if submission is None:
                    # Stream caught up, continue
                    continue
                    
                process_submission(submission)
                retry_delay = 5  # Reset delay on successful processing
                
        except (ServerError, RequestException, ResponseException, ConnectionError) as e:
            print(f"Connection error: {e}")
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)  # Exponential backoff
            
        except KeyboardInterrupt:
            print("\nBot stopped by user")
            break
            
        except Exception as e:
            print(f"Unexpected error: {e}")
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

if __name__ == "__main__":
    main()