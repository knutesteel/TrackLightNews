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
            self.service_email = self.creds.service_account_email
            self.client = gspread.authorize(self.creds)
            return True, "Authentication successful"
        except Exception as e:
            return False, f"Authentication failed: {str(e)}"
            
    def get_new_urls(self, sheet_identifier, existing_urls):
        """
        Fetch URLs from the sheet (Column A) that have NO status in Column B.
        - If URL is already in existing_urls, mark Column B as "Duplicate".
        - If URL is new, return it for processing.
        
        Returns (new_items, error_msg, stats_dict).
        new_items is a list of dicts: {'url': url, 'row': row_number}
        """
        stats = {"total_rows": 0, "valid_urls": 0, "duplicates": 0, "new": 0}
        
        if not self.client:
            return [], "Not authenticated", stats
            
        sheet_identifier = sheet_identifier.strip()
        
        try:
            # Open Sheet
            try:
                if "docs.google.com" in sheet_identifier:
                     sheet = self.client.open_by_url(sheet_identifier).sheet1
                else:
                    try:
                         sheet = self.client.open_by_key(sheet_identifier).sheet1
                    except gspread.exceptions.APIError:
                        sheet = self.client.open(sheet_identifier).sheet1
            except gspread.exceptions.SpreadsheetNotFound:
                email_msg = f" Ensure it is shared with: {self.service_email}" if hasattr(self, 'service_email') else ""
                return [], f"Spreadsheet '{sheet_identifier}' not found. Please check the Name/ID/URL.{email_msg}", stats
            except gspread.exceptions.APIError as e:
                return [], f"Google API Error: {e.response.text if hasattr(e, 'response') else str(e)}", stats

            # Get all values (to check Column B)
            rows = sheet.get_all_values()
            stats["total_rows"] = len(rows)
            
            new_items = []
            updates = [] # For batch updating duplicates
            
            # Iterate rows (1-based index for gspread)
            for idx, row in enumerate(rows):
                row_num = idx + 1
                
                # Get URL (Col A) and Status (Col B)
                url = row[0].strip() if len(row) > 0 else ""
                status = row[1].strip() if len(row) > 1 else ""
                
                # Skip invalid URLs (headers or empty)
                if not url.startswith("http"):
                    continue
                
                stats["valid_urls"] += 1
                
                # Logic:
                # 1. If Status is NOT empty, skip (already processed/failed/duplicate)
                if status:
                    continue
                
                # 2. If Status is empty, check if it's a known URL
                if url in existing_urls:
                    # Mark as Duplicate
                    stats["duplicates"] += 1
                    # We'll batch update these
                    updates.append({
                        'range': f'B{row_num}',
                        'values': [['Duplicate']]
                    })
                else:
                    # It's a new, unprocessed URL
                    new_items.append({'url': url, 'row': row_num})

            # Batch update duplicates if any
            if updates:
                try:
                    sheet.batch_update(updates)
                except Exception as e:
                    print(f"Failed to batch update duplicates: {e}")

            stats["new"] = len(new_items)
            return new_items, None, stats

        except Exception as e:
            return [], f"Error accessing sheet: {str(e)}", stats

    def update_status(self, sheet_identifier, row_num, status):
        """
        Updates Column B for a specific row with the given status.
        """
        if not self.client:
            return
        
        try:
            # Re-open sheet (or cache it if performance is an issue, but this is safer)
            if "docs.google.com" in sheet_identifier:
                 sheet = self.client.open_by_url(sheet_identifier).sheet1
            else:
                try:
                     sheet = self.client.open_by_key(sheet_identifier).sheet1
                except:
                    sheet = self.client.open(sheet_identifier).sheet1
            
            sheet.update_cell(row_num, 2, status)
        except Exception as e:
            print(f"Error updating status for row {row_num}: {e}")

    def get_urls(self, sheet_identifier):
        """
        Fetch all valid URLs from the first column of the sheet (Column A).
        Returns (urls, error) where urls is a list of strings.
        """
        if not self.client:
            return [], "Not authenticated"
        
        sheet_identifier = sheet_identifier.strip()
        
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
            email_msg = f" Ensure it is shared with: {self.service_email}" if hasattr(self, 'service_email') else ""
            return [], f"Spreadsheet '{sheet_identifier}' not found. Please check the Name/ID/URL.{email_msg}"
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
            
        sheet_identifier = sheet_identifier.strip()
        
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
                # Found it
            except gspread.exceptions.WorksheetNotFound:
                # Create it if it doesn't exist
                try:
                    ws = sh.add_worksheet(title="Tracklight_DB", rows=1000, cols=4)
                    ws.update('A1', [['ID', 'JSON_Data', 'Article_Title', 'TLDR']])
                except gspread.exceptions.APIError as e:
                    # If creation fails (e.g., permissions), we might be read-only
                    print(f"Failed to create DB sheet: {e}")
                    raise Exception(f"Failed to create 'Tracklight_DB' sheet. Ensure the service account has EDIT permissions. Error: {e}")
                    
            return ws
        except gspread.exceptions.SpreadsheetNotFound:
            email_msg = f" Ensure it is shared with: {self.service_email}" if hasattr(self, 'service_email') else ""
            raise Exception(f"Spreadsheet '{sheet_identifier}' not found. Please check the Name/ID/URL.{email_msg}")
        except Exception as e:
            print(f"Error getting DB sheet: {e}")
            raise e

    def load_db(self, sheet_identifier):
        """
        Loads all articles from Tracklight_DB worksheet (row-based).
        """
        return self.load_db_rows(sheet_identifier)

    def save_db(self, sheet_identifier, articles):
        """
        Saves all articles to Tracklight_DB worksheet (overwrite).
        Columns: ID, JSON_Data, Article_Title, TLDR
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
            # Added Title and TLDR columns
            ws.update('A1', [['ID', 'JSON_Data', 'Article_Title', 'TLDR']])
            
            rows = []
            for art in articles:
                # Prepare safe strings for Title and TLDR
                title = art.get('article_title', '') or ""
                tldr = art.get('tl_dr', '') or ""
                
                # Convert list to string for the cell
                if isinstance(tldr, list):
                    tldr = "\n".join(tldr)
                
                if len(tldr) > 4000: tldr = tldr[:4000] # Cell limit safety
                
                rows.append([
                    str(art.get('id', '')), 
                    json.dumps(art),
                    title,
                    tldr
                ])
            
            if rows:
                ws.update(f'A2:D{len(rows)+1}', rows)
                
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
