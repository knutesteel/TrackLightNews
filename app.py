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
load_dotenv()

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
                timeout_config = (5 + (i * 5), 15 + (i * 15))
                
                r = requests.get(url, headers=headers, timeout=timeout_config, allow_redirects=True)
                if r.status_code == 200 and r.content:
                    resp = r
                    break
                else:
                    last_err = f"HTTP {r.status_code}"
            except Exception as e:
                last_err = str(e)
        
        if not resp:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, headers={'User-Agent': uas[0]}, timeout=(10, 30), verify=False)
                if r.status_code == 200:
                    resp = r
            except Exception:
                pass

        if not resp:
            return f"Error: {last_err or 'Failed to fetch'}"
        
        resp.encoding = resp.apparent_encoding or resp.encoding
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup.select('nav, header, footer, aside, script, style'):
            tag.decompose()
        article_node = soup.find('article')
        main_node = soup.find('main') or soup.find(attrs={'role': 'main'})
        container = article_node or main_node or soup
        paragraphs = container.find_all('p')
        chunks = []
        for p in paragraphs:
            t = p.get_text(separator=' ', strip=True)
            if t and len(t) > 20:
                chunks.append(t)
        text_content = ' '.join(chunks)
        if not text_content or len(text_content) < 200:
            paragraphs = soup.find_all('p')
            text_content = ' '.join([p.get_text(separator=' ', strip=True) for p in paragraphs])
        return text_content[:15000]
    except Exception as e:
        return f"Error: {str(e)}"

def analyze_with_chatgpt(text, api_key, timeout_seconds=45):
    client = openai.OpenAI(api_key=api_key)
    
    prompt = """
    You are an expert fraud analyst for Tracklight.ai. Analyze the following news article text.
    
    Extract and generate the following information in strict JSON format:
    1. "article_title": The title of the article.
    2. "date": The date of the article or event (YYYY-MM-DD format if possible). Double-check the text for publication dates or time references to ensure accuracy. If unsure, state "Unknown".
    3. "date_verification": A brief explanation of how you determined the date (e.g., "Found publication date in header", "Inferred from 'yesterday' reference relative to current date context").
    4. "fraud_indicator": One of ["High", "Medium", "Low"].
    5. "tl_dr": A two-sentence summary of the article (formerly 'summary').
    6. "full_summary_bullets": A list of strings, each a key point from the article.
    7. "history_overview": 2–4 concise bullet points summarizing relevant history or background.
    8. "allegations": 2–4 concise bullet points summarizing alleged actions/issues (use "None" if not applicable).
    9. "current_situation": 2–4 concise bullet points summarizing the current status.
    10. "next_steps": 2–4 concise bullet points for what comes next (investigations, audits, policy changes).
    11. "people_mentioned": A list of names of people or key contacts mentioned.
    12. "prevention_strategies": A list of objects, where each object has:
       - "issue": A string referencing a specific key point or fraud vulnerability from the summary.
       - "prevention": A string explaining how Tracklight.ai (a fraud prevention platform) could have prevented this specific issue. Tailor the response to the issue.
    13. "discovery_questions": A list of questions to ask potential clients as Tracklight discovery questions, tailored to this article.
    
    Return ONLY the JSON object. Do not add markdown formatting like ```json ... ```.
    
    Article Text:
    """ + text

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            response_format={"type": "json_object"},
            timeout=timeout_seconds
        )
        return response.choices[0].message.content
    except Exception:
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                response_format={"type": "json_object"},
                timeout=timeout_seconds
            )
            return response.choices[0].message.content
        except Exception as e2:
            return f"Error calling OpenAI: {str(e2)}"

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

# Initialize Managers
dm = DataManager()
sm = SheetManager()

def render_brand_header():
    # Check for local logo file
    logo_files = [f for f in os.listdir('.') if f.lower().startswith('logo.') and f.lower().endswith(('.png', '.jpg', '.jpeg', '.svg'))]
    
    if logo_files:
        # Display logo
        st.image(logo_files[0], width=200)
    else:
        # Fallback title
        st.title("Tracklight.ai Article Analyzer")

