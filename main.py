import praw
import openai
import yfinance as yf
import requests
import re
import matplotlib.pyplot as plt
from datetime import datetime
from io import BytesIO
import json

# --- CONFIGURATION ---

key = "sk"
# add to string
key += "-proj-bczafP9hW7WLBs_Q3SnHkKXmevDuYyqYaFSyMu0V-7Y_68dhD1rrcJjmkVsrTx0qkNEyycsvHdT3BlbkFJIeq5E8HpPRXGV_nWua_IQ4e49VmVfLHrgIq_aUvvrTNzJUvlCIAPCGgpWtEpfDST1ZfJ-CzDIA"
openai.api_key =  key

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
        temperature=0
    )
    return response.choices[0].message.content

def get_stock_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="7d")
    info = {
        "current_price": stock.info.get('currentPrice', None),
        "market_cap": stock.info.get('marketCap', None),
        "volume": stock.info.get('volume', None)
    }
    return hist, info

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
    color_map = {"Positive": 0x00ff00, "Neutral": 0xffa500, "Negative": 0xff0000}
    try:
        sentiment_json = json.loads(sentiment)  # sentiment returned from OpenAI
        sentiment_value = sentiment_json.get("sentiment", "Neutral")
    except:
        sentiment_value = "Neutral"

    embed_color = color_map.get(sentiment_value, 0x808080)

    fields = []
    for ticker, info in tickers_info.items():
        hist = info['hist']
        info_data = info['info']

        if not info_data['current_price']:
            continue  # Skip if no valid stock info
        
        # Last 5 days
        last_prices = hist['Close'].tail(5).tolist()
        last_prices_str = ", ".join([f"${p:.2f}" for p in last_prices])
        
        # Percentage changes
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

    requests.post(DISCORD_WEBHOOK_URL, json=embed)

    # Generate HTML report (same as before)
    # html_content = generate_html(title, url, sentiment, tickers_info)
    # with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmpfile:
    #     tmpfile.write(html_content.encode())
    #     tmpfile_path = tmpfile.name

    # # Send HTML file
    # with open(tmpfile_path, "rb") as f:
    #     requests.post(
    #         DISCORD_WEBHOOK_URL,
    #         files={"file": (os.path.basename(tmpfile_path), f, "text/html")}
    #     )
    # os.remove(tmpfile_path)

def process_submission(submission):
    title = submission.title
    body = submission.selftext
    full_text = f"{title}\n{body}"

    sentiment_result = analyze_sentiment(full_text)
    tickers = extract_tickers(full_text)
    tickers_info = {}
    for ticker in tickers:
        hist, info = get_stock_data(ticker)
        tickers_info[ticker] = {"hist": hist, "info": info}

    if tickers_info:
        send_discord_alert(title, submission.url, sentiment_result, tickers_info)
        print(f"Alert sent for {title} with tickers {tickers}")
    else:
        print(f"No tickers detected for post: {title}")

# --- MAIN LOGIC ---
print("Starting stream...")
for submission in subreddit.stream.submissions(skip_existing=True):
    process_submission(submission)