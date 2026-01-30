import streamlit as st
import openai
import requests
from bs4 import BeautifulSoup
import json
import pandas as pd
from data_manager import DataManager
from sheet_manager import SheetManager
from email_manager import EmailManager
from utils import normalize_analysis
import os
from datetime import datetime
from dotenv import load_dotenv, set_key
from urllib.parse import quote_plus
import urllib.parse
import ast
import io
import streamlit.components.v1 as components

# Load environment variables
load_dotenv(override=True)

# Set page configuration
st.set_page_config(
    page_title="Tracklight.ai Article Analyzer",
    page_icon="🔍",
    layout="wide"
)

# --- Helper Functions ---
def analyze_global_summary(articles, api_key):
    if not articles: return "No articles to analyze."
    try:
        client = openai.OpenAI(api_key=api_key)
        
        # Prepare context with IDs
        context = "Here are the articles:\n\n"
        for a in articles[:100]: 
            title = a.get("article_title", "Unknown")
            summ = a.get("tl_dr", a.get("summary", ""))
            aid = a.get("id")
            if len(summ) > 400: summ = summ[:400] + "..."
            context += f"ID: {aid}\nTitle: {title}\nSummary: {summ}\n\n"
            
        prompt = """
        Analyze the article summaries and group them by commonality (e.g., fraud case, fraud scheme, program, specific people, etc).
        
        Return a strict JSON object with the following structure:
        {
            "groups": [
                {
                    "group_title": "Descriptive Group Name",
                    "article_ids": ["id_1", "id_2"]
                }
            ]
        }
        
        Rules:
        1. Ensure every article ID from the input is assigned to at least one group.
        2. Group titles should be specific and descriptive (e.g., "PPP Loan Fraud", "Medicare Schemes", "Crypto Scams").
        3. If an article fits multiple groups, choose the most relevant one.
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a senior fraud analyst that outputs JSON."},
                {"role": "user", "content": prompt + "\n\n" + context}
            ],
            temperature=0.5,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return f"Error: {str(e)}"

def scrape_article(url):
    try:
        uas = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0'
        ]
        headers_base = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1'
        }
        last_err = None
        resp = None
        
        # Progressive timeouts and retry logic
        for i, ua in enumerate(uas):
            try:
                headers = dict(headers_base)
                headers['User-Agent'] = ua
                resp = requests.get(url, headers=headers, timeout=10 + (i*5))
                if resp.status_code == 200:
                    break
            except Exception as e:
                last_err = e
                continue
        
        if not resp or resp.status_code != 200:
            return None, f"Failed to fetch: {last_err or resp.status_code if resp else 'Unknown error'}"
            
        soup = BeautifulSoup(resp.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
            
        # Get text
        text = soup.get_text(separator=' ', strip=True)
        
        # Basic title extraction
        title = soup.title.string if soup.title else ""
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)
                
        return {"title": title, "text": text}, None
    except Exception as e:
        return None, str(e)

@st.cache_data(show_spinner=False)
def get_person_overview(person, tl_dr, bullets_text, api_key):
    try:
        if not api_key or not person:
            return ""
        client = openai.OpenAI(api_key=api_key)
        context = f"TL;DR: {tl_dr}\nKey Points:\n{bullets_text}"
        q = f"From the context, write 1–2 tight sentences identifying {person}'s role category (e.g., reporter, official, suspect, victim, prosecutor, commentator) and their involvement. Do not imply guilt unless explicitly stated. If someone only reported or covered the story, state that clearly."
        msg = context + "\n\nQuestion:\n" + q
        r = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You write precise 1–2 sentence role summaries that avoid mislabeling and do not infer guilt."},
                {"role": "user", "content": msg}
            ],
            temperature=0.3
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return ""

@st.cache_data(show_spinner=False)
def generate_outreach_text(prompt, api_key):
    try:
        if not api_key:
            return "Error: No API Key provided."
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a professional communication assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"

# Initialize Managers
if "data_manager" not in st.session_state:
    st.session_state["data_manager"] = DataManager()
dm = st.session_state["data_manager"]

if "sheet_manager" not in st.session_state:
    st.session_state["sheet_manager"] = SheetManager()
sm = st.session_state["sheet_manager"]

def render_brand_header():
    # Check for local logo file
    logo_files = [f for f in os.listdir('.') if f.lower().startswith('logo.') and f.lower().endswith(('.png', '.jpg', '.jpeg', '.svg'))]
    
    if logo_files:
        # Display logo
        st.image(logo_files[0], width=200)
    else:
        # Fallback title
        st.title("Tracklight.ai Article Analyzer")

def get_config(key, default=""):
    """Get configuration from env vars or streamlit secrets"""
    # 1. Try environment variable (os.getenv)
    val = os.getenv(key)
    if val:
        return val
    
    # 2. Try Streamlit Secrets
    try:
        if key in st.secrets:
            return st.secrets[key]
    except FileNotFoundError:
        pass
    except Exception:
        pass
        
    return default

def mark_url_deleted(url):
    if not url:
        return
    url = url.strip()
    prefs = dm.get_preferences()
    deleted = prefs.get("deleted_urls", [])
    if url not in deleted:
        deleted.append(url)
        prefs["deleted_urls"] = deleted
        dm.save_preferences(prefs)

def normalize_url(url):
    if not url: return ""
    return url.strip().rstrip('/')

def maybe_auto_check_email(email_user, email_pass, api_key, force=False):
    try:
        if not email_user or not email_pass:
            if force:
                st.warning("Please enter Email User and Password in the sidebar settings.")
            return

        now_ts = datetime.now().timestamp()
        last_ts = float(st.session_state.get("last_email_check_ts", 0))
        
        # If not forced, enforce 1-minute cooldown
        if not force and now_ts - last_ts < 60:
            return
            
        st.session_state["last_email_check_ts"] = now_ts
        
        with st.spinner("Checking email for new articles..."):
            em = EmailManager(email_user, email_pass)
            prefs = dm.get_preferences()
            blocked = prefs.get("blocked_domains", [])
            deleted_urls = set(prefs.get("deleted_urls", []))
            
            links = em.fetch_new_links(blocked_domains=blocked)
            
            if isinstance(links, str) and links.startswith("Error"):
                err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] Email Error: {links}"
                st.session_state["last_activity_log"] = (st.session_state.get("last_activity_log", "") + err_msg + "\n")
                
                if force:
                    if "Application-specific password required" in links:
                        st.error("Authentication Failed: You must use a Google App Password, not your regular password.")
                        st.markdown("[Click here to generate an App Password](https://myaccount.google.com/apppasswords)")
                        st.info("Go to Google Account > Security > 2-Step Verification > App Passwords.")
                    else:
                        st.error(f"Email Check Failed: {links}")
                return

            if not links:
                if force:
                    st.info("No new valid links found in recent emails.")
                return

            articles = dm.get_all_articles()
            existing_urls = {normalize_url(a.get("url")) for a in articles if a.get("url")}
            
            # Normalize deleted URLs for comparison
            deleted_set = set()
            for u in deleted_urls:
                deleted_set.add(normalize_url(u))
            
            new_links = []
            for l in links:
                norm_l = normalize_url(l)
                if norm_l not in existing_urls and norm_l not in deleted_set:
                    new_links.append(l)
                    
            if new_links:
                added = []
                for l in new_links:
                    added.append({
                        "url": l,
                        "article_title": "New from Email",
                        "status": "Not Started",
                        "source": "email",
                        "added_at": datetime.now().isoformat()
                    })
                dm.save_articles(added)
                if force:
                    st.success(f"Added {len(added)} new articles from email!")
                else:
                    st.toast(f"Added {len(added)} new articles from email.")
            elif force:
                st.info("No new unique links found.")
                
    except Exception as e:
        if force:
            st.error(f"Error checking email: {e}")

def logs_page():
    st.title("Activity Logs")
    if st.button("Back to Dashboard"):
        st.session_state["show_logs"] = False
        st.rerun()
    
    log_text = st.session_state.get("last_activity_log", "No logs yet.")
    st.text_area("Logs", value=log_text, height=400)

# --- Sidebar ---
with st.sidebar:
    st.header("Settings")
    
    # API Key
    env_api_key = get_config("OPENAI_API_KEY", "")
    api_key = st.text_input("Enter OpenAI API Key", type="password", value=env_api_key)
    
    if st.button("Save API Key Permanently"):
        if api_key:
            env_file = ".env"
            if not os.path.exists(env_file):
                with open(env_file, 'w') as f:
                    pass
            set_key(env_file, "OPENAI_API_KEY", api_key)
            st.success("API Key saved to .env file!")
            st.rerun()
        else:
            st.error("Please enter a key to save.")

    if not api_key:
        st.warning("Please enter your OpenAI API Key to proceed.")
        
    st.markdown("---")

    # Email Integration
    st.subheader("Email Integration")
    st.caption("Auto-load URLs from Gmail.")
    
    env_email_user = get_config("EMAIL_USER", "articleanalyzer@gmail.com")
    env_email_pass = get_config("EMAIL_PASS", "")
    
    email_user = st.text_input("Gmail Address", value=env_email_user).strip()
    email_pass_input = st.text_input("App Password", type="password", value=env_email_pass, help="Generate an App Password in your Google Account settings.")
    email_pass = email_pass_input.replace(" ", "").strip()
    st.caption("[Get App Password](https://myaccount.google.com/apppasswords) (Required for 2FA)")
    
    if st.button("Save Email Config"):
        set_key(".env", "EMAIL_USER", email_user)
        set_key(".env", "EMAIL_PASS", email_pass)
        st.success("Email settings saved!")
        st.rerun()

    try:
        from streamlit import st_autorefresh
        st_autorefresh(interval=60000, key="email_poll")
    except Exception:
        pass

    st.markdown("---")
    
    # Google Sheets Integration
    st.subheader("Google Sheets Integration")
    st.caption("Auto-load URLs from a Google Sheet.")
    
    sheet_name = st.text_input("Google Sheet Name / ID / URL", value=get_config("GOOGLE_SHEET_NAME", ""), help="You can enter the exact Name, the Sheet ID, or the full URL.")
    
    saved_creds_file = "google_creds.json"
    has_saved_creds = os.path.exists(saved_creds_file)
    
    if has_saved_creds:
        st.success("✅ Credentials Loaded")
        try:
            with open(saved_creds_file, 'r') as f:
                saved_c = json.load(f)
                saved_email = saved_c.get("client_email")
                if saved_email:
                    st.markdown(f"**Service Email:**")
                    st.code(saved_email, language="text")
                    st.caption("Share your sheet with this email.")
                else:
                    st.error("File found but missing 'client_email'.")
        except Exception as e:
            st.error(f"Error reading credentials: {e}")
            
        # Advanced: Force Push
        with st.expander("Advanced: Data Recovery"):
            st.warning("Use this if your Google Sheet is empty but you have local data you want to upload.")
            if st.button("Force Push Local Data to Remote"):
                try:
                    # Authenticate if needed
                    if not dm.sm or not sm.client:
                        with open(saved_creds_file, 'r') as f:
                            creds = json.load(f)
                        sm.authenticate(creds)
                    
                    # Force load local data to be sure
                    local_data = dm._load_from_local()
                    if local_data:
                        success, msg = sm.save_db(sheet_name, local_data)
                        if success:
                            st.success(f"Successfully pushed {len(local_data)} articles to remote Sheet!")
                            # Update cache
                            dm.articles_cache = local_data
                        else:
                            st.error(f"Push failed: {msg}")
                    else:
                        st.warning("No local data found to push.")
                except Exception as e:
                    st.error(f"Error: {e}")

    else:
        st.warning("⚠️ No Service Account Linked")
        st.markdown("""
        **How to connect:**
        1. Paste your **Service Account JSON** below.
        2. The **Service Email** will appear.
        3. Share your Google Sheet with that email.
        4. Click **Save Google Config**.
        """)

    creds_val = ""
    if has_saved_creds:
        try:
            with open(saved_creds_file, 'r') as f:
                creds_val = f.read()
        except: pass
        
    creds_input = st.text_area("Service Account JSON", value=creds_val, placeholder="{ ... }", help="Paste the content of your service_account.json here.")
    
    # Immediate validation (Preview)
    if creds_input and creds_input != creds_val:
        st.markdown("---")
        st.caption("Preview of pasted credentials:")
        try:
            c = json.loads(creds_input)
            preview_email = c.get("client_email")
            if preview_email:
                st.markdown(f"**Found Email:** `{preview_email}`")
            else:
                st.warning("JSON parsed, but 'client_email' not found.")
        except json.JSONDecodeError:
            if len(creds_input) > 10:
                st.warning("Invalid JSON format.")
    
    if st.button("Save Google Config"):
        if sheet_name:
            set_key(".env", "GOOGLE_SHEET_NAME", sheet_name)
        
        if creds_input:
            try:
                c = json.loads(creds_input)
                with open(saved_creds_file, 'w') as f:
                    json.dump(c, f)
                st.success("Configuration Saved! Reloading...")
                st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON: {e}")
        else:
            st.success("Sheet Name Saved!")
            st.rerun()

    st.markdown("---")
    st.subheader("Tools")
    
    with st.expander("🗑️ Bulk Delete / Cleanup"):
        st.caption("Permanently remove articles from the database.")
        
        # 1. Purge Soft-Deleted
        all_articles = dm.get_all_articles()
        deleted_count = len([a for a in all_articles if a.get("status") == "Deleted"])
        
        st.markdown(f"**Soft-Deleted Articles:** {deleted_count}")
        if deleted_count > 0:
            if st.button("Purge All Soft-Deleted"):
                with st.spinner("Purging..."):
                    to_purge = [a["id"] for a in all_articles if a.get("status") == "Deleted"]
                    for aid in to_purge:
                        dm.purge_article(aid)
                    st.success(f"Purged {len(to_purge)} articles!")
                    st.rerun()
        
        st.divider()
        
        # 2. Delete Active
        st.markdown("**Delete Active Articles**")
        active_candidates = dm.get_active_articles()
        
        if not active_candidates:
            st.info("No active articles.")
        else:
            with st.form("sidebar_bulk_delete"):
                opts = {f"{a.get('article_title')}": a.get('id') for a in active_candidates}
                sel = st.multiselect("Select to Delete", options=list(opts.keys()))
                permanent = st.checkbox("Permanent (Prevent Re-sync)", value=True, help="If checked, the URL will be ignored in future syncs.")
                
                if st.form_submit_button("Delete"):
                    for title in sel:
                        aid = opts[title]
                        if permanent:
                            dm.purge_article(aid)
                        else:
                            dm.delete_article(aid, hard=False)
                    st.success(f"Deleted {len(sel)} articles.")
                    st.rerun()
    
    st.markdown("---")
    st.markdown("### About Tracklight.ai")
    st.markdown("Prevent fraud before it happens.")
    
    prefs = dm.get_preferences()
    current_fs = int(st.session_state.get("font_size", prefs.get("font_size", 18)))
    new_fs = st.number_input("Body Font Size (px)", min_value=12, max_value=36, value=current_fs, step=1)
    if int(new_fs) != current_fs:
        st.session_state["font_size"] = int(new_fs)
        dm.save_preferences({"font_size": int(new_fs)})
        st.rerun()

# Apply Styles
font_size = int(st.session_state.get("font_size", dm.get_preferences().get("font_size", 18)))
h1 = int(font_size * 2.0)
h2 = int(font_size * 1.7)
h3 = int(font_size * 1.4)
st.markdown(
    f"""
    <style>
    html, body, [class*="css"]  {{
        font-size: {font_size}px !important;
    }}
    .stTextInput input, .stSelectbox, .stTextArea textarea {{
        font-size: {font_size}px !important;
    }}
    .stMarkdown p {{
        font-size: {font_size}px !important;
    }}
    h1 {{ font-size: {h1}px !important; }}
    h2 {{ font-size: {h2}px !important; }}
    h3 {{ font-size: {h3}px !important; }}
    
    /* Force wrap in dataframe cells */
    .stTable td {{
        white-space: normal !important;
        overflow-wrap: break-word !important;
    }}

    /* Sticky Header for Details Page */
    div[data-testid="stVerticalBlock"] > div:has(#sticky-marker) {{
        position: sticky;
        top: 2.875rem; 
        z-index: 999;
        background-color: white;
        padding: 10px 0;
        border-bottom: 1px solid #ddd;
    }}
    </style>
    """,
    unsafe_allow_html=True
)

render_brand_header()

if st.session_state.get("show_logs"):
    logs_page()
elif st.session_state.get("is_global_summary"):
    # ... Global Summary logic ...
    st.title("Global Analysis")
    if st.button("Back to Dashboard"):
        st.session_state["is_global_summary"] = False
        st.rerun()
    
    # Analyze all active articles
    active = dm.get_active_articles()
    if st.button("Run Analysis"):
        with st.spinner("Analyzing..."):
            res = analyze_global_summary(active, api_key)
            st.json(res)
else:
    # --- Dashboard ---
    
    # Restore Connection Logic
    is_sheet_connected = False
    if has_saved_creds and sheet_name:
        try:
            # Check if we need to authenticate or re-connect
            if not dm.sm or not sm.client:
                with open(saved_creds_file, 'r') as f:
                    creds = json.load(f)
                success, msg = sm.authenticate(creds)
                if success:
                    # Try to set backend immediately
                    try:
                        dm.set_backend(sm, sheet_name)
                        is_sheet_connected = True
                    except Exception as e:
                        st.sidebar.error(f"DB Error: {e}")
                else:
                    st.sidebar.error(f"Sheet Auth Error: {msg}")
            else:
                 is_sheet_connected = True
        except Exception as e:
            st.sidebar.error(f"Credentials Error: {e}")

    col_title, col_sync = st.columns([2.5, 2])
    with col_sync:
        if is_sheet_connected:
            # Display current sheet name/URL for confirmation
            st.caption(f"Syncing with: {sheet_name[:30]}...")
            if st.button("🔄 Sync with Google Sheet", help="Pull new URLs and refresh data"):
                with st.spinner("Syncing..."):
                    # Reload DB to get latest changes
                    dm.set_backend(sm, sheet_name)
                    
                    articles = dm.get_all_articles()
                    existing_urls = {normalize_url(a.get("url")) for a in articles if a.get("url")}
                    
                    # Also exclude permanently deleted URLs
                    prefs = dm.get_preferences()
                    deleted_prefs = set(prefs.get("deleted_urls", []))
                    for d_url in deleted_prefs:
                        existing_urls.add(normalize_url(d_url))
                    
                    # Fetch new from Sheet
                    new_urls, err, stats = sm.get_new_urls(sheet_name, existing_urls)
                    
                    if err:
                        st.error(err)
                    else:
                        msg = f"Found {stats['total_rows']} rows. {stats['valid_urls']} valid URLs. {stats['duplicates']} duplicates."
                        if stats['total_rows'] == 0:
                            st.warning(f"{msg} Is the sheet empty or data not in Column A?")
                        elif stats['new'] > 0:
                            st.success(f"{msg} Found {stats['new']} NEW.")
                        else:
                            st.info(f"{msg} No new URLs.")
                            
                        if new_urls:
                            articles_to_add = []
                            count = 0
                            for url in new_urls:
                                if not url: continue
                                if normalize_url(url) not in existing_urls:
                                    articles_to_add.append({
                                        "url": url,
                                        "article_title": "New from Sheet",
                                        "status": "Not Started",
                                        "source": "sheet",
                                        "added_at": datetime.now().isoformat()
                                    })
                                    count += 1
                            
                            if articles_to_add:
                                dm.save_articles(articles_to_add)
                                st.success(f"Added {count} new articles!")
                                st.rerun()
        else:
            st.warning("⚠️ Sync Unavailable: Not connected to Google Sheet")
            
            with st.expander("🔌 Connection Troubleshooter"):
                st.write("To sync, you need to connect your Google Sheet.")
                if has_saved_creds:
                    st.success("✅ Credentials File Found")
                    try:
                        with open(saved_creds_file, 'r') as f:
                            c = json.load(f)
                            e = c.get("client_email")
                            if e:
                                st.markdown(f"**Service Email:** `{e}`")
                                st.info("Ensure the Google Sheet is shared with this email.")
                    except: pass
                else:
                    st.error("❌ No Credentials File Found")
                
                if not sheet_name:
                    st.error("❌ No Sheet Name/URL configured")

    # --- Main Content ---
    
    tab_list, tab_details = st.tabs(["📋 Dashboard", "📄 Article Details"])
    
    active_articles = dm.get_active_articles()

    with tab_list:
        st.subheader(f"Active Articles ({len(active_articles)})")
        
        # Search
        search = st.text_input("Search articles...", placeholder="Title, Summary, or URL")
        
        filtered = active_articles
        if search:
            s = search.lower()
            filtered = [a for a in active_articles if s in a.get("article_title", "").lower() or s in a.get("url", "").lower()]
            
        # Display as data_editor
        if filtered:
            data_for_df = []
            for a in filtered:
                # Map status for consistency
                s = a.get("status", "Not Started")
                if s == "In Progress": s = "In Process"
                if s == "Done": s = "Complete"
                
                # Format TL;DR
                tldr = a.get("tl_dr", "")
                if isinstance(tldr, list):
                    tldr = "; ".join(tldr)
                
                data_for_df.append({
                    "id": a.get("id"),
                    "Status": s,
                    "Title": a.get("article_title", "Untitled"),
                    "TL;DR": str(tldr)[:300], # Truncate for display
                    "full_obj": a # Keep reference if needed, but dicts in DF are tricky
                })
            
            df = pd.DataFrame(data_for_df)
            
            # Editor Config
            editor_key = "article_editor"
            
            edited_df = st.data_editor(
                df,
                key=editor_key,
                column_config={
                    "id": None, # Hidden
                    "full_obj": None, # Hidden
                    "Status": st.column_config.SelectboxColumn(
                        "Status",
                        options=["Not Started", "In Process", "Complete"],
                        required=True,
                        width="medium"
                    ),
                    "Title": st.column_config.TextColumn(
                        "Title",
                        width="large",
                        help="Select row to view details"
                    ),
                    "TL;DR": st.column_config.TextColumn(
                        "TL;DR",
                        width="large"
                    )
                },
                hide_index=True,
                use_container_width=True,
                disabled=["Title", "TL;DR"],
                selection_mode="single-row",
                on_select="rerun"
            )
            
            # Handle Status Changes
            # Compare edited_df with original data to find changes
            # We can iterate and check against dm.get_article or the filtered list
            for index, row in edited_df.iterrows():
                art_id = row["id"]
                new_status = row["Status"]
                
                # Find original in filtered list
                orig = next((a for a in filtered if a["id"] == art_id), None)
                if orig:
                    orig_s = orig.get("status", "Not Started")
                    if orig_s == "In Progress": orig_s = "In Process"
                    if orig_s == "Done": orig_s = "Complete"
                    
                    if new_status != orig_s:
                        dm.update_article(art_id, {"status": new_status})
                        st.toast(f"Updated status for '{row['Title']}'")
            
            # Handle Selection for Navigation
            if editor_key in st.session_state and st.session_state[editor_key].get("selection", {}).get("rows"):
                sel_idx = st.session_state[editor_key]["selection"]["rows"][0]
                if sel_idx < len(df):
                    selected_id = df.iloc[sel_idx]["id"]
                    st.session_state["selected_article_id"] = selected_id
                    st.info(f"Selected '{df.iloc[sel_idx]['Title']}'. Switch to 'Article Details' tab to view.")

        else:
            st.info("No articles found.")
            
    with tab_details:
        if not active_articles:
            st.info("No articles to select.")
        else:
            # Selector logic - prioritize session state selection
            opts = {f"{a.get('article_title')}": a.get("id") for a in active_articles}
            opt_titles = list(opts.keys())
            
            # Determine default index
            default_idx = 0
            if "selected_article_id" in st.session_state:
                # Find title for this ID
                target_id = st.session_state["selected_article_id"]
                for i, title in enumerate(opt_titles):
                    if opts[title] == target_id:
                        default_idx = i
                        break
            
            sel_title = st.selectbox("Select Article", options=opt_titles, index=default_idx)
            sel_id = opts[sel_title]
            
            # Update selection state if changed via dropdown
            if sel_id != st.session_state.get("selected_article_id"):
                st.session_state["selected_article_id"] = sel_id

            article = next((a for a in active_articles if a["id"] == sel_id), None)
            
            if article:
                # Header with Fraud Indicator
                fi = article.get("fraud_indicator")
                if fi:
                    fi_color = "red" if fi == "High" else "orange" if fi == "Medium" else "green"
                    st.markdown(f"## {article.get('article_title')} <span style='color:{fi_color}; font-size:0.5em; border:1px solid {fi_color}; padding:2px 5px; border-radius:5px; vertical-align: middle;'>{fi.upper()}</span>", unsafe_allow_html=True)
                else:
                    st.header(article.get("article_title"))
                st.caption(f"URL: {article.get('url')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    current_status = article.get("status", "Not Started")
                    # Normalize for dropdown
                    if current_status == "In Progress": current_status = "In Process"
                    if current_status == "Done": current_status = "Complete"
                    
                    status_opts = ["Not Started", "In Process", "Complete", "Deleted"]
                    try:
                        idx = status_opts.index(current_status)
                    except ValueError:
                        idx = 0
                        
                    new_status = st.selectbox("Status", status_opts, index=idx)
                    if new_status != current_status:
                        dm.update_article(sel_id, {"status": new_status})
                        st.rerun()
                
                with col2:
                    if st.button("🗑️ Delete Article"):
                        dm.update_article(sel_id, {"status": "Deleted"})
                        st.rerun()

                # Analysis Section
                st.markdown("### Analysis")
                if st.button("🔍 Analyze Article"):
                    if not api_key:
                        st.error("API Key required.")
                    else:
                        with st.spinner("Scraping and Analyzing..."):
                            # Scrape
                            content, err = scrape_article(article.get("url"))
                            if err:
                                st.error(err)
                            else:
                                # Update article with content
                                dm.update_article(sel_id, {"scraped_text": content["text"], "article_title": content["title"]})
                                
                                # Analyze
                                prompt = f"""
                                Analyze this article for fraud prevention insights.
                                Title: {content['title']}
                                Text: {content['text'][:15000]}
                                
                                Return a JSON object with:
                                - tl_dr (list of strings): 5 to 10 of the most relevant points.
                                - summary (string): Narrative summary, up to 5 paragraphs.
                                - organizations_involved (list of objects): Each with 'name' and 'role_summary'. Identify all companies and government organizations.
                                - allegations (string): Details on what types of fraud occurred or allegedly occurred.
                                - current_situation (string): Have there been arrests, convictions, plea bargains, or other resolutions?
                                - next_steps (string): Information on what happens next.
                                - prevention_strategies (list of objects): Each with 'issue' and 'prevention'.
                                - fraud_indicator (High/Medium/Low): Overall risk level.
                                - discovery_questions (list of strings): 3-5 questions to ask clients.
                                - date (YYYY-MM-DD if found)
                                """
                                
                                try:
                                    client = openai.OpenAI(api_key=api_key)
                                    response = client.chat.completions.create(
                                        model="gpt-4o",
                                        messages=[
                                            {"role": "system", "content": "You are a fraud analyst. Output JSON."},
                                            {"role": "user", "content": prompt}
                                        ],
                                        response_format={"type": "json_object"}
                                    )
                                    analysis = json.loads(response.choices[0].message.content)
                                    analysis = normalize_analysis(analysis)
                                    
                                    # Merge into article
                                    dm.update_article(sel_id, {
                                        **analysis
                                    })
                                    st.success("Analysis complete!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Analysis failed: {e}")

                # Display Analysis Results
                if article.get("tl_dr") or article.get("summary"):
                    st.markdown("---")
                    
                    # Header with Fraud Indicator
                    fi = article.get("fraud_indicator", "Unknown")
                    fi_color = "red" if fi == "High" else "orange" if fi == "Medium" else "green"
                    st.markdown(f"### Analysis Result <span style='color:{fi_color}; font-size:0.8em; border:1px solid {fi_color}; padding:2px 5px; border-radius:5px;'>{fi.upper()}</span>", unsafe_allow_html=True)
                    
                    st.subheader("TL;DR")
                    tldr = article.get("tl_dr")
                    if isinstance(tldr, list):
                        for point in tldr:
                            st.markdown(f"- {point}")
                    else:
                        st.write(tldr)

                    st.subheader("Summary")
                    st.write(article.get("summary", ""))

                    st.subheader("Organizations Involved")
                    orgs = article.get("organizations_involved", [])
                    if orgs:
                        for org in orgs:
                            if isinstance(org, dict):
                                name = org.get("name", "Unknown")
                                role = org.get("role_summary", org.get("role", ""))
                                st.markdown(f"**{name}**: {role}")
                            elif isinstance(org, str):
                                st.markdown(f"- {org}")

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.subheader("Allegations")
                        st.write(article.get("allegations", "N/A"))
                    with col_b:
                        st.subheader("Current Situation")
                        st.write(article.get("current_situation", "N/A"))

                    st.subheader("Next Steps")
                    st.write(article.get("next_steps", "N/A"))

                    st.subheader("Tracklight Prevention")
                    prevs = article.get("prevention_strategies", [])
                    if prevs:
                        for p in prevs:
                            if isinstance(p, dict):
                                issue = p.get("issue", "")
                                prev = p.get("prevention", "")
                                st.markdown(f"**Issue:** {issue} — **Tracklight Solution:** {prev}")
                            else:
                                st.write(p)

                    st.markdown("---")
                    st.subheader("K. Chat with Article")
                    
                    # Chat Logic
                    if "chat_history" not in article:
                         article["chat_history"] = []
                    
                    # Display history
                    if article["chat_history"]:
                        chat_data = []
                        for chat in article["chat_history"]:
                            chat_data.append({"User Question": chat["q"], "AI Response": chat["a"]})
                        st.table(pd.DataFrame(chat_data))
                    
                    # Input
                    with st.form("chat_form"):
                        user_q = st.text_input("Ask a question about this article:")
                        submitted = st.form_submit_button("Ask")
                        if submitted and user_q:
                            # Call OpenAI
                            with st.spinner("Thinking..."):
                                try:
                                    client = openai.OpenAI(api_key=api_key)
                                    # Context
                                    ctx = f"Article Title: {article.get('article_title')}\n\nSummary: {article.get('summary')}\n\nFull Text (snippet): {article.get('scraped_text', '')[:10000]}"
                                    resp = client.chat.completions.create(
                                        model="gpt-4o",
                                        messages=[
                                            {"role": "system", "content": "You are a helpful assistant answering questions about the provided article."},
                                            {"role": "user", "content": f"{ctx}\n\nQuestion: {user_q}"}
                                        ]
                                    )
                                    ans = resp.choices[0].message.content
                                    
                                    # Save to history
                                    new_history = article.get("chat_history", [])
                                    new_history.append({"q": user_q, "a": ans})
                                    dm.update_article(sel_id, {"chat_history": new_history})
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
