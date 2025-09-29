#!/usr/bin/env python3
"""
Telegram News Bot - GitHub Actions Version
Enhanced to deliver only latest, non-repeated, and important articles
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
from dateutil import parser as date_parser
import feedparser

try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(["pip", "install", "python-telegram-bot", "python-dateutil", "feedparser"])
    from telegram import Bot
    TELEGRAM_AVAILABLE = True

@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_time: datetime
    importance_score: int
    content_hash: str = ""
    description: str = ""

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
        
        # Only include high-quality, reliable news sources
        self.news_sources = {
            'TechCrunch': 'https://feeds.feedburner.com/TechCrunch/',
            'The Verge': 'https://www.theverge.com/rss/index.xml',
            'Ars Technica': 'http://feeds.arstechnica.com/arstechnica/index/',
            'Reuters Tech': 'https://feeds.reuters.com/reuters/technologyNews',
            'Bloomberg Tech': 'https://feeds.bloomberg.com/technology/news.rss',
            'BBC Tech': 'http://feeds.bbci.co.uk/news/technology/rss.xml',
            'Economic Times Tech': 'https://economictimes.indiatimes.com/tech/rss/feedsdefault.cms',
            'LiveMint Tech': 'https://www.livemint.com/rss/technology',
            'Inc42': 'https://inc42.com/feed/',
            'MoneyControl Tech': 'https://www.moneycontrol.com/rss/technology.xml',
            'CoinDesk': 'https://feeds.feedburner.com/CoinDesk',
        }
        
        # Enhanced keyword scoring system
        self.high_impact_keywords = {
            'breaking_urgent': {
                'keywords': ['breaking', 'urgent', 'alert', 'major', 'massive', 'historic', 'unprecedented'],
                'score': 10
            },
            'indian_companies': {
                'keywords': ['paytm', 'flipkart', 'zomato', 'swiggy', 'byju', 'ola', 'phonepe', 'tcs', 'infosys', 'reliance', 'jio', 'wipro', 'hcl'],
                'score': 6
            },
            'global_tech_giants': {
                'keywords': ['apple', 'google', 'microsoft', 'amazon', 'meta', 'tesla', 'nvidia', 'openai', 'chatgpt', 'anthropic', 'deepmind'],
                'score': 7
            },
            'high_impact_events': {
                'keywords': ['ipo', 'acquisition', 'merger', 'funding', 'layoffs', 'hack', 'breach', 'crash', 'surge', 'bankruptcy', 'scandal'],
                'score': 8
            },
            'emerging_tech': {
                'keywords': ['ai', 'artificial intelligence', 'crypto', 'bitcoin', 'ethereum', 'blockchain', '5g', 'quantum', 'autonomous', 'drone'],
                'score': 5
            },
            'financial_impact': {
                'keywords': ['billion', 'million', 'stock', 'market', 'valuation', 'revenue', 'profit', 'loss', 'earnings'],
                'score': 4
            }
        }
        
        # Time threshold for "latest" articles (24 hours)
        self.latest_threshold = timedelta(hours=24)
        
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
                published_time TEXT,
                scraped_at TEXT,
                sent_to_channel BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Create index for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sent_articles 
            ON articles (sent_to_channel, published_time)
        ''')
        
        self.conn.commit()
        
        # Clean up old articles (older than 7 days)
        self.cleanup_old_articles()
    
    def cleanup_old_articles(self):
        """Remove articles older than 7 days to keep database lean"""
        cutoff_date = (datetime.now() - timedelta(days=7)).isoformat()
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM articles WHERE scraped_at < ?', (cutoff_date,))
        deleted_count = cursor.rowcount
        self.conn.commit()
        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} old articles from database")
    
    def create_content_hash(self, title: str, url: str) -> str:
        """Create a more robust hash for duplicate detection"""
        # Clean title: remove special chars, normalize spaces, convert to lowercase
        clean_title = re.sub(r'[^\w\s]', '', title.lower()).strip()
        clean_title = re.sub(r'\s+', ' ', clean_title)  # Normalize spaces
        
        # Extract domain from URL for better duplicate detection
        url_domain = re.sub(r'https?://(www\.)?', '', url.split('/')[2] if '/' in url else url)
        
        content = f"{clean_title}:{url_domain}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def is_article_sent(self, content_hash: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('SELECT content_hash FROM articles WHERE content_hash = ? AND sent_to_channel = 1', (content_hash,))
        return cursor.fetchone() is not None
    
    def is_similar_article_sent(self, title: str) -> bool:
        """Check for similar articles using title similarity"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT title FROM articles WHERE sent_to_channel = 1')
        sent_titles = [row[0] for row in cursor.fetchall()]
        
        # Simple similarity check based on common words
        title_words = set(re.findall(r'\w+', title.lower()))
        if len(title_words) < 3:  # Too short to compare
            return False
            
        for sent_title in sent_titles:
            sent_words = set(re.findall(r'\w+', sent_title.lower()))
            if len(sent_words) < 3:
                continue
                
            # Calculate Jaccard similarity
            intersection = title_words.intersection(sent_words)
            union = title_words.union(sent_words)
            
            if len(intersection) / len(union) > 0.7:  # 70% similarity threshold
                return True
        
        return False
    
    def calculate_importance_score(self, title: str, description: str = '', published_time: datetime = None) -> int:
        """Enhanced importance scoring with time decay"""
        score = 0
        text = (title + ' ' + description).lower()
        title_lower = title.lower()
        
        # Keyword-based scoring
        for category, config in self.high_impact_keywords.items():
            keywords = config['keywords']
            keyword_score = config['score']
            
            matches = sum(1 for keyword in keywords if keyword in text)
            if matches > 0:
                score += matches * keyword_score
        
        # Title position bonus (keywords in title are more important)
        title_bonus = 0
        for category, config in self.high_impact_keywords.items():
            if category in ['breaking_urgent', 'high_impact_events']:
                for keyword in config['keywords']:
                    if keyword in title_lower:
                        title_bonus += 5
        
        score += title_bonus
        
        # Freshness bonus (newer articles get higher scores)
        if published_time:
            time_diff = datetime.now() - published_time
            if time_diff < timedelta(hours=1):
                score += 8  # Very fresh
            elif time_diff < timedelta(hours=6):
                score += 5  # Fresh
            elif time_diff < timedelta(hours=12):
                score += 2  # Somewhat fresh
        
        # Indian context bonus
        if any(word in title_lower for word in ['india', 'indian', 'delhi', 'mumbai', 'bangalore', 'bengaluru']):
            score += 4
        
        return min(score, 20)  # Cap at 20
    
    def parse_publish_date(self, date_str: str) -> datetime:
        """Parse various date formats from RSS feeds"""
        if not date_str:
            return datetime.now()
        
        try:
            # Try using feedparser's date parsing first
            parsed_date = date_parser.parse(date_str)
            return parsed_date
        except:
            try:
                # Fallback to common formats
                for fmt in ['%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S%z']:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except:
                        continue
            except:
                pass
        
        return datetime.now()
    
    def is_article_fresh(self, published_time: datetime) -> bool:
        """Check if article is within the freshness threshold"""
        time_diff = datetime.now() - published_time
        return time_diff <= self.latest_threshold
    
    def scrape_rss_feed(self, source_name: str, feed_url: str) -> List[NewsItem]:
        articles = []
        
        try:
            # Use feedparser for better RSS parsing
            feed = feedparser.parse(feed_url)
            
            if feed.bozo and feed.bozo_exception:
                print(f"  Warning: Feed parsing issue for {source_name}")
            
            # Get recent entries (limit to 15 for performance)
            entries = feed.entries[:15]
            
            for entry in entries:
                try:
                    title = entry.get('title', '').strip()
                    url = entry.get('link', '').strip()
                    
                    if not title or not url:
                        continue
                    
                    # Parse publish date
                    pub_date_str = entry.get('published') or entry.get('pubDate') or entry.get('updated', '')
                    published_time = self.parse_publish_date(pub_date_str)
                    
                    # Only process fresh articles
                    if not self.is_article_fresh(published_time):
                        continue
                    
                    # Get description/summary
                    description = entry.get('description') or entry.get('summary', '')
                    if description:
                        description = re.sub(r'<[^>]+>', '', description)  # Remove HTML tags
                        description = description.strip()[:300]  # Limit length
                    
                    # Calculate importance
                    importance = self.calculate_importance_score(title, description, published_time)
                    
                    # Only include high-importance articles
                    if importance >= 10:  # Raised threshold for higher quality
                        content_hash = self.create_content_hash(title, url)
                        
                        # Check for duplicates and similar articles
                        if not self.is_article_sent(content_hash) and not self.is_similar_article_sent(title):
                            articles.append(NewsItem(
                                title=title,
                                url=url,
                                source=source_name,
                                published_time=published_time,
                                importance_score=importance,
                                content_hash=content_hash,
                                description=description
                            ))
                
                except Exception as e:
                    continue
            
            if articles:
                print(f"  Found {len(articles)} fresh, high-impact articles from {source_name}")
            
        except Exception as e:
            print(f"  Error with {source_name}: {str(e)[:50]}")
        
        return articles
    
    def save_article(self, article: NewsItem, sent: bool = False):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            article.content_hash,
            article.url,
            article.title,
            article.source,
            article.importance_score,
            article.published_time.isoformat(),
            datetime.now().isoformat(),
            sent
        ))
        self.conn.commit()
    
    async def send_to_channel(self, article: NewsItem):
        try:
            # Enhanced message formatting based on importance
            if article.importance_score >= 18:
                emoji = "🔥"
                urgency = "🚨 BREAKING NEWS"
            elif article.importance_score >= 15:
                emoji = "📢"
                urgency = "⚡ MAJOR UPDATE"
            elif article.importance_score >= 12:
                emoji = "⭐"
                urgency = "📰 HIGH IMPACT"
            else:
                emoji = "💡"
                urgency = "🔔 IMPORTANT"
            
            # Calculate article age
            time_diff = datetime.now() - article.published_time
            if time_diff < timedelta(minutes=30):
                freshness = "🕐 Just now"
            elif time_diff < timedelta(hours=2):
                freshness = f"🕐 {int(time_diff.total_seconds() / 60)}m ago"
            else:
                freshness = f"🕐 {int(time_diff.total_seconds() / 3600)}h ago"
            
            message = f"""{urgency}

{emoji} **{article.title}**

📍 {article.source} | ⭐ {article.importance_score}/20 | {freshness}

🔗 [Read Full Article]({article.url})

#{article.source.replace(' ', '')} #TechNews #Breaking"""
            
            await self.bot.send_message(
                chat_id=self.channel_username,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            print(f"  ✅ Sent: {article.title[:60]}... (Score: {article.importance_score})")
            return True
            
        except Exception as e:
            print(f"  ❌ Send error: {e}")
            return False
    
    async def run_once(self):
        """Run one complete cycle - for GitHub Actions"""
        print(f"\n🚀 Starting enhanced news scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        all_articles = []
        
        print("\n📡 Scanning news sources for fresh, high-impact articles...")
        for source_name, feed_url in self.news_sources.items():
            print(f"  🔍 Checking {source_name}...")
            articles = self.scrape_rss_feed(source_name, feed_url)
            if articles:
                all_articles.extend(articles)
                for article in articles:
                    self.save_article(article)
            await asyncio.sleep(0.5)
        
        # Sort by importance score (highest first), then by freshness
        all_articles.sort(key=lambda x: (x.importance_score, x.published_time), reverse=True)
        
        # Limit to top 8 articles to avoid spam
        top_articles = all_articles[:8]
        
        if top_articles:
            print(f"\n📤 Found {len(top_articles)} exceptional articles to send:")
            for i, article in enumerate(top_articles, 1):
                age = datetime.now() - article.published_time
                age_str = f"{int(age.total_seconds() / 3600)}h" if age.total_seconds() > 3600 else f"{int(age.total_seconds() / 60)}m"
                print(f"  {i}. [{article.importance_score}/20] {article.title[:50]}... ({age_str} ago)")
            
            print("\n📨 Sending to channel...")
            
            sent_count = 0
            for article in top_articles:
                success = await self.send_to_channel(article)
                if success:
                    sent_count += 1
                    self.save_article(article, sent=True)
                await asyncio.sleep(3)  # Longer delay to avoid rate limiting
            
            print(f"\n✅ Summary: Successfully sent {sent_count}/{len(top_articles)} articles")
        else:
            print("\n📭 No new exceptional articles found in the last 24 hours")
        
        print("="*80)
        print("🎯 Enhanced news scan completed successfully\n")

async def main():
    try:
        bot = TelegramNewsBot()
        await bot.run_once()
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())
