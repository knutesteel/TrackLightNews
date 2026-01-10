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
    def write_status(self, sheet_name, url, status):
        """
        Write a status back to the sheet next to the URL (optional feature).
        For now, just a placeholder if needed later.
        """
        pass