def maybe_auto_check_email(email_user, email_pass, api_key, force=False):
    try:
        if not email_user or not email_pass:
            return
        now_ts = datetime.now().timestamp()
        last_ts = float(st.session_state.get("last_email_check_ts", 0))
        if not force and now_ts - last_ts < 300:
            return
        st.session_state["last_email_check_ts"] = now_ts
        with st.spinner("Checking email..."):
            em = EmailManager(email_user, email_pass)
            prefs = dm.get_preferences()
            blocked = prefs.get("blocked_domains", [])
            links = em.fetch_new_links(blocked_domains=blocked)
            if isinstance(links, str) and links.startswith("Error"):
                st.session_state["last_activity_log"] = (st.session_state.get("last_activity_log", "") + f"[{datetime.now().strftime('%H:%M:%S')}] Auto-Email Error: {links}\n")
                return
            if not links:
                return
            articles = dm.get_all_articles()
            existing_urls = {a.get("url") for a in articles if a.get("url")}
            new_links = [l for l in links if l not in existing_urls]
            if not new_links:
                return
            progress_bar = st.progress(0)
            added_count = 0
            for i, url in enumerate(new_links):
                new_art = {
                    "url": url,
                    "status": "In Process",
                    "article_title": "Analyzing...",
                    "added_at": datetime.now().isoformat()
                }
                saved_art = dm.save_article(new_art)
                txt = scrape_article(url)
                if isinstance(txt, str) and txt.startswith("Error"):
                    dm.update_article(saved_art["id"], {
                        "last_error": txt, 
                        "status": "Error",
                        "tl_dr": "Unknown - Bad Link?"
                    })
                else:
                    if api_key:
                        res = analyze_with_chatgpt(txt, api_key)
                        if isinstance(res, str) and res.startswith("Error"):
                            dm.update_article(saved_art["id"], {"last_error": res, "status": "Not Started"})
                        else:
                            try:
                                data = json.loads(res)
                                data = normalize_analysis(data)
                                updates = {
                                    "status": "Qualified",
                                    "article_title": data.get("article_title", "Unknown Title"),
                                    "date": data.get("date"),
                                    "date_verification": data.get("date_verification"),
                                    "fraud_indicator": data.get("fraud_indicator"),
                                    "tl_dr": data.get("tl_dr", data.get("summary")),
                                    "full_summary_bullets": data.get("full_summary_bullets"),
                                    "people_mentioned": data.get("people_mentioned"),
                                    "prevention_strategies": data.get("prevention_strategies", data.get("prevention")),
                                    "discovery_questions": data.get("discovery_questions"),
                                    "last_error": ""
                                }
                                dm.update_article(saved_art["id"], updates)
                            except:
                                dm.update_article(saved_art["id"], {"last_error": "JSON Parse Error"})
                    else:
                        dm.update_article(saved_art["id"], {"last_error": "No API Key"})
                added_count += 1
                progress_bar.progress((i + 1) / len(new_links))
            st.success(f"Auto-added {added_count} new article(s).")
            st.rerun()
    except Exception:
        pass

def main():
    # --- Sidebar ---
    with st.sidebar:
        st.header("Settings")
        
        # Page Navigation - REMOVED (moved to Tabs)
        # page = st.radio("Navigation", ["Dashboard", "TLDR Overview"])
        # st.markdown("---")

        # Check for existing key in environment
        env_api_key = os.getenv("OPENAI_API_KEY", "")
        
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

        st.subheader("Email Integration")
        st.caption("Auto-load URLs from Gmail.")
        
        env_email_user = os.getenv("EMAIL_USER", "articleanalyzer@gmail.com")
        env_email_pass = os.getenv("EMAIL_PASS", "")
        
        email_user = st.text_input("Gmail Address", value=env_email_user)
        email_pass = st.text_input("App Password", type="password", value=env_email_pass, help="Generate an App Password in your Google Account settings.")
        
        if st.button("Save Email Config"):
            set_key(".env", "EMAIL_USER", email_user)
            set_key(".env", "EMAIL_PASS", email_pass)
            st.success("Email settings saved!")
            st.rerun()

        try:
            from streamlit import st_autorefresh
            st_autorefresh(interval=300000, key="email_poll")
        except Exception:
            pass
        maybe_auto_check_email(email_user, email_pass, api_key)

        st.markdown("---")
        
        st.subheader("Google Sheets Integration")
        st.caption("Auto-load URLs from a Google Sheet.")
        
        sheet_name = st.text_input("Google Sheet Name / ID / URL", value=os.getenv("GOOGLE_SHEET_NAME", ""), help="You can enter the exact Name, the Sheet ID, or the full URL.")
        creds_input = st.text_area("Service Account JSON", placeholder="{ ... }", help="Paste the content of your service_account.json here.")
        
        saved_creds_file = "google_creds.json"
        has_saved_creds = os.path.exists(saved_creds_file)
        
        if st.button("Save Google Config"):
            if sheet_name:
                set_key(".env", "GOOGLE_SHEET_NAME", sheet_name)
            
            if creds_input:
                try:
                    json.loads(creds_input)
                    with open(saved_creds_file, 'w') as f:
                        f.write(creds_input)
                    st.success("Configuration saved!")
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("Invalid JSON format for credentials.")
            elif has_saved_creds and sheet_name:
                 st.success("Sheet name saved!")
                 st.rerun()
        
        if has_saved_creds:
            st.success("✅ Credentials loaded locally")

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
        global_summary_page(api_key)
    else:
        # Main Dashboard
        dashboard_page(api_key, sheet_name, saved_creds_file, has_saved_creds, email_user, email_pass)

