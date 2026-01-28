import gspread
from google.oauth2.service_account import Credentials
import json
import os

class SheetManager:
    def __init__(self):
        self.scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        self.creds = None
        self.client = None
        
    def authenticate(self, creds_json_content):
        """
        Authenticate using the provided JSON credentials content (dict).
        """
        try:
            self.creds = Credentials.from_service_account_info(creds_json_content, scopes=self.scope)
            self.client = gspread.authorize(self.creds)
            return True, "Authentication successful"
        except Exception as e:
            return False, f"Authentication failed: {str(e)}"
            
    def get_new_urls(self, sheet_identifier, existing_urls):
        """
        Fetch URLs from the first column of the sheet that are not in existing_urls.
        Assumes URLs are in Column A.
        Returns a list of new URLs.
        
        sheet_identifier: Can be the Sheet Name (e.g., "My Sheet") OR the Sheet ID (the long string in the URL).
        """
        if not self.client:
            return [], "Not authenticated"
            
        try:
            # Try to open the sheet by key (ID) first, then by name
            try:
                # Check if it looks like an ID (long alphanumeric string) or Name
                # A simple heuristic: IDs don't usually have spaces, Names often do.
                # But safer is to just try open_by_key first if it looks like an ID, else open.
                
                # However, the user might paste the whole URL. Let's handle that.
                if "docs.google.com" in sheet_identifier:
                     sheet = self.client.open_by_url(sheet_identifier).sheet1
                else:
                    # Try opening by key (assuming it's an ID)
                    try:
                         sheet = self.client.open_by_key(sheet_identifier).sheet1
                    except gspread.exceptions.APIError:
                        # If key fails (e.g. it's actually a name), try by name
                        sheet = self.client.open(sheet_identifier).sheet1
                        
            except gspread.exceptions.SpreadsheetNotFound:
                return [], f"Spreadsheet '{sheet_identifier}' not found. Please check the Name/ID/URL and ensure it is shared with the service account email."
            except gspread.exceptions.APIError as e:
                return [], f"Google API Error: {e.response.text if hasattr(e, 'response') else str(e)}"
                
            # Get all values from column A (assuming URLs are there)
            # Use col_values(1) which gets the entire column
            urls = sheet.col_values(1)
            
            # Filter out headers if "http" is not in the string or it's empty
            valid_urls = [url.strip() for url in urls if url.strip().startswith("http")]
            
            # Find new ones
            new_urls = [url for url in valid_urls if url not in existing_urls]
            
            return new_urls, None
        except Exception as e:
            return [], f"Error accessing sheet: {str(e)}"

    def get_urls(self, sheet_identifier):
        """
        Fetch all valid URLs from the first column of the sheet (Column A).
        Returns (urls, error) where urls is a list of strings.
        """
        if not self.client:
            return [], "Not authenticated"
        try:
            if "docs.google.com" in sheet_identifier:
                 sheet = self.client.open_by_url(sheet_identifier).sheet1
            else:
                try:
                     sheet = self.client.open_by_key(sheet_identifier).sheet1
                except gspread.exceptions.APIError:
                    sheet = self.client.open(sheet_identifier).sheet1
            urls = sheet.col_values(1)
            valid_urls = [url.strip() for url in urls if url.strip().startswith("http")]
            return valid_urls, None
        except gspread.exceptions.SpreadsheetNotFound:
            return [], f"Spreadsheet '{sheet_identifier}' not found. Please check the Name/ID/URL and ensure it is shared with the service account email."
        except gspread.exceptions.APIError as e:
            return [], f"Google API Error: {e.response.text if hasattr(e, 'response') else str(e)}"
        except Exception as e:
            return [], f"Error accessing sheet: {str(e)}"
    def _get_db_sheet(self, sheet_identifier):
        """
        Helper to get or create the 'Tracklight_DB' worksheet.
        """
        if not self.client:
            return None
            
        try:
            # Open the spreadsheet
            if "docs.google.com" in sheet_identifier:
                sh = self.client.open_by_url(sheet_identifier)
            else:
                try:
                    sh = self.client.open_by_key(sheet_identifier)
                except gspread.exceptions.APIError:
                    sh = self.client.open(sheet_identifier)

            # Try to get the worksheet
            try:
                ws = sh.worksheet("Tracklight_DB")
                # Force unhide if it exists
                if ws.hidden:
                    # gspread doesn't have a direct 'unhide' method in older versions, 
                    # but we can try to update property if supported or just ignore.
                    # Usually accessing it is enough.
                    pass
            except gspread.exceptions.WorksheetNotFound:
                # Create it if it doesn't exist
                ws = sh.add_worksheet(title="Tracklight_DB", rows=1000, cols=2)
                # Initialize with empty list
                ws.update('A1', [['ID', 'JSON_Data']])
            return ws
        except Exception as e:
            print(f"Error getting DB sheet: {e}")
            return None

    def load_db(self, sheet_identifier):
        """
        Loads all articles from Tracklight_DB worksheet (row-based).
        """
        return self.load_db_rows(sheet_identifier)

    def save_db(self, sheet_identifier, articles):
        """
        Saves all articles to Tracklight_DB worksheet (overwrite).
        """
        ws = self._get_db_sheet(sheet_identifier)
        if not ws:
            return False, "Could not access DB sheet"
        
        try:
            # We store the entire JSON dump in cell A2
            # This is simple but has a cell character limit (50k chars).
            # If articles grow large, we might need to split or use rows.
            # For now, let's try to store as rows if it's too big, or just simple JSON string if small.
            
            # BETTER APPROACH: Store each article as a JSON string in a row
            # Column A: ID, Column B: JSON Data
            
            # Let's check how we want to do this.
            # A simple JSON dump in one cell is risky for size.
            # Storing each article in a row is safer.
            
            # Let's rewrite:
            # Clear sheet
            ws.clear()
            ws.update('A1', [['ID', 'JSON_Data']])
            
            rows = []
            for art in articles:
                rows.append([str(art.get('id', '')), json.dumps(art)])
            
            if rows:
                ws.update(f'A2:B{len(rows)+1}', rows)
                
            return True, "Saved"
        except Exception as e:
            return False, f"Error saving DB: {str(e)}"

    def load_db_rows(self, sheet_identifier):
        """
        Loads articles from rows (safer for size).
        """
        ws = self._get_db_sheet(sheet_identifier)
        if not ws:
            return []
        
        try:
            # Get all values
            rows = ws.get_all_values()
            if not rows or len(rows) < 2:
                return []
            
            articles = []
            # Skip header
            for row in rows[1:]:
                if len(row) >= 2 and row[1]:
                    try:
                        articles.append(json.loads(row[1]))
                    except:
                        pass
            return articles
        except Exception as e:
            print(f"Error loading DB rows: {e}")
            return []

    def write_status(self, sheet_name, url, status):
        """
        Write a status back to the sheet next to the URL (optional feature).
        For now, just a placeholder if needed later.
        """
        pass

    # --- Database Persistence Methods ---
    def _get_db_sheet(self, sheet_identifier):
        if not self.client: return None
        try:
            # Open the spreadsheet
            if "docs.google.com" in sheet_identifier:
                 sh = self.client.open_by_url(sheet_identifier)
            else:
                try:
                     sh = self.client.open_by_key(sheet_identifier)
                except gspread.exceptions.APIError:
                    sh = self.client.open(sheet_identifier)
            
            # Look for 'Tracklight_DB' worksheet
            try:
                ws = sh.worksheet("Tracklight_DB")
            except gspread.exceptions.WorksheetNotFound:
                # Create it
                ws = sh.add_worksheet(title="Tracklight_DB", rows=1000, cols=2)
                ws.append_row(["id", "json_data"])
            return ws
        except Exception:
            return None

    def load_db(self, sheet_identifier):
        """Loads all articles from Tracklight_DB worksheet."""
        ws = self._get_db_sheet(sheet_identifier)
        if not ws: return []
        
        try:
            # Get all values (skipping header)
            rows = ws.get_all_values()
            if len(rows) < 2: return []
            
            articles = []
            for r in rows[1:]: # Skip header
                if len(r) >= 2:
                    try:
                        articles.append(json.loads(r[1]))
                    except: pass
            return articles
        except Exception:
            return []

    def save_db(self, sheet_identifier, articles):
        """Saves all articles to Tracklight_DB worksheet (overwrite)."""
        ws = self._get_db_sheet(sheet_identifier)
        if not ws: return False
        
        try:
            # Prepare rows
            rows = [["id", "json_data"]]
            for a in articles:
                rows.append([str(a.get("id", "")), json.dumps(a)])
            
            # Clear and write
            ws.clear()
            ws.update(rows)
            return True
        except Exception:
            return False
