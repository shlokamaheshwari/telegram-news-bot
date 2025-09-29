#!/usr/bin/env python3
"""
Telegram News Bot - GitHub Actions Version
Runs once per execution, designed for scheduled workflows
"""

import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime, timedelta
import re
import sqlite3
from dataclasses import dataclass
from typing import List
import asyncio
import hashlib
import os

try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(["pip", "install", "python-telegram-bot"])
    from telegram import Bot
    TELEGRAM_AVAILABLE = True

@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_time: str
    importance_score: int
    content_hash: str = ""

class TelegramNewsBot:
    def __init__(self):
        # Get credentials from environment variables
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.channel_username = os.getenv('TELEGRAM_CHANNEL_USERNAME')
        
        if not self.bot_token or not self.channel_username:
            raise ValueError("Missing environment variables: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_USERNAME")
        
        # Ensure channel username format
        if not self.channel_username.startswith('@') and not self.channel_username.startswith('-'):
            self.channel_username = '@' + self.channel_username
        
        self.bot = Bot(token=self.bot_token)
        print(f"Bot initialized for channel: {self.channel_username}")
        
        # News sources
        self.news_sources = {
            'Economic Times Tech': 'https://economictimes.indiatimes.com/tech/rss/feedsdefault.cms',
            'LiveMint Tech': 'https://www.livemint.com/rss/technology',
            'Inc42': 'https://inc42.com/feed/',
            'YourStory': 'https://yourstory.com/feed',
            'MoneyControl Tech': 'https://www.moneycontrol.com/rss/technology.xml',
            'TechCrunch': 'https://feeds.feedburner.com/TechCrunch/',
            'The Verge': 'https://www.theverge.com/rss/index.xml',
            'Ars Technica': 'http://feeds.arstechnica.com/arstechnica/index/',
            'VentureBeat': 'https://feeds.feedburner.com/venturebeat/SZYF',
            'Reuters Tech': 'https://feeds.reuters.com/reuters/technologyNews',
            'Bloomberg Tech': 'https://feeds.bloomberg.com/technology/news.rss',
            'CNBC Tech': 'https://www.cnbc.com/id/19854910/device/rss/rss.html',
            'BBC Tech': 'http://feeds.bbci.co.uk/news/technology/rss.xml',
            'Times of India Tech': 'https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms',
            'CoinDesk': 'https://feeds.feedburner.com/CoinDesk',
        }
        
        self.high_impact_keywords = {
            'breaking_urgent': ['breaking', 'urgent', 'alert', 'major', 'massive', 'historic'],
            'indian_companies': ['paytm', 'flipkart', 'zomato', 'swiggy', 'byju', 'ola', 'phonepe', 'tcs', 'infosys', 'reliance', 'jio'],
            'global_tech': ['apple', 'google', 'microsoft', 'amazon', 'meta', 'tesla', 'nvidia', 'openai', 'chatgpt'],
            'high_impact': ['ipo', 'acquisition', 'merger', 'funding', 'layoffs', 'hack', 'breach', 'crash', 'surge'],
            'tech_trends': ['ai', 'crypto', 'bitcoin', 'ethereum', 'blockchain', '5g', 'quantum']
        }
        
        self.setup_database()
    
    def setup_database(self):
        self.conn = sqlite3.connect('telegram_news.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                content_hash TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                source TEXT,
                importance_score INTEGER,
                scraped_at TEXT,
                sent_to_channel BOOLEAN DEFAULT FALSE
            )
        ''')
        self.conn.commit()
    
    def create_content_hash(self, title: str, url: str) -> str:
        clean_title = re.sub(r'[^\w\s]', '', title.lower()).strip()
        content = f"{clean_title}:{url}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def is_article_sent(self, content_hash: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('SELECT content_hash FROM articles WHERE content_hash = ? AND sent_to_channel = 1', (content_hash,))
        return cursor.fetchone() is not None
    
    def calculate_importance_score(self, title: str, content: str = '') -> int:
        score = 0
        text = (title + ' ' + content).lower()
        title_lower = title.lower()
        
        for category, keywords in self.high_impact_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in text)
            if matches > 0:
                if category == 'breaking_urgent':
                    score += matches * 8
                elif category == 'high_impact':
                    score += matches * 6
                elif category in ['indian_companies', 'global_tech']:
                    score += matches * 4
                else:
                    score += matches * 3
        
        if any(word in title_lower for word in ['breaking', 'major', 'massive']):
            score += 8
        if any(word in title_lower for word in ['india', 'indian']):
            score += 3
        
        return min(score, 15)
    
    def scrape_rss_feed(self, source_name: str, feed_url: str) -> List[NewsItem]:
        articles = []
        
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            try:
                soup = BeautifulSoup(response.content, 'xml')
            except:
                soup = BeautifulSoup(response.content, 'html.parser')
            
            items = soup.find_all('item')[:10]
            
            for item in items:
                try:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    desc_elem = item.find('description')
                    
                    if title_elem and link_elem:
                        title = title_elem.get_text().strip()
                        url = link_elem.get_text().strip()
                        description = desc_elem.get_text().strip() if desc_elem else ''
                        description = re.sub(r'<[^>]+>', '', description)
                        
                        importance = self.calculate_importance_score(title, description)
                        
                        if importance >= 7:
                            content_hash = self.create_content_hash(title, url)
                            
                            if not self.is_article_sent(content_hash):
                                articles.append(NewsItem(
                                    title=title,
                                    url=url,
                                    source=source_name,
                                    published_time=datetime.now().strftime('%Y-%m-%d %H:%M'),
                                    importance_score=importance,
                                    content_hash=content_hash
                                ))
                except:
                    continue
            
            if articles:
                print(f"  Found {len(articles)} new articles from {source_name}")
            
        except Exception as e:
            print(f"  Error with {source_name}: {str(e)[:50]}")
        
        return articles
    
    def save_article(self, article: NewsItem, sent: bool = False):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            article.content_hash,
            article.url,
            article.title,
            article.source,
            article.importance_score,
            datetime.now().isoformat(),
            sent
        ))
        self.conn.commit()
    
    async def send_to_channel(self, article: NewsItem):
        try:
            if article.importance_score >= 12:
                emoji = "🚨"
                urgency = "BREAKING"
            elif article.importance_score >= 9:
                emoji = "📢"
                urgency = "IMPORTANT"
            else:
                emoji = "⚡"
                urgency = "HIGH IMPACT"
            
            message = f"""{emoji} **{urgency}**

**{article.title}**

📍 {article.source} | ⭐ {article.importance_score}/15

🔗 [Read More]({article.url})

#TechNews #Breaking"""
            
            await self.bot.send_message(
                chat_id=self.channel_username,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            print(f"  Sent: {article.title[:60]}...")
            return True
            
        except Exception as e:
            print(f"  Send error: {e}")
            return False
    
    async def run_once(self):
        """Run one complete cycle - for GitHub Actions"""
        print(f"\nStarting news check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)
        
        all_articles = []
        
        print("\nScanning news sources...")
        for source_name, feed_url in self.news_sources.items():
            articles = self.scrape_rss_feed(source_name, feed_url)
            if articles:
                all_articles.extend(articles)
                for article in articles:
                    self.save_article(article)
            await asyncio.sleep(0.5)
        
        all_articles.sort(key=lambda x: x.importance_score, reverse=True)
        
        # Limit to top 10 to avoid spam
        top_articles = all_articles[:10]
        
        if top_articles:
            print(f"\nFound {len(top_articles)} high-impact articles to send")
            print("\nSending to channel...")
            
            sent_count = 0
            for article in top_articles:
                success = await self.send_to_channel(article)
                if success:
                    sent_count += 1
                    self.save_article(article, sent=True)
                await asyncio.sleep(2)
            
            print(f"\nSummary: Sent {sent_count}/{len(top_articles)} articles")
        else:
            print("\nNo new high-impact articles found")
        
        print("="*60)
        print("Run completed successfully\n")

async def main():
    try:
        bot = TelegramNewsBot()
        await bot.run_once()
    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())