def global_summary_page(api_key):
    st.title("🌎 Global Analysis & Grouping")
    if st.button("⬅️ Back to Dashboard"):
        st.session_state["is_global_summary"] = False
        st.rerun()
        
    if not api_key:
        st.warning("Please enter OpenAI API Key in the sidebar to run analysis.")
        return
    
    articles = dm.get_all_articles()
    if not articles:
        st.info("No articles available to analyze.")
        return

    st.markdown("This tool sends summaries of your articles to ChatGPT to identify common themes, fraud schemes, and groupings.")
    
    if st.button("Run Global Analysis", type="primary"):
        with st.spinner("Analyzing all articles for commonalities..."):
            report = analyze_global_summary(articles, api_key)
            st.session_state["global_report_json"] = report
            # Clear old text report if it exists
            if "global_report" in st.session_state:
                del st.session_state["global_report"]
    
    # Handle Legacy Text Report (if user hasn't re-run) or New JSON Report
    if "global_report_json" in st.session_state:
        report = st.session_state["global_report_json"]
        
        if isinstance(report, str) and report.startswith("Error"):
            st.error(report)
        elif isinstance(report, dict):
            # Render Groups
            st.markdown("---")
            groups = report.get("groups", [])
            art_map = {a["id"]: a for a in articles}
            
            if not groups:
                st.info("No groupings found.")
            
            for grp in groups:
                g_title = grp.get("group_title", "Uncategorized")
                ids = grp.get("article_ids", [])
                
                # Filter for valid articles
                valid_arts = [art_map[i] for i in ids if i in art_map]
                count = len(valid_arts)
                
                if count > 0:
                    with st.expander(f"{g_title} ({count} articles)", expanded=False):
                        for art in valid_arts:
                            title = art.get("article_title", "Unknown Title")
                            aid = art.get("id")
                            # Use HTML link to ensure it opens in same tab and triggers reload with param
                            st.markdown(f"• <a href='/?article_id={aid}' target='_self' style='text-decoration:none; color:inherit; hover:text-decoration:underline;'>{title}</a>", unsafe_allow_html=True)
        else:
            # Fallback for some reason
            st.write(report)
            
    elif "global_report" in st.session_state:
        # Legacy support if they didn't re-run
        st.markdown("---")
        st.markdown(st.session_state["global_report"])

def logs_page():
    st.title("📜 Activity Logs")
    if st.button("⬅️ Back to Dashboard"):
        st.session_state["show_logs"] = False
        st.rerun()
    
    if "last_activity_log" in st.session_state and st.session_state["last_activity_log"]:
        st.code(st.session_state["last_activity_log"], language="text")
        if st.button("Clear Log"):
            del st.session_state["last_activity_log"]
            st.rerun()
    else:
        st.info("No logs available.")

