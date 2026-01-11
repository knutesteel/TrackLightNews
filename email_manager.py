import imaplib
import email
from email.header import decode_header
import re
from bs4 import BeautifulSoup
import urllib.parse

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailManager:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.imap_server = "imap.gmail.com"
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 465
        self.last_scanned_count = 0

    def send_email(self, to_email, subject, body_html):
        try:
            msg = MIMEMultipart()
            msg["From"] = self.username
            msg["To"] = to_email
            msg["Subject"] = subject
            
            msg.attach(MIMEText(body_html, "html"))
            
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.username, self.password)
                server.sendmail(self.username, to_email, msg.as_string())
            return True, "Email sent successfully!"
        except Exception as e:
            return False, f"Failed to send email: {str(e)}"

    def fetch_new_links(self, blocked_domains=None):
        links = set()
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.username, self.password)
            # Force INBOX only per user environment
            if not self._safe_select(mail, "INBOX"):
                mail.close()
                mail.logout()
                return []
            # Try UNSEEN first
            status, messages = mail.search(None, "(UNSEEN)")
            email_ids = []
            if status == "OK":
                email_ids = messages[0].split()
            # Fallback: scan last 50 messages if UNSEEN empty
            if not email_ids:
                status, messages = mail.search(None, "ALL")
                if status == "OK":
                    all_ids = messages[0].split()
                    email_ids = all_ids[-50:] if len(all_ids) > 50 else all_ids
            self.last_scanned_count = len(email_ids)
            for email_id in email_ids:
                res, msg = mail.fetch(email_id, "(RFC822)")
                for response in msg:
                    if isinstance(response, tuple):
                        msg_obj = email.message_from_bytes(response[1])
                        content = ""
                        if msg_obj.is_multipart():
                            for part in msg_obj.walk():
                                content_type = part.get_content_type()
                                content_disposition = str(part.get("Content-Disposition"))
                                try:
                                    body = part.get_payload(decode=True)
                                    if body:
                                        body = body.decode(errors='ignore')
                                    else:
                                        continue
                                except:
                                    continue
                                if content_type == "text/plain" and "attachment" not in content_disposition:
                                    content += body
                                elif content_type == "text/html" and "attachment" not in content_disposition:
                                    content += body
                        else:
                            try:
                                body = msg_obj.get_payload(decode=True)
                                if body:
                                    body = body.decode(errors='ignore')
                                    content += body
                            except:
                                pass
                        found = self._extract_links(content, blocked_domains)
                        links.update(found)
            mail.close()
            mail.logout()
        except Exception as e:
            return f"Error: {str(e)}"
        
        return list(links)

    def _discover_mailboxes(self, mail):
        try:
            status, boxes = mail.list()
            names = []
            if status == "OK" and boxes:
                for b in boxes:
                    try:
                        s = b.decode()
                    except Exception:
                        s = str(b)
                    # Typical format: '(\\HasNoChildren) "/" "[Gmail]/All Mail"'
                    parts = s.split(' "/" ')
                    name = parts[-1].strip()
                    if name.startswith('"') and name.endswith('"'):
                        name = name[1:-1]
                    # Normalize encoding artifacts
                    names.append(name)
            return names
        except Exception:
            return ["INBOX"]

    def _safe_select(self, mail, mailbox_name):
        try:
            status, _ = mail.select(mailbox_name)
            if status == "OK":
                return True
        except Exception:
            pass
        try:
            quoted = f'"{mailbox_name}"'
            status, _ = mail.select(quoted)
            return status == "OK"
        except Exception:
            return False

    def _extract_links(self, text, blocked_domains=None):
        found_links = set()
        
        # Regex for URLs
        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        
        # If text looks like HTML, parse it
        if "<html" in text.lower() or "<body" in text.lower() or "<a href" in text.lower():
            soup = BeautifulSoup(text, "html.parser")
            for a in soup.find_all('a', href=True):
                href = a['href']
                unwrapped = self._unwrap_url(href)
                if self._is_valid_article_link(unwrapped, blocked_domains):
                    found_links.add(unwrapped)
        
        # Also run regex on the raw text to catch plain text links (and in case HTML parsing missed some)
        # Note: running regex on HTML might catch src attributes, etc. but _is_valid filters some.
        # Safer to rely on BS4 for HTML, but text/plain parts need regex.
        # We'll just add everything valid.
        raw_matches = url_pattern.findall(text)
        for url in raw_matches:
             # Remove trailing chars that might be part of sentence
             url = url.rstrip(').,;\'"')
             unwrapped = self._unwrap_url(url)
             if self._is_valid_article_link(unwrapped, blocked_domains):
                 found_links.add(unwrapped)
                 
        return found_links

    def _unwrap_url(self, url):
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.lower()
            qs = urllib.parse.parse_qs(parsed.query)
            # Google redirector (e.g., https://www.google.com/url?q=...)
            if ("google." in domain) and (path == "/url"):
                dest = qs.get("q", [None])[0]
                if dest:
                    return dest
            # Common tracking redirectors that carry a 'url' or 'u' or 'redirect' param
            for key in ("url", "u", "redirect", "r", "target"):
                if key in qs and qs[key]:
                    dest = qs[key][0]
                    if dest:
                        return dest
            return url
        except Exception:
            return url

    def _is_valid_article_link(self, url, blocked_domains=None):
        # Filter out common non-article links
        invalid_domains = [
            "mail.google.com", "calendar.google.com", "bing.com",
            "facebook.com", "twitter.com", "linkedin.com", "instagram.com",
            "calendly.com", "zoom.us", "teams.microsoft.com", "webex.com",
            "accounts.google", "support.google", "youtube.com", "vimeo.com",
            "apollo.io", "click.apollo.io", "track.apollo.io",
            "outlook.office.com", "w3.org", "bookwithme", "yutori.com",
            "resend-links.com", "chromewebstore.google.com",
            "myaccount.google.com", "lh3.googleusercontent.com",
            "unsubscribe", "preferences", "manage"
        ]
        
        if blocked_domains:
            invalid_domains.extend(blocked_domains)
        
        # Clean URL
        url = url.strip()
        
        if not url.startswith("http"):
            return False
            
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
        except:
            return False
        
        # Exclude mailto links and non-http
        if url.lower().startswith("mailto:"):
            return False
        
        for inv in invalid_domains:
            if inv in domain:
                return False
        
        # Check for unsubscribe keywords in the URL path/query
        lower_url = url.lower()
        if "unsubscribe" in lower_url or "optout" in lower_url or "manage-preferences" in lower_url or "preferences" in lower_url or "meetingtype" in lower_url:
            return False
            
        # Basic extension check
        if lower_url.endswith(('.png', '.jpg', '.jpeg', '.gif', '.css', '.js', '.ico', '.svg', '.woff', '.ttf')):
            return False
            
        return True
