"""News and AI-analysis facade."""
from .production_core import (
    is_duplicate_news,
    deduplicate_news_articles,
    fetch_telegram_channel_news,
    fetch_all_instant_news,
    get_openrouter_analysis,
    get_openrouter_gold_signal,
    analyze_news_rule_based,
    _is_gold_relevant_news,
    _gold_relevant_articles,
    _gold_rule_based_news_points,
    _gold_news_intelligence,
    _is_nasdaq_news,
    _nasdaq_relevant_articles,
)