def dashboard_page(api_key, sheet_name, saved_creds_file, has_saved_creds, email_user, email_pass):
    col_title, col_check, col_global = st.columns([2.5, 1, 1])
    with col_title:
        st.title("🔍 Tracklight.ai Article Analyzer")
    with col_check:
        if st.button("📧 Check Email Now"):
             maybe_auto_check_email(email_user, email_pass, api_key, force=True)
    with col_global:
        if st.button("🌎 Global Analysis"):
            st.session_state["is_global_summary"] = True
            st.rerun()


    # --- Queue Processing Logic ---
    if st.session_state.get("is_reanalyzing", False):
        queue = st.session_state.get("reanalyze_queue", [])
        
        if not queue:
            st.session_state["is_reanalyzing"] = False
            st.success("Batch processing complete!")
            if st.button("Clear Log & Continue"):
                st.session_state["last_activity_log"] = ""
                st.rerun()
        else:
            current_id = queue.pop(0)
            st.session_state["reanalyze_queue"] = queue
            
            articles_map = {a["id"]: a for a in dm.get_all_articles()}
            row = articles_map.get(current_id)
            
            if "last_activity_log" not in st.session_state:
                st.session_state["last_activity_log"] = ""
                
            def log(msg):
                ts = datetime.now().strftime("%H:%M:%S")
                entry = f"[{ts}] {msg}"
                st.session_state["last_activity_log"] += entry + "\n"
            
            with st.status(f"Processing... ({len(queue) + 1} remaining)", expanded=True) as status:
                st.code(st.session_state["last_activity_log"], language="text")
                
                if not row:
                    log(f"Error: Article ID {current_id} not found.")
                else:
                    url = row.get("url")
                    title = row.get("article_title", "Unknown")
                    log(f"--- Processing: {title or url} ---")
                    
                    api_key_use = os.getenv("OPENAI_API_KEY") or st.session_state.get("api_key_input", "")
                    
                    if not api_key_use:
                        log("Error: No API Key found.")
                    elif not url:
                        log("Error: Missing URL.")
                        try:
                            dm.update_article(current_id, {"last_error": "Missing URL"})
                        except: pass
                    else:
                        log(f"Scraping URL: {url}")
                        txt = scrape_article(url)
                        
                        if isinstance(txt, str) and txt.startswith("Error"):
                            log(f"Scrape Failed: {txt}")
                            try:
                                dm.update_article(current_id, {
                                    "last_error": txt, 
                                    "status": "Error",
                                    "tl_dr": "Unknown - Bad Link?"
                                })
                            except: pass
                        else:
                            log(f"Scrape Success ({len(txt)} chars). Analyzing...")
                            res = analyze_with_chatgpt(txt, api_key_use)
                            
                            if isinstance(res, str) and res.startswith("Error"):
                                log(f"AI Failed: {res}")
                                try:
                                    dm.update_article(current_id, {"last_error": res, "status": "Not Started"})
                                except: pass
                            else:
                                log("AI Success. Updating DB...")
                                try:
                                    data = json.loads(res)
                                    data = normalize_analysis(data)
                                    updates = {
                                        "article_title": data.get("article_title", row.get("article_title")),
                                        "date": data.get("date", row.get("date")),
                                        "date_verification": data.get("date_verification", row.get("date_verification")),
                                        "fraud_indicator": data.get("fraud_indicator", row.get("fraud_indicator")),
                                        "tl_dr": data.get("tl_dr", data.get("summary", row.get("tl_dr", row.get("summary")))),
                                        "full_summary_bullets": data.get("full_summary_bullets", row.get("full_summary_bullets")),
                                        "people_mentioned": data.get("people_mentioned", row.get("people_mentioned")),
                                        "prevention_strategies": data.get("prevention_strategies", row.get("prevention_strategies", row.get("prevention"))),
                                        "discovery_questions": data.get("discovery_questions", row.get("discovery_questions")),
                                        "last_error": ""
                                    }
                                    dm.update_article(current_id, updates)
                                    log("Saved.")
                                except json.JSONDecodeError:
                                    log("Error: JSON Parse Failed.")
                                    try:
                                        dm.update_article(current_id, {"last_error": "JSON Parse Error"})
                                    except: pass
                                except Exception as e:
                                    log(f"Error saving: {e}")
            st.rerun()

    # Check for details view via query params
    try:
        params_init = st.query_params
        if isinstance(params_init, dict):
            pid_raw = params_init.get("article_id")
            if pid_raw:
                pid = pid_raw[0] if isinstance(pid_raw, list) else pid_raw
                if pid:
                    articles_all = dm.get_all_articles()
                    sel_article = next((a for a in articles_all if a.get("id") == pid), None)
                    if sel_article:
                        st.session_state["selected_article"] = sel_article
                        st.session_state["is_details"] = True
    except Exception:
        pass
        
    is_details = st.session_state.get("is_details", False)

    if not is_details:
        # --- Sync Section ---
        col_url, col_text = st.columns(2)
        with col_url:
            st.markdown("### By URL")
            url_input = st.text_input("Article URL", placeholder="https://example.com/news-article")
            analyze_url = st.button("Analyze URL", type="primary")

        with col_text:
            st.markdown("### By Text")
            text_input = st.text_area("Article Text", height=150, placeholder="Paste full text here...")
            analyze_text = st.button("Analyze Text", type="primary")

        target_mode = None
        target_content = None
        
        if analyze_url:
            if not url_input:
                st.error("Please enter a URL.")
            else:
                target_mode = "URL"
                target_content = url_input
        elif analyze_text:
            if not text_input:
                st.error("Please paste text.")
            else:
                target_mode = "Text"
                target_content = text_input

        if target_mode and target_content:
            if not api_key:
                st.error("Please provide an OpenAI API Key in the sidebar.")
            else:
                log_container = st.empty()
                log_msgs = []
                def log(msg):
                    ts = datetime.now().strftime("%H:%M:%S")
                    log_msgs.append(f"[{ts}] {msg}")
                    log_container.code("\n".join(log_msgs), language="text")

                target_url_display = target_content if target_mode == "URL" else f"Manual Entry {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                log(f"Starting process for: {target_url_display}")
                
                article_text = ""
                if target_mode == "URL":
                    with st.spinner("Scraping article..."):
                        log("Scraping URL...")
                        article_text = scrape_article(target_content)
                else:
                    log("Using pasted text...")
                    article_text = target_content
                    
                if article_text.startswith("Error"):
                    log(f"Scrape Failed: {article_text}")
                    st.error(f"Failed to scrape article: {article_text}")
                    # Save as error record
                    err_data = {
                        "article_title": "Unknown - Link Error",
                        "url": target_url_display,
                        "tl_dr": "Unknown - Bad Link?",
                        "status": "Error",
                        "last_error": article_text,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "fraud_indicator": "Unknown"
                    }
                    dm.save_article(err_data)
                    st.warning("Saved as error record.")
                    st.rerun()
                else:
                    log(f"Text ready ({len(article_text)} chars). Analyzing...")
                    with st.spinner("Analyzing with ChatGPT..."):
                        analysis_result = analyze_with_chatgpt(article_text, api_key)
                        
                    if analysis_result.startswith("Error"):
                        log(f"AI Failed: {analysis_result}")
                        st.error(analysis_result)
                    else:
                        log("AI Success. Parsing & Saving...")
                        try:
                            data = json.loads(analysis_result)
                            data = normalize_analysis(data)
                            data["url"] = target_url_display
                            dm.save_article(data)
                            log("Saved successfully.")
                            st.success("Analysis Complete! The article has been added to the dashboard.")
                            st.rerun()
                        except json.JSONDecodeError:
                            log("Error: JSON Parse Failed.")
                            st.error("Failed to parse response.")

        st.markdown("---")

        articles = dm.get_all_articles()
        
        if not articles:
            st.info("No articles analyzed yet. Use the section above to add one.")
        else:
            # Prepare DataFrame
            df = pd.DataFrame(articles)
            
            # --- Filters & Sorting Controls ---
            status_options = ["Not Started", "In Process", "Qualified", "Disqualified"]
            default_status = st.session_state.get("status_filter", ["Not Started", "In Process", "Qualified"])
            status_filter = st.multiselect("Filter by Status", status_options, default_status)
            st.session_state["status_filter"] = status_filter

            col_sort1, col_sort2 = st.columns([2, 1])
            with col_sort1:
                sort_by_options = ["Date Added", "Fraud Indicator", "Title", "Date", "Status"]
                default_sort_by = st.session_state.get("sort_by", "Date Added")
                sort_by = st.selectbox("Sort by", sort_by_options, index=sort_by_options.index(default_sort_by))
                st.session_state["sort_by"] = sort_by
            
            with col_sort2:
                order_options = ["Descending", "Ascending"]
                default_order = st.session_state.get("sort_order", "Descending")
                sort_order = st.radio("Order", order_options, index=order_options.index(default_order), horizontal=True)
                st.session_state["sort_order"] = sort_order

            # Export to Excel
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Articles')
            
            st.download_button(
                label="📥 Export Table to Excel",
                data=excel_buffer.getvalue(),
                file_name=f"articles_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            view_df = df.copy()

            # Apply Status filter
            if "status" in view_df.columns and status_filter:
                view_df = view_df[view_df["status"].isin(status_filter)]
            


            # Apply Sorting
            ascending = sort_order == "Ascending"
            if sort_by == "Date Added":
                def _parse_added(x):
                    try: return pd.to_datetime(x, errors="coerce")
                    except: return pd.NaT
                view_df["_added"] = view_df["added_at"].apply(_parse_added)
                view_df = view_df.sort_values(by="_added", ascending=ascending, na_position="last").drop(columns=["_added"])
            elif sort_by == "Fraud Indicator":
                rank = {"High": 3, "Medium": 2, "Low": 1}
                view_df["_rank"] = view_df["fraud_indicator"].map(rank).fillna(0)
                view_df = view_df.sort_values(by="_rank", ascending=ascending).drop(columns=["_rank"])
            elif sort_by == "Title":
                view_df = view_df.sort_values(by="article_title", ascending=ascending, na_position="last")
            elif sort_by == "Date":
                def _parse_date(x):
                    try: return pd.to_datetime(x, errors="coerce")
                    except: return pd.NaT
                view_df["_date"] = view_df["date"].apply(_parse_date)
                view_df = view_df.sort_values(by="_date", ascending=ascending, na_position="last").drop(columns=["_date"])
            elif sort_by == "Status":
                s_rank = {"Not Started": 0, "In Process": 1, "Qualified": 2, "Disqualified": 3}
                view_df["_srank"] = view_df["status"].map(s_rank).fillna(99)
                view_df = view_df.sort_values(by="_srank", ascending=ascending).drop(columns=["_srank"])

            view_df = view_df.reset_index(drop=True)
            st.session_state["current_view"] = view_df.to_dict(orient="records")

            # Append duplicate rows
            dup_rows = st.session_state.get("duplicate_rows", [])
            if isinstance(dup_rows, list) and dup_rows:
                dup_df = pd.DataFrame(dup_rows)
                view_df = pd.concat([view_df, dup_df], ignore_index=True)
            
            # --- CUSTOM ROW LAYOUT (Mimic Table but with st.columns) ---
            # This allows multiline wrapping + selection + hyperlinks
            
            # --- SELECTION STATE MANAGEMENT ---
            if "selected_rows" not in st.session_state:
                st.session_state["selected_rows"] = set()
            
            # Bulk Selection
            col_sel1, col_sel2, col_dummy = st.columns([1, 1, 6])
            with col_sel1:
                if st.button("Select All"):
                    st.session_state["selected_rows"] = set(view_df["id"].dropna().tolist())
                    st.rerun()
            with col_sel2:
                if st.button("Deselect All"):
                    st.session_state["selected_rows"] = set()
                    st.rerun()

            # --- ACTIONS BAR ---
            with st.container():
                st.markdown('<span id="sticky-marker"></span>', unsafe_allow_html=True)
                action_col1, action_col2, action_col3 = st.columns([1, 1, 1])
                with action_col1:
                    if st.button("Show Details (First Selected)", use_container_width=True):
                        sel = list(st.session_state["selected_rows"])
                        if not sel:
                            st.warning("Select an article.")
                        else:
                            art = next((a for a in articles if a["id"] == sel[0]), None)
                            if art:
                                st.session_state["selected_article"] = art
                                st.session_state["is_details"] = True
                                st.rerun()

                with action_col2:
                     if st.button("Reanalyze Selected", use_container_width=True):
                        sel = list(st.session_state["selected_rows"])
                        if not sel:
                            st.warning("No articles selected.")
                        elif not api_key:
                            st.error("No API Key.")
                        else:
                            st.session_state["reanalyze_queue"] = sel
                            st.session_state["is_reanalyzing"] = True
                            st.session_state["last_activity_log"] = ""
                            st.rerun()
                
                with action_col3:
                    if st.button("Delete Selected", type="primary", use_container_width=True):
                        sel = list(st.session_state["selected_rows"])
                        if sel:
                            for aid in sel:
                                dm.delete_article(aid)
                            st.session_state["selected_rows"] = set()
                            st.success(f"Deleted {len(sel)} articles.")
                            st.rerun()

            # --- HEADER ROW ---
            st.markdown("---")
            h1, h2, h3, h4, h5, h6 = st.columns([0.5, 3, 1, 1, 1, 4])
            h1.markdown("**Sel**")
            h2.markdown("**Title**")
            h3.markdown("**Date**")
            h4.markdown("**Fraud**")
            h5.markdown("**Status**")
            h6.markdown("**TL;DR Summary**")
            st.markdown("---")
            
            # --- RENDER ROWS ---
            for idx, row in view_df.iterrows():
                aid = row.get("id")
                if not aid: continue # Skip duplicates/invalid
                
                c1, c2, c3, c4, c5, c6 = st.columns([0.5, 3, 1, 1, 1, 4])
                
                # 1. Selection
                is_sel = aid in st.session_state["selected_rows"]
                # Use a callback to update state immediately
                def update_sel(aid=aid):
                    if st.session_state[f"sel_{aid}"]:
                        st.session_state["selected_rows"].add(aid)
                    else:
                        st.session_state["selected_rows"].discard(aid)

                c1.checkbox("Select", key=f"sel_{aid}", value=is_sel, on_change=update_sel, label_visibility="collapsed")
                
                # 2. Title (Link)
                title = row.get("article_title", "Untitled")
                url = row.get("url", "#")
                # Make title a clickable link
                c2.markdown(f"[{title}]({url})")
                
                # 3. Date (mm/dd/yy)
                d_str = row.get("date", "")
                d_fmt = d_str
                try:
                    if d_str and d_str != "Duplicate":
                        d_fmt = pd.to_datetime(d_str).strftime("%m/%d/%y")
                except: pass
                c3.write(d_fmt)
                
                # 4. Fraud
                c4.write(row.get("fraud_indicator", ""))
                
                # 5. Status
                c5.write(row.get("status", ""))
                
                # 6. Summary
                c6.write(row.get("tl_dr", ""))
                
                st.markdown("<hr style='margin: 0.5em 0; opacity: 0.3;'>", unsafe_allow_html=True)


    else:
        # --- DETAILS VIEW ---
        # Scroll to top
        components.html(
            """
            <script>
                window.parent.document.querySelector('section.main').scrollTo(0, 0);
            </script>
            """,
            height=0
        )
        st.header("Article Details")
        
        if "selected_article" in st.session_state and st.session_state["selected_article"]:
            article = st.session_state["selected_article"]
            
            # Prepare navigation data
            current_view = st.session_state.get("current_view", [])
            total_count = len(current_view) if isinstance(current_view, list) else 0
            current_index = int(st.session_state.get("selected_index", 0))

            # Top Action Bar - Sticky
            with st.container():
                st.markdown('<span id="sticky-marker"></span>', unsafe_allow_html=True)
                # Updated columns to include Prev/Next
                col_back, col_prev, col_next, col_re_url, col_re_text, col_email, col_del, col_block = st.columns([0.8, 0.8, 0.8, 1.2, 1.2, 0.8, 0.8, 1.2])
                
                with col_back:
                    if st.button("🔙 Dashboard"):
                        try: st.query_params["article_id"] = ""
                        except: pass
                        st.session_state["is_details"] = False
                        st.rerun()

                with col_prev:
                    if total_count > 0:
                        if st.button("⬅️ Prev", disabled=current_index <= 0, key="btn_prev_top"):
                            new_index = max(0, current_index - 1)
                            st.session_state["selected_index"] = new_index
                            st.session_state["selected_article"] = current_view[new_index]
                            st.query_params["article_id"] = current_view[new_index]["id"]
                            st.rerun()

                with col_next:
                    if total_count > 0:
                        if st.button("Next ➡️", disabled=current_index >= total_count - 1, key="btn_next_top"):
                            new_index = min(total_count - 1, current_index + 1)
                            st.session_state["selected_index"] = new_index
                            st.session_state["selected_article"] = current_view[new_index]
                            st.query_params["article_id"] = current_view[new_index]["id"]
                            st.rerun()

                with col_block:
                    if st.button("🚫 Block Domain"):
                        u = article.get("url", "")
                        if u:
                            try:
                                d = urllib.parse.urlparse(u).netloc.lower()
                                if d:
                                    prefs = dm.get_preferences()
                                    blocked = prefs.get("blocked_domains", [])
                                    if d not in blocked:
                                        blocked.append(d)
                                        prefs["blocked_domains"] = blocked
                                        dm.save_preferences(prefs)
                                        st.success(f"Blocked: {d}")
                                        st.rerun()
                                    else:
                                        st.warning(f"{d} is already blocked.")
                            except:
                                st.error("Invalid URL")
                        else:
                            st.error("No URL to block")

                with col_re_url:
                    if st.button("Reanalyze (URL)"):
                         if not api_key:
                             st.error("No API Key")
                         elif not article.get("url"):
                             st.error("No URL")
                         else:
                             with st.spinner("Reanalyzing from URL..."):
                                 txt = scrape_article(article["url"])
                                 if txt.startswith("Error"):
                                     st.error(txt)
                                 else:
                                     res = analyze_with_chatgpt(txt, api_key)
                                     if res.startswith("Error"):
                                         st.error(res)
                                     else:
                                         try:
                                             d = json.loads(res)
                                             d = normalize_analysis(d)
                                             updates = {
                                                "article_title": d.get("article_title"),
                                                "date": d.get("date"),
                                                "fraud_indicator": d.get("fraud_indicator"),
                                                "tl_dr": d.get("tl_dr", d.get("summary")),
                                                "full_summary_bullets": d.get("full_summary_bullets"),
                                                "people_mentioned": d.get("people_mentioned"),
                                                "prevention_strategies": d.get("prevention_strategies", d.get("prevention")),
                                                "discovery_questions": d.get("discovery_questions"),
                                                "last_error": ""
                                             }
                                             dm.update_article(article["id"], updates)
                                             # Update session state object
                                             article.update(updates)
                                             st.success("Reanalyzed successfully!")
                                             st.rerun()
                                         except Exception as e:
                                             st.error(f"Save failed: {e}")

                with col_re_text:
                    # Replaced button with popover for text input
                    with st.popover("Reanalyze (Text)"):
                        new_text = st.text_area("Paste text here", height=300)
                        if st.button("Analyze Pasted Text", type="primary"):
                             if not api_key:
                                 st.error("No API Key")
                             elif not new_text:
                                 st.error("No text provided")
                             else:
                                 with st.spinner("Analyzing text..."):
                                     res = analyze_with_chatgpt(new_text, api_key)
                                     if res.startswith("Error"):
                                         st.error(res)
                                     else:
                                         try:
                                             d = json.loads(res)
                                             d = normalize_analysis(d)
                                             updates = {
                                                "article_title": d.get("article_title"),
                                                "date": d.get("date"),
                                                "fraud_indicator": d.get("fraud_indicator"),
                                                "tl_dr": d.get("tl_dr", d.get("summary")),
                                                "full_summary_bullets": d.get("full_summary_bullets"),
                                                "people_mentioned": d.get("people_mentioned"),
                                                "prevention_strategies": d.get("prevention_strategies", d.get("prevention")),
                                                "discovery_questions": d.get("discovery_questions"),
                                                "last_error": ""
                                             }
                                             dm.update_article(article["id"], updates)
                                             article.update(updates)
                                             st.success("Reanalyzed successfully!")
                                             st.rerun()
                                         except Exception as e:
                                             st.error(f"Save failed: {e}")
                
                with col_email:
                    with st.popover("✉️ Email"):
                        email_to = st.text_input("Recipient Email")
                        if st.button("Send Summary"):
                            if not email_to:
                                st.error("Please enter an email address.")
                            else:
                                subject = f"Summary: {article.get('article_title', 'Article Analysis')}"
                                # Build HTML body
                                bullets_html = ""
                                if article.get('full_summary_bullets'):
                                    bullets_html = "<ul>" + "".join([f"<li>{b}</li>" for b in article.get('full_summary_bullets')]) + "</ul>"
                                
                                body_html = f"""
                                <h2>{article.get('article_title', 'Unknown Title')}</h2>
                                <p><strong>Date:</strong> {article.get('date', 'Unknown')}</p>
                                <p><strong>Fraud Indicator:</strong> {article.get('fraud_indicator', 'N/A')}</p>
                                <hr>
                                <h3>TL;DR</h3>
                                <p>{article.get('tl_dr', '')}</p>
                                <h3>Full Summary</h3>
                                {bullets_html}
                                <hr>
                                <p><em>Sent via Tracklight.ai Article Analyzer</em></p>
                                """
                                
                                e_user = os.getenv("EMAIL_USER")
                                e_pass = os.getenv("EMAIL_PASS")
                                
                                if not e_user or not e_pass:
                                    st.error("Email credentials not configured in sidebar.")
                                else:
                                    with st.spinner("Sending email..."):
                                        em = EmailManager(e_user, e_pass)
                                        success, msg = em.send_email(email_to, subject, body_html)
                                        if success:
                                            st.success(msg)
                                        else:
                                            st.error(msg)

                with col_del:
                    if st.button("🗑️ Delete", type="primary"):
                        dm.delete_article(article["id"])
                        st.success("Article deleted.")
                        try: st.query_params["article_id"] = ""
                        except: pass
                        st.session_state["is_details"] = False
                        st.rerun()

                # Sticky Notes Field
                st.markdown("**Notes**")
                def update_notes():
                    # Callback to save notes
                    new_val = st.session_state.get(f"notes_{article['id']}")
                    if new_val is not None:
                        article["notes"] = new_val
                        dm.update_article(article["id"], {"notes": new_val})

                st.text_area(
                    "Notes", 
                    value=article.get("notes", ""), 
                    height=100, 
                    key=f"notes_{article['id']}", 
                    label_visibility="collapsed",
                    on_change=update_notes,
                    placeholder="Add notes here (auto-saved)..."
                )

                # --- Status & Priority ---
                st.markdown("#### Status & Priority")
                sp_col1, sp_col2 = st.columns(2)
                
                with sp_col1:
                    current_status = article.get("status", "Not Started")
                    status_opts = ["Not Started", "In Process", "Qualified", "Error", "Completed", "Archived"]
                    if current_status not in status_opts:
                        status_opts.append(current_status)
                    
                    new_status = st.selectbox(
                        "Status", 
                        options=status_opts, 
                        index=status_opts.index(current_status), 
                        key=f"status_sel_{article['id']}"
                    )

                with sp_col2:
                    current_prio = article.get("priority", "Medium")
                    prio_opts = ["High", "Medium", "Low"]
                    if current_prio not in prio_opts:
                        prio_opts.append(current_prio)

                    new_prio = st.selectbox(
                        "Priority", 
                        options=prio_opts, 
                        index=prio_opts.index(current_prio), 
                        key=f"prio_sel_{article['id']}"
                    )

                if st.button("Update Status & Priority", key=f"btn_update_sp_{article['id']}"):
                    article["status"] = new_status
                    article["priority"] = new_prio
                    dm.update_article(article["id"], {"status": new_status, "priority": new_prio})
                    st.success(f"Updated: {new_status} / {new_prio}")
                    st.rerun()

                
                # Removed old text area block since it's now in popover
                # if st.session_state.get("show_reanalyze_text"): ...

            st.markdown("---")
            
            st.subheader(f"📄 {article.get('article_title', 'Unknown')}")
            st.caption(f"ID: {article.get('id')}")
            
            # Navigation between articles (Moved to top sticky bar)
            if total_count > 0:
                st.caption(f"Article {current_index + 1} of {total_count}")
            
            st.markdown(f"**Date:** {article.get('date', 'Unknown')}")
            if article.get('date_verification'):
                st.caption(f"📅 Verification: {article.get('date_verification')}")
            st.markdown(f"**URL:** {article.get('url', 'N/A')}")
            
            st.markdown("### A. TL;DR")
            st.write(article.get("tl_dr", article.get("summary", "No summary.")))
            
            st.markdown("### B. Full Summary")
            bullets = article.get("full_summary_bullets", [])
            if bullets and isinstance(bullets, list):
                for b in bullets:
                    st.write(f"• {b}")
            else:
                st.write("No full summary available.")

            st.markdown("### C. History")
            history = article.get("history_overview", [])
            if isinstance(history, list) and history:
                for h in history:
                    st.write(f"• {h}")
            elif isinstance(history, str) and history:
                st.write(history)
            else:
                st.write("No history overview available.")

            st.markdown("### D. Allegations")
            allegations = article.get("allegations", [])
            if isinstance(allegations, list) and allegations:
                for a in allegations:
                    st.write(f"• {a}")
            elif isinstance(allegations, str) and allegations:
                st.write(allegations)
            else:
                st.write("No allegations listed.")

            st.markdown("### E. Current Situation")
            current = article.get("current_situation", [])
            if isinstance(current, list) and current:
                for c in current:
                    st.write(f"• {c}")
            elif isinstance(current, str) and current:
                st.write(current)
            else:
                st.write("No current situation details.")

            st.markdown("### F. Next Steps")
            next_steps = article.get("next_steps", [])
            if isinstance(next_steps, list) and next_steps:
                for ns in next_steps:
                    st.write(f"• {ns}")
            elif isinstance(next_steps, str) and next_steps:
                st.write(next_steps)
            else:
                st.write("No next steps provided.")

            st.markdown("### G. Tracklight.ai Prevention")
            strategies = article.get("prevention_strategies", article.get("prevention", []))
            if isinstance(strategies, list):
                for s in strategies:
                    if isinstance(s, dict):
                        st.markdown(f"**Issue:** {s.get('issue', '')}")
                        st.info(f"🛡️ {s.get('prevention', '')}")
                    else:
                        st.info(s)
            elif isinstance(strategies, str):
                 st.info(strategies)
            else:
                st.write("No prevention info.")

            st.markdown("### H. Fraud Indicator")
            indicator = article.get("fraud_indicator", "Unknown")
            if indicator == "High":
                st.error(f"🚨 {indicator}")
            elif indicator == "Medium":
                st.warning(f"⚠️ {indicator}")
            else:
                st.success(f"✅ {indicator}")
            
            st.markdown("### I. People Mentioned")
            people = article.get("people_mentioned", [])
            tl = article.get("tl_dr", "")
            bullets_text = ""
            if isinstance(bullets, list):
                bullets_text = "\n".join([str(x) for x in bullets])[:2000]
            if isinstance(people, list) and people:
                for person in people:
                    st.markdown(f"**{person}**")
                    ov = get_person_overview(person, tl, bullets_text, api_key)
                    if ov:
                        st.markdown(f"<div style='margin-left: 1em;'>{ov}</div>", unsafe_allow_html=True)
                        q = quote_plus(f"Analyze {person}'s role in {article.get('article_title', '')}")
                        st.markdown(f"[Deeper Analysis of {person}](https://chat.openai.com/?q={q})")
            else:
                st.write(str(people))
            
            st.markdown("---")
            st.markdown("### J. Discovery Questions")
            questions = article.get("discovery_questions", [])
            if isinstance(questions, list):
                for q in questions:
                    st.write(f"❓ {q}")
            else:
                st.write(str(questions))
                
        else:
            st.info("👈 Please select an article from the Dashboard to view details here.")

    st.markdown("---")
    if st.button("📜 Review Logs", key="dashboard_logs_btn"):
        st.session_state["show_logs"] = True
        st.rerun()

if __name__ == "__main__":
    main()
