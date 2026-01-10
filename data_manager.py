import json
import os
import uuid
from datetime import datetime

DATA_FILE = "articles_data.json"
PREFS_FILE = "preferences.json"

class DataManager:
    def __init__(self):
        self.data_file = DATA_FILE
        self.prefs_file = PREFS_FILE
        self._ensure_data_file()
        self._ensure_prefs_file()

    def _ensure_data_file(self):
        if not os.path.exists(self.data_file):
            with open(self.data_file, 'w') as f:
                json.dump([], f)
                
    def _ensure_prefs_file(self):
        if not os.path.exists(self.prefs_file):
            with open(self.prefs_file, 'w') as f:
                json.dump({"font_size": 18}, f)

    def get_all_articles(self):
        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def save_article(self, article_data):
        articles = self.get_all_articles()
        
        # Add metadata if not present
        if "id" not in article_data:
            article_data["id"] = str(uuid.uuid4())
        if "added_at" not in article_data:
            article_data["added_at"] = datetime.now().isoformat()
        if "status" not in article_data:
            article_data["status"] = "Not Started"
        if "notes" not in article_data:
            article_data["notes"] = ""
        if "last_error" not in article_data:
            article_data["last_error"] = ""
            
        articles.append(article_data)
        self._save_to_file(articles)
        return article_data

    def update_article(self, article_id, updates):
        articles = self.get_all_articles()
        for article in articles:
            if article["id"] == article_id:
                article.update(updates)
                break
        self._save_to_file(articles)

    def delete_article(self, article_id):
        articles = self.get_all_articles()
        articles = [a for a in articles if a["id"] != article_id]
        self._save_to_file(articles)

    def _save_to_file(self, articles):
        with open(self.data_file, 'w') as f:
            json.dump(articles, f, indent=4)
            
    # --- Preferences Methods ---
    def get_preferences(self):
        try:
            with open(self.prefs_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"font_size": 18}

    def save_preferences(self, prefs):
        with open(self.prefs_file, 'w') as f:
            json.dump(prefs, f, indent=4)
