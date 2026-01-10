# Tracklight.ai Article Analyzer

This application is a comprehensive tool for analyzing news articles to identify fraud indicators, extract key insights, and group trends using OpenAI's GPT-4o. It is designed to assist Tracklight.ai in monitoring fraud cases and generating prevention strategies.

## Features

### 🔍 Analysis & Scraping
- **URL & Text Analysis**: Scrape content from URLs or paste text directly for AI analysis.
- **Deep Insights**: Automatically extracts:
  - Fraud Indicators (High, Medium, Low)
  - Key People & Roles
  - Prevention Strategies (Tailored to Tracklight.ai)
  - Discovery Questions for Sales
  - Comprehensive Summaries (TL;DR + Bullet Points)

### 📊 Dashboard
- **Interactive Table**: Sort, filter, and select articles.
- **Bulk Actions**: Delete or reanalyze multiple articles at once.
- **Excel Export**: Download your article database to `.xlsx`.
- **Custom View**: Selectable rows with direct links to detailed analysis.

### 🌎 Global Analysis
- **Trend Grouping**: AI automatically groups articles by common themes (e.g., "Medicare Fraud", "PPP Loans").
- **Expandable Reports**: View article counts and drill down into specific groups.

### 🛠 Integrations
- **Gmail**: Automatically fetch and process new article links from your inbox.
- **Google Sheets**: Bulk sync URLs from a Google Sheet.
- **Email Sharing**: Send formatted article summaries directly from the app.

### 📝 Data Management
- **Sticky Notes**: Add persistent notes to any article.
- **Edit & Reanalyze**: Update analysis with new prompts or text.
- **Local Database**: All data is saved locally in `articles_data.json`.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Run the App**:
    ```bash
    streamlit run app.py
    ```

3.  **Configuration**:
    - **OpenAI API Key**: Required for analysis (enter in sidebar).
    - **Email/Google**: Optional configuration in sidebar for integrations.

## Requirements

- Python 3.8+
- OpenAI API Key
