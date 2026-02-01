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

def analyze_content(text, title, api_key):
    """
    Analyzes the provided text using GPT-4o and returns the structured JSON analysis.
    """
    if not api_key:
        raise ValueError("API Key is missing.")
        
    prompt = f"""
    Analyze this article for fraud prevention insights.
    Title: {title}
    Text: {text[:15000]}
    
    Return a JSON object with:
    - article_title (string): A concise, descriptive headline for this article.
    - tl_dr (list of strings): 5 to 10 of the most relevant points.
    - summary (string): Narrative summary, up to 5 paragraphs.
    - organizations_involved (list of objects): Each with 'name', 'role_summary', and 'people' (list of objects, each with 'name' and 'role'). Identify all companies and government organizations and the key people associated with them.
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
        return normalize_analysis(analysis)
    except Exception as e:
        raise Exception(f"Analysis failed: {e}")

def analyze_person(name, context_text, api_key):
    """
    Analyzes a specific person mentioned in the text using GPT-4o.
    """
    if not api_key:
        return "Error: No API Key provided."
        
    prompt = f"""
    Analyze the following text to find details about "{name}".
    
    Text Context:
    {context_text[:10000]}
    
    Provide a brief summary of who they are, their role in the events described, and any background information mentioned. 
    Focus on facts explicitly stated in the text or generally known public information relevant to this context.
    Output as a concise paragraph.
    """
    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a research analyst."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
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
    
    sheet_name = st.text_input("Google Sheet Name / ID / URL", value=get_config("GOOGLE_SHEET_NAME", ""), key="sheet_url_input_v2", help="You can enter the exact Name, the Sheet ID, or the full URL.")
    
    # Credentials Logic: Secrets -> File -> Input
    creds_source = None
    creds_dict = None
    
    # 1. Try Streamlit Secrets
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds_source = "secrets_dict"
        elif "service_account_json" in st.secrets:
            # Handle potential JSON formatting issues in secrets
            secret_val = st.secrets["service_account_json"]
            try:
                creds_dict = json.loads(secret_val)
            except json.JSONDecodeError:
                # Try to fix common issues (like single quotes) if it's a python dict string
                try:
                    creds_dict = ast.literal_eval(secret_val)
                except:
                    raise ValueError("Invalid JSON format in 'service_account_json' secret. Ensure property names are in double quotes.")
            creds_source = "secrets_json"
    except Exception as e:
        st.error(f"Error loading secrets: {e}")

    # 2. Try Local File
    saved_creds_file = "google_creds.json"
    if not creds_dict and os.path.exists(saved_creds_file):
        try:
            with open(saved_creds_file, 'r') as f:
                creds_dict = json.load(f)
            creds_source = "local_file"
        except Exception:
            pass

    if creds_dict:
        if creds_source.startswith("secrets"):
            st.success("✅ Credentials Loaded from Streamlit Secrets")
        else:
            st.success("✅ Credentials Loaded from Local File")
            
        saved_email = creds_dict.get("client_email")
        if saved_email:
            st.markdown(f"**Service Email:**")
            st.code(saved_email, language="text")
            st.caption("Share your sheet with this email.")
        else:
            st.warning("Credentials loaded, but 'client_email' field is missing.")
            st.json(creds_dict) # Debug help
        
        # Authenticate Session if needed
        if not dm.sm or not sm.client:
            success, msg = sm.authenticate(creds_dict)
            if not success:
                 st.error(f"Auth Error: {msg}")

        # Advanced: Force Push
        with st.expander("Advanced: Data Recovery"):
            st.warning("Use this if your Google Sheet is empty but you have local data you want to upload.")
            if st.button("Force Push Local Data to Remote"):
                try:
                    # Authenticate if needed
                    if not dm.sm or not sm.client:
                        sm.authenticate(creds_dict)
                    
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
        2. Click **Save Google Config**.
        
        **☁️ Hosting on Streamlit Cloud?**
        Files are deleted when the app restarts. To save permanently:
        1. Go to your App Dashboard > Settings > Secrets.
        2. Paste your JSON like this:
        ```toml
        service_account_json = \"\"\"
        {
          "type": "service_account",
          ... your json here ...
        }
        \"\"\"
        ```
        """)

    # Input Area (Show existing value if from file, else empty)
    input_val = ""
    if creds_source == "local_file":
        try:
            with open(saved_creds_file, 'r') as f:
                input_val = f.read()
        except: pass
        
    creds_input = st.text_area("Service Account JSON", value=input_val, placeholder="{ ... }", help="Paste the content of your service_account.json here.")
    
    # Immediate validation (Preview)
    if creds_input and creds_input != input_val:
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
            # Try to help the user if they pasted Python dict syntax
            try:
                c = ast.literal_eval(creds_input)
                if isinstance(c, dict):
                    st.info("💡 Detected Python dictionary syntax (single quotes). We will convert this to valid JSON for you on save.")
                    preview_email = c.get("client_email")
                    if preview_email:
                        st.markdown(f"**Found Email:** `{preview_email}`")
                else:
                    st.warning("Invalid JSON format. Expecting double quotes around property names.")
            except:
                if len(creds_input) > 10:
                    st.warning("Invalid JSON format. Expecting double quotes around property names.")
    
    if st.button("Save Google Config"):
        if sheet_name:
            set_key(".env", "GOOGLE_SHEET_NAME", sheet_name)
        
        if creds_input:
            try:
                # Try JSON load first
                try:
                    c = json.loads(creds_input)
                except json.JSONDecodeError:
                    # Fallback to ast.literal_eval for single-quote dicts
                    c = ast.literal_eval(creds_input)
                
                # Save to file
                with open(saved_creds_file, 'w') as f:
                    json.dump(c, f)
                st.success("Configuration Saved! (Note: On Cloud, this resets on reboot. Use Secrets for permanence.)")
                
                # Also try to authenticate immediately to update session
                sm.authenticate(c)
                
                import time
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON: {e}")
        else:
            st.success("Sheet Name Saved!")
            st.rerun()

    st.markdown("---")
    st.subheader("Tools")
    

    
    # Debug Control
    show_debug = st.checkbox("Show Debug Info", value=st.session_state.get("show_debug", False), key="debug_checkbox_sidebar")
    st.session_state["show_debug"] = show_debug

    # --- Hidden Admin Reset ---
    # Access via ?admin_reset=true in URL
    if st.query_params.get("admin_reset") == "true":
        st.divider()
        st.error("⚠️ ADMIN ZONE")
        if st.button("HARD RESET DATABASE", type="primary"):
            dm.clear_all_articles()
            st.success("Database Wiped from Cloud & Remote.")
            st.rerun()
    # --------------------------

    if show_debug:
        st.divider()
        st.subheader("🛠️ Debug Info")
        try:
            info = dm.get_storage_info()
            st.json(info)
            
            # Show Service Email in Debug too
            if hasattr(sm, 'service_email') and sm.service_email:
                st.write(f"**Service Email:** `{sm.service_email}`")
            elif os.path.exists(saved_creds_file):
                 try:
                    with open(saved_creds_file, 'r') as f:
                        saved_c = json.load(f)
                        st.write(f"**Service Email (from file):** `{saved_c.get('client_email')}`")
                 except: pass

            # Safe fetch of active articles for debug display
            debug_active = dm.get_active_articles()
            st.write(f"Active Articles Count: {len(debug_active)}")
            if debug_active:
                st.write("Top 3 Active Articles (Raw):")
                st.json(debug_active[:3])
        except Exception as e:
            st.error(f"Debug Display Error: {e}")

    st.markdown("---")
    
    # System Info & Debug moved to bottom for visibility
    # REMOVED: Duplicate Debug Checkbox
            
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
    if creds_dict and sheet_name:
        try:
            # Check if we need to authenticate or re-connect
            if not dm.sm or not sm.client:
                success, msg = sm.authenticate(creds_dict)
                if success:
                    # Try to set backend immediately
                    try:
                        # Don't reload (load=False) on auto-reconnect to preserve local state
                        # unless the cache is empty
                        should_load = (len(dm.articles_cache) == 0)
                        dm.set_backend(sm, sheet_name, load=should_load)
                        is_sheet_connected = True
                        
                        # --- One-Time Reset Logic ---
                        if os.path.exists("reset_pending.txt"):
                            try:
                                dm.clear_all_articles()
                                os.remove("reset_pending.txt")
                                st.sidebar.success("✅ System Reset: All data wiped from Local and Remote.")
                                st.rerun()
                            except Exception as e:
                                st.sidebar.error(f"Reset Failed: {e}")
                        # ----------------------------
                        
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
                    # Reload DB to get latest changes (explicit sync)
                    dm.set_backend(sm, sheet_name, load=True)
                    
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
                if creds_dict:
                    st.success("✅ Credentials Found")
                    e = creds_dict.get("client_email")
                    if e:
                         st.markdown(f"**Service Email:** `{e}`")
                         st.info("Ensure the Google Sheet is shared with this email.")
                else:
                    st.error("❌ No Credentials Found")
                
                if not sheet_name:
                    st.error("❌ No Sheet Name/URL configured")

    # --- Main Content ---
    
    # View State Management
    if "current_view" not in st.session_state:
        st.session_state["current_view"] = "dashboard"
        
    active_articles = dm.get_active_articles()
    
    # Sort active articles by 'added_at' descending (newest first)
    active_articles.sort(key=lambda x: x.get("added_at", ""), reverse=True)

    if st.session_state["current_view"] == "dashboard":
        if st.session_state.get("new_article_added"):
            st.success("✅ New article added! It should appear at the top of the list below.")
            # Clear flag after showing once
            st.session_state["new_article_added"] = False
            
        st.subheader(f"Active Articles ({len(active_articles)})")
        
        # --- Quick Add Inputs ---
        if "quick_add_counter" not in st.session_state:
            st.session_state["quick_add_counter"] = 0
            
        with st.form("quick_add_form", clear_on_submit=False):
            qa_c1, qa_c2 = st.columns(2)
            # Use dynamic keys to allow programmatic clearing
            qa_key_url = f"qa_url_{st.session_state['quick_add_counter']}"
            qa_key_text = f"qa_text_{st.session_state['quick_add_counter']}"
            
            with qa_c1:
                qa_url = st.text_input("URL to analyze", key=qa_key_url)
            with qa_c2:
                qa_text = st.text_area("Text to analyze", height=100, key=qa_key_text)
            
            submitted = st.form_submit_button("Add and Analyze")
            if submitted:
                # Deduplication Check
                prefs = dm.get_preferences()
                deleted_urls = set(u.strip().rstrip('/') for u in prefs.get("deleted_urls", []) if u)
                
                # Active URLs
                active_urls = set()
                for a in active_articles:
                    u = a.get("url", "")
                    if u:
                        active_urls.add(u.strip().rstrip('/'))
                
                norm_qa_url = qa_url.strip().rstrip('/') if qa_url else ""
                
                if norm_qa_url:
                    if norm_qa_url in deleted_urls:
                         st.error("⚠️ This URL was previously deleted and is blacklisted.")
                         # Allow user to force add? Maybe not for now.
                         # st.stop() will stop execution here
                    elif norm_qa_url in active_urls:
                         st.warning("⚠️ This URL is already in the dashboard.")
                    else:
                        # Proceed with adding
                        to_add = []
                        
                        with st.spinner("Processing article..."):
                            # 1. Handle URL Input
                            if qa_url:
                                new_art = {
                                    "url": qa_url,
                                    "article_title": "Processing...",
                                    "status": "In Process",
                                    "source": "manual_dashboard",
                                    "added_at": datetime.now().isoformat()
                                }
                                
                                # Attempt immediate scrape and analysis
                                try:
                                    # Scrape
                                    content, err = scrape_article(qa_url)
                                    if err:
                                        new_art["last_error"] = f"Scrape Error: {err}"
                                        new_art["article_title"] = "Scrape Failed"
                                    else:
                                        new_art["scraped_text"] = content["text"]
                                        new_art["article_title"] = content["title"]
                                        
                                        # Analyze
                                        if api_key:
                                            try:
                                                analysis = analyze_content(content["text"], content["title"], api_key)
                                                new_art.update(analysis)
                                                new_art["status"] = "Not Started" # Ready for review
                                            except Exception as e:
                                                new_art["last_error"] = f"Analysis Error: {e}"
                                        else:
                                            new_art["last_error"] = "Analysis skipped: No API Key"
                                            
                                except Exception as e:
                                    new_art["last_error"] = f"Unexpected Error: {e}"
                                     
                                to_add.append(new_art)

                            # 2. Handle Text Input
                            if qa_text:
                                new_art = {
                                    "scraped_text": qa_text,
                                    "article_title": "New Text Analysis",
                                    "status": "In Process",
                                    "source": "manual_dashboard",
                                    "added_at": datetime.now().isoformat()
                                }
                                
                                if api_key:
                                    try:
                                        analysis = analyze_content(qa_text, "Provided Text", api_key)
                                        new_art.update(analysis)
                                        new_art["status"] = "Not Started"
                                    except Exception as e:
                                        new_art["last_error"] = f"Analysis Error: {e}"
                                else:
                                    new_art["last_error"] = "Analysis skipped: No API Key"
                                    
                                to_add.append(new_art)
                            
                            if to_add:
                                try:
                                    dm.save_articles(to_add)
                                    st.toast(f"✅ Added {len(to_add)} articles! Refreshing...")
                                    
                                    # Clear inputs by incrementing counter
                                    st.session_state["quick_add_counter"] += 1
                                    
                                    # Clear search to ensure visibility
                                    st.session_state["dashboard_search"] = ""
                                    
                                    # Small delay to ensure toast is seen and file write completes
                                    import time
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to save articles: {e}")
                            else:
                                st.warning("Please enter a URL or Text.")
                elif qa_text:
                    # Just Text (No URL check needed)
                    to_add = []
                    with st.spinner("Processing text..."):
                         new_art = {
                            "scraped_text": qa_text,
                            "article_title": "New Text Analysis",
                            "status": "In Process",
                            "source": "manual_dashboard",
                            "added_at": datetime.now().isoformat()
                        }
                        
                         if api_key:
                            try:
                                analysis = analyze_content(qa_text, "Provided Text", api_key)
                                new_art.update(analysis)
                                new_art["status"] = "Not Started"
                            except Exception as e:
                                new_art["last_error"] = f"Analysis Error: {e}"
                         else:
                            new_art["last_error"] = "Analysis skipped: No API Key"
                            
                         to_add.append(new_art)
                         
                         try:
                            dm.save_articles(to_add)
                            st.toast(f"✅ Added Text Analysis! Refreshing...")
                            
                            # Clear inputs by incrementing counter
                            st.session_state["quick_add_counter"] += 1
                            
                            st.session_state["dashboard_search"] = ""
                            import time
                            time.sleep(0.5)
                            st.rerun()
                         except Exception as e:
                            st.error(f"Failed to save: {e}")
                else:
                    st.warning("Please enter a URL or Text.")
        
        # Search
        search = st.text_input("Search articles...", placeholder="Title, Summary, or URL", key="dashboard_search")
        

        
        filtered = active_articles
        if search:
            s = search.lower()
            filtered = [a for a in active_articles if s in a.get("article_title", "").lower() or s in a.get("url", "").lower()]
            
        # --- Custom List View with Buttons ---
        if filtered:
            # Pagination
            items_per_page = 20
            total_pages = max(1, (len(filtered) - 1) // items_per_page + 1)
            
            # Only show pagination controls if needed
            if total_pages > 1:
                col_p1, col_p2 = st.columns([1, 4])
                with col_p1:
                    current_page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
            else:
                current_page = 1
            
            start_idx = (current_page - 1) * items_per_page
            end_idx = start_idx + items_per_page
            page_items = filtered[start_idx:end_idx]
            
            # Header
            c1, c2, c3, c4, c5 = st.columns([1.5, 3, 4, 0.7, 0.7])
            c1.markdown("**Status**")
            c2.markdown("**Title**")
            c3.markdown("**TL;DR**")
            c4.markdown("**Read**")
            c5.markdown("**Del**")
            st.divider()
            
            for a in page_items:
                c1, c2, c3, c4, c5 = st.columns([1.5, 3, 4, 0.7, 0.7])
                
                # Status
                current_status = a.get("status", "Not Started")
                # Normalize status for dropdown
                if current_status == "In Progress": current_status = "In Process"
                if current_status == "Done": current_status = "Complete"
                if current_status not in ["Not Started", "In Process", "Complete"]:
                    current_status = "Not Started"
                    
                new_status = c1.selectbox(
                    "Status", 
                    options=["Not Started", "In Process", "Complete"], 
                    key=f"status_{a['id']}", 
                    index=["Not Started", "In Process", "Complete"].index(current_status),
                    label_visibility="collapsed"
                )
                
                if new_status != current_status:
                    dm.update_article(a['id'], {"status": new_status})
                    st.toast(f"Updated status to {new_status}")
                    # Optional: st.rerun() if we want immediate refresh
                
                # Title
                c2.write(a.get("article_title", "Untitled"))
                
                # TLDR
                tldr = a.get("tl_dr", "")
                if isinstance(tldr, list): tldr = "; ".join(tldr)
                c3.write(str(tldr)[:200] + "..." if len(str(tldr)) > 200 else str(tldr))
                
                # Read Button
                if c4.button("Read", key=f"read_{a['id']}"):
                    st.session_state["selected_article_id"] = a["id"]
                    st.session_state["current_view"] = "details"
                    st.rerun()
                    
                # Delete Button
                if c5.button("🗑️", key=f"del_{a['id']}", help="Permanently Delete and Blacklist URL"):
                    dm.purge_article(a['id'])
                    st.toast("Article deleted and blacklisted")
                    st.rerun()
                    
                st.markdown("---")
        else:
            st.info("No articles found.")

    elif st.session_state["current_view"] == "details":
        if st.button("← Back to Dashboard"):
            st.session_state["current_view"] = "dashboard"
            st.rerun()
            
        # Logic for displaying details
        if not active_articles:
             st.info("No articles available.")
        else:
            # Find selected article
            selected_id = st.session_state.get("selected_article_id")
            # Validate if it still exists
            current_article = next((a for a in active_articles if a["id"] == selected_id), None)
            
            if not current_article:
                st.warning("Selected article not found (it may have been deleted).")
                st.session_state["current_view"] = "dashboard"
                st.rerun()
            else:
                # Set 'article' for the rest of the logic
                article = current_article
                sel_id = selected_id
                
                # Header
                fi = article.get("fraud_indicator")
                
                # Editable Title
                col_t1, col_t2 = st.columns([4, 1])
                with col_t1:
                     new_title = st.text_input("Article Title", value=article.get("article_title", "Untitled"), key=f"title_edit_{sel_id}", label_visibility="collapsed")
                     if new_title != article.get("article_title"):
                         dm.update_article(sel_id, {"article_title": new_title})
                         st.rerun()
                
                with col_t2:
                     # Display Fraud Indicator Badge next to title if exists
                     if fi:
                        fi_color = "red" if fi == "High" else "orange" if fi == "Medium" else "green"
                        st.markdown(f"<div style='text-align:right; padding-top: 5px;'><span style='color:{fi_color}; font-size:1em; border:1px solid {fi_color}; padding:5px 10px; border-radius:5px;'>{fi.upper()}</span></div>", unsafe_allow_html=True)

                st.caption(f"URL: {article.get('url')}")
                
                # --- Sticky Navigation & Actions Toolbar ---
                st.markdown('<div id="sticky-marker"></div>', unsafe_allow_html=True)
                with st.container():
                    # Calculate IDs for Navigation
                    active_ids = [a["id"] for a in active_articles]
                    try:
                        curr_idx = active_ids.index(sel_id)
                    except:
                        curr_idx = 0
                    
                    prev_id = active_ids[curr_idx - 1] if curr_idx > 0 else None
                    next_id = active_ids[curr_idx + 1] if curr_idx < len(active_ids) - 1 else None

                    # Toolbar Layout: [Prev] [Next] [Analyze URL] [Analyze Text] [Delete] [Status]
                    t1, t2, t3, t4, t5, t6 = st.columns([0.6, 0.6, 1.3, 1.3, 0.6, 2])
                    
                    with t1:
                        if st.button("⬅️", disabled=(prev_id is None), help="Previous Article"):
                            st.session_state["selected_article_id"] = prev_id
                            st.rerun()
                    with t2:
                        if st.button("➡️", disabled=(next_id is None), help="Next Article"):
                            st.session_state["selected_article_id"] = next_id
                            st.rerun()
                    
                    with t3:
                        analyze_url_clicked = st.button("Analyze URL", help="Scrape and analyze the URL")
                        
                    with t4:
                        if st.button("Analyze Text", help="Manually paste text to analyze"):
                            st.session_state[f"show_text_input_{sel_id}"] = not st.session_state.get(f"show_text_input_{sel_id}", False)
                            st.rerun()

                    with t5:
                        if st.button("🗑️", help="Permanently Delete Article"):
                            dm.purge_article(sel_id)
                            st.toast("Article deleted and blacklisted")
                            # Try to go to next, else prev, else dashboard
                            new_id = next_id if next_id else prev_id
                            if new_id:
                                st.session_state["selected_article_id"] = new_id
                            else:
                                st.session_state["current_view"] = "dashboard"
                            st.rerun()
                            
                    with t6:
                        # Status
                        current_status = article.get("status", "Not Started")
                        # Normalize
                        if current_status == "In Progress": current_status = "In Process"
                        if current_status == "Done": current_status = "Complete"
                        status_opts = ["Not Started", "In Process", "Complete", "Deleted"]
                        try:
                            idx = status_opts.index(current_status)
                        except ValueError:
                            idx = 0
                        new_st = st.selectbox("Status", status_opts, index=idx, key=f"status_detail_{sel_id}", label_visibility="collapsed")
                        if new_st != current_status:
                            dm.update_article(sel_id, {"status": new_st})
                            st.rerun()
                    
                    st.divider()

                # --- Analysis Logic ---
                do_analysis = False
                analysis_text = ""
                analysis_title = article.get("article_title", "Unknown")
                
                # 1. Manual Text Input
                if st.session_state.get(f"show_text_input_{sel_id}", False):
                    with st.form("manual_text_form"):
                        st.markdown("### Paste Article Text")
                        txt_input = st.text_area("Text Content", value=article.get("scraped_text", ""), height=300)
                        if st.form_submit_button("Run Analysis on Text"):
                            analysis_text = txt_input
                            analysis_title = article.get("article_title") # Keep existing title or ask?
                            dm.update_article(sel_id, {"scraped_text": analysis_text})
                            do_analysis = True
                
                # 2. URL Scrape Trigger
                if analyze_url_clicked:
                    with st.spinner("Scraping..."):
                        content, err = scrape_article(article.get("url"))
                        if err:
                            st.error(err)
                        else:
                            analysis_text = content["text"]
                            analysis_title = content["title"]
                            dm.update_article(sel_id, {"scraped_text": analysis_text, "article_title": analysis_title})
                            do_analysis = True

                # 3. Perform Analysis if triggered
                if do_analysis and api_key:
                     with st.spinner("Analyzing with GPT-4o..."):
                        try:
                            # Use current title for context, but allow GPT to override if it finds a better one
                            analysis = analyze_content(analysis_text, analysis_title, api_key)
                            
                            # Merge into article
                            update_payload = {**analysis}
                            
                            # Ensure we prioritize the scraped title if available, or GPT title if it's better than "Processing..."
                            # If GPT returns a title, use it, but maybe respect manual edits?
                            # User asked: "if an article fails... update the Title and TLDR on the dashboard"
                            # So we should trust the new analysis title.
                            
                            if "article_title" not in update_payload and analysis_title and analysis_title != "Processing...":
                                update_payload["article_title"] = analysis_title
                            
                            dm.update_article(sel_id, update_payload)
                            st.success("Analysis complete!")
                            st.session_state[f"show_text_input_{sel_id}"] = False # Hide input after success
                            st.rerun()
                        except Exception as e:
                            st.error(f"Analysis failed: {e}")
                elif do_analysis and not api_key:
                    st.error("Please enter API Key in sidebar.")

                # Display Analysis Results
                if article.get("tl_dr") or article.get("summary"):
                    st.markdown("---")
                    
                    # Header with Fraud Indicator
                    fi = article.get("fraud_indicator", "Unknown")
                    fi_color = "red" if fi == "High" else "orange" if fi == "Medium" else "green"
                    st.markdown(f"### Analysis Result <span style='color:{fi_color}; font-size:0.8em; border:1px solid {fi_color}; padding:2px 5px; border-radius:5px;'>{fi.upper()}</span>", unsafe_allow_html=True)
                    
                    st.subheader("1. TL;DR")
                    tldr = article.get("tl_dr")
                    if isinstance(tldr, list):
                        for point in tldr:
                            st.markdown(f"- {point}")
                    else:
                        st.write(tldr)

                    st.subheader("2. Summary")
                    st.write(article.get("summary", ""))

                    st.subheader("3. Organizations Involved")
                    orgs = article.get("organizations_involved", [])
                    if orgs:
                        for idx_org, org in enumerate(orgs):
                            if isinstance(org, dict):
                                name = org.get("name", "Unknown")
                                role = org.get("role_summary", org.get("role", ""))
                                st.markdown(f"**{name}**: {role}")
                                
                                # People Logic
                                people = org.get("people", [])
                                if people:
                                    for idx_p, person in enumerate(people):
                                        p_name = person.get("name", "Unknown")
                                        p_role = person.get("role", "")
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;👤 **{p_name}** — {p_role}")
                                        
                                        # Buttons Row
                                        # Use columns for compact buttons
                                        pb1, pb2, pb3 = st.columns([1.5, 2, 6])
                                        with pb1:
                                            # LinkedIn Lookup
                                            li_url = f"https://www.linkedin.com/search/results/all/?keywords={urllib.parse.quote_plus(p_name)}"
                                            st.link_button("LinkedIn Search", li_url)
                                        with pb2:
                                            # Person Details (GPT)
                                            # Unique key for every button
                                            btn_key = f"btn_p_det_{sel_id}_{idx_org}_{idx_p}"
                                            if st.button("Person Details", key=btn_key):
                                                with st.spinner(f"Researching {p_name}..."):
                                                    details = analyze_person(p_name, article.get("scraped_text", ""), api_key)
                                                    person["details"] = details
                                                    # Update the main article object in DB
                                                    dm.update_article(sel_id, {"organizations_involved": orgs})
                                                    st.rerun()
                                        
                                        # Display Details if available
                                        if person.get("details"):
                                            st.info(f"**Details on {p_name}:** {person.get('details')}")

                            elif isinstance(org, str):
                                st.markdown(f"- {org}")

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.subheader("4. Allegations")
                        st.write(article.get("allegations", "N/A"))
                    with col_b:
                        st.subheader("5. Current Situation")
                        st.write(article.get("current_situation", "N/A"))

                    st.subheader("6. Next Steps")
                    st.write(article.get("next_steps", "N/A"))

                    st.subheader("7. Tracklight Prevention")
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
                    if "chat_counter" not in st.session_state:
                        st.session_state["chat_counter"] = 0
                    
                    chat_key = f"chat_in_{sel_id}_{st.session_state['chat_counter']}"

                    with st.form("chat_form"):
                        user_q = st.text_input("Ask a question about this article:", key=chat_key)
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
                                    
                                    # Clear input
                                    st.session_state["chat_counter"] += 1
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
