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
        
        # Persistence backend
        self.sm = None
        self.sheet_name = None
        
        # In-memory cache
        self.articles_cache = self._load_from_local()

    def _ensure_data_file(self):
        if not os.path.exists(self.data_file):
            with open(self.data_file, 'w') as f:
                json.dump([], f)
                
    def _ensure_prefs_file(self):
        if not os.path.exists(self.prefs_file):
            with open(self.prefs_file, 'w') as f:
                json.dump({"font_size": 18}, f)

    def _load_from_local(self):
        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def set_backend(self, sheet_manager, sheet_name):
        """
        Attaches a SheetManager backend for persistent storage.
        Loads data from the sheet immediately.
        """
        self.sm = sheet_manager
        self.sheet_name = sheet_name
        
        # Load from remote DB
        try:
            remote_data = self.sm.load_db(self.sheet_name)
            if remote_data:
                self.articles_cache = remote_data
                # Sync back to local file
                self._save_to_local(remote_data)
        except Exception as e:
            print(f"Error loading from backend: {e}")

    def get_all_articles(self):
        return self.articles_cache

    def save_article(self, article_data):
        # Add metadata if not present
        self._prepare_article(article_data)
        self.articles_cache.append(article_data)
        self._save()
        return article_data

    def save_articles(self, articles_list):
        """Batch save multiple articles."""
        for article_data in articles_list:
            self._prepare_article(article_data)
            self.articles_cache.append(article_data)
        self._save()
        return articles_list

    def _prepare_article(self, article_data):
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

    def update_article(self, article_id, updates):
        for article in self.articles_cache:
            if article["id"] == article_id:
                article.update(updates)
                break
        self._save()

    def delete_article(self, article_id):
        self.articles_cache = [a for a in self.articles_cache if a["id"] != article_id]
        self._save()

    def _save(self):
        # Save locally
        self._save_to_local(self.articles_cache)
        
        # Save remotely if connected
        if self.sm and self.sheet_name:
            try:
                self.sm.save_db(self.sheet_name, self.articles_cache)
            except Exception as e:
                print(f"Error saving to backend: {e}")

    def _save_to_local(self, articles):
        with open(self.data_file, 'w') as f:
            json.dump(articles, f, indent=4)
            
    def _save_to_file(self, articles):
        # Deprecated but kept for compatibility if needed internally
        self._save_to_local(articles)
            
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
