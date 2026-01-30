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

    def set_backend(self, sheet_manager, sheet_name, load=True):
        """
        Attaches a SheetManager backend for persistent storage.
        If load=True, loads data from the sheet immediately.
        """
        self.sm = sheet_manager
        self.sheet_name = sheet_name
        
        if load:
            # Load from remote DB
            try:
                remote_data = self.sm.load_db(self.sheet_name)
                if remote_data:
                    # Filter out blacklisted URLs from remote data
                    prefs = self.get_preferences()
                    deleted_urls = set(u.strip().rstrip('/') for u in prefs.get("deleted_urls", []) if u)
                    
                    filtered_remote = []
                    for a in remote_data:
                        url = a.get("url", "").strip().rstrip('/')
                        # If it has a URL and that URL is in the blacklist, skip it
                        if url and url in deleted_urls:
                            continue
                        filtered_remote.append(a)
                    
                    remote_data = filtered_remote

                    # Merge logic:
                    # 1. Create a map of Remote articles by ID
                    remote_map = {a.get("id"): a for a in remote_data}
                    
                    # 2. Iterate through local cache. 
                    # If ID exists in remote, use remote (Source of Truth).
                    # If ID does NOT exist in remote, KEEP local (assume it's new and hasn't synced yet).
                    merged = []
                    
                    # Start with remote data as the base
                    merged = list(remote_data)
                    remote_ids = set(remote_map.keys())
                    
                    # Add any local items that are missing from remote
                    # (e.g. items added while offline or if last sync failed)
                    for local_art in self.articles_cache:
                        if local_art.get("id") not in remote_ids:
                            merged.append(local_art)
                    
                    self.articles_cache = merged
                    self._save_to_local(merged)
                    
                    # If we found local items that weren't in remote, we should probably trigger a save?
                    # For now, let's just update memory/local file. Next save action will push them.
                else:
                    # Remote is empty.
                    # If local cache is empty, try reloading from disk just in case
                    if not self.articles_cache:
                        self.articles_cache = self._load_from_local()
                    
                    if self.articles_cache:
                        # If remote is empty but local has data, migrate local to remote
                        print("Remote DB empty. Migrating local data...")
                        success, msg = self.sm.save_db(self.sheet_name, self.articles_cache)
                        if not success:
                            print(f"Migration failed: {msg}")
            except Exception as e:
                print(f"Error loading from backend: {e}")

    def get_all_articles(self):
        return self.articles_cache

    def get_active_articles(self):
        return [a for a in self.articles_cache if a.get("status") != "Deleted"]

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

    def delete_article(self, article_id, hard=False):
        if hard:
            self.articles_cache = [a for a in self.articles_cache if a["id"] != article_id]
        else:
            # Soft delete by default to prevent re-sync
            found = False
            for article in self.articles_cache:
                if article["id"] == article_id:
                    article["status"] = "Deleted"
                    found = True
                    break
            # Fallback if not found or if we want to ensure it's removed if corrupted
            if not found and hard:
                 self.articles_cache = [a for a in self.articles_cache if a["id"] != article_id]
                 
        self._save()

    def purge_article(self, article_id):
        """
        Permanently delete article and add URL to ignored list so it doesn't re-sync.
        """
        article = next((a for a in self.articles_cache if a["id"] == article_id), None)
        if article:
            url = article.get("url")
            if url:
                prefs = self.get_preferences()
                deleted = set(prefs.get("deleted_urls", []))
                deleted.add(url)
                prefs["deleted_urls"] = list(deleted)
                self.save_preferences(prefs)
            
            # Remove from cache
            self.articles_cache = [a for a in self.articles_cache if a["id"] != article_id]
            self._save()

    def clear_all_articles(self):
        """
        Delete ALL articles from local cache and remote DB.
        Does NOT add them to blacklist.
        Used for hard resets.
        """
        self.articles_cache = []
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
            
    def get_storage_info(self):
        """Returns debug info about storage."""
        info = {
            "data_file": os.path.abspath(self.data_file),
            "prefs_file": os.path.abspath(self.prefs_file),
            "cache_size": len(self.articles_cache),
            "remote_connected": bool(self.sm and self.sheet_name)
        }
        if os.path.exists(self.data_file):
            info["file_size_bytes"] = os.path.getsize(self.data_file)
        else:
            info["file_size_bytes"] = 0
        return info

    def get_preferences(self):
        try:
            with open(self.prefs_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"font_size": 18}

    def save_preferences(self, prefs):
        with open(self.prefs_file, 'w') as f:
            json.dump(prefs, f, indent=4)
